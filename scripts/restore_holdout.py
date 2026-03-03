#!/usr/bin/env python3
"""Restore holdout scenarios from a known git ref.

Symmetric counterpart to strip_holdout.py. Restores /scenarios/
and Makefile targets from a clean source (default: origin/main).

This script does NOT commit — the caller decides when to commit.

Usage:
    python packages/dark-factory/scripts/restore_holdout.py
    python packages/dark-factory/scripts/restore_holdout.py --ref origin/main
    python packages/dark-factory/scripts/restore_holdout.py --dry-run
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def _get_repo_root() -> Path:
    """Walk up from this file to find the git repo root."""
    current = Path(__file__).resolve().parent
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    raise RuntimeError("Repo root not found -- no .git directory in any parent")


REPO_ROOT = _get_repo_root()

# NOTE: strip_holdout.py also removes review pack artifacts (docs/pr_review_pack.html,
# docs/pr_diff_data.json). These are NOT restored — they are generated artifacts that
# will be regenerated when a new PR review pack is produced.

STRIP_MARKER = "[factory:holdout-stripped]"


def restore_scenarios(
    repo_root: Path, ref: str, dry_run: bool = False
) -> list[str]:
    """Restore /scenarios/ from a git ref.

    Uses `git checkout <ref> -- scenarios/` to restore files
    from the specified ref without changing the current branch.

    Returns list of restored paths.
    """
    # Check if scenarios exist at the ref
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", ref, "scenarios/"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"ERROR: Could not list scenarios at ref '{ref}'")
        print(f"  stderr: {result.stderr.strip()}")
        return []

    files = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
    if not files:
        print(f"WARNING: No scenario files found at ref '{ref}'")
        return []

    if dry_run:
        print(f"Would restore {len(files)} files from '{ref}':")
        for f in files:
            print(f"  + {f}")
        return files

    # Restore from ref
    subprocess.run(
        ["git", "checkout", ref, "--", "scenarios/"],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
    )

    # Unstage the restored files (leave them as working tree changes)
    subprocess.run(
        ["git", "reset", "HEAD", "scenarios/"],
        cwd=str(repo_root),
        capture_output=True,
    )

    return files


def restore_makefile_targets(
    repo_root: Path, ref: str, dry_run: bool = False
) -> list[str]:
    """Restore Makefile targets that were commented out by strip_holdout.py.

    Instead of trying to uncomment, we restore the entire Makefile from
    the ref and keep all non-scenario changes from the current version.

    Simpler approach: just uncomment the marked blocks.

    Returns list of restored target names.
    """
    makefile = repo_root / "Makefile"
    if not makefile.exists():
        return []

    content = makefile.read_text()
    restored_targets: list[str] = []

    # Find and uncomment all marked blocks
    escaped_marker = re.escape(STRIP_MARKER)
    pattern = (
        rf"# {escaped_marker}\s*— stripped by strip_holdout\.py\n"
        rf"((?:# .*\n)*)"
        rf"# end {escaped_marker}"
    )

    for match in re.finditer(pattern, content):
        commented_block = match.group(1)
        # Uncomment: remove leading "# " from each line
        uncommented_lines = []
        for line in commented_block.splitlines():
            if line.startswith("# "):
                uncommented_lines.append(line[2:])
            elif line == "#":
                uncommented_lines.append("")
            else:
                uncommented_lines.append(line)
        uncommented = "\n".join(uncommented_lines)

        # Extract target name for logging
        target_match = re.match(r"(\w[\w-]*):", uncommented)
        if target_match:
            restored_targets.append(target_match.group(1))

        if not dry_run:
            content = content.replace(match.group(0), uncommented)

    if restored_targets and not dry_run:
        makefile.write_text(content)

    return restored_targets


def verify_restored(repo_root: Path, expected_count: int) -> list[str]:
    """Verify that restoration was complete.

    Returns list of verification failures (empty = success).
    """
    failures: list[str] = []

    scenarios_dir = repo_root / "scenarios"
    if not scenarios_dir.exists():
        failures.append("scenarios/ directory does not exist")
        return failures

    actual = list(scenarios_dir.rglob("*.md"))
    if len(actual) == 0:
        failures.append("scenarios/ exists but has no .md files")
    elif expected_count > 0 and len(actual) != expected_count:
        failures.append(
            f"Expected {expected_count} scenario files, "
            f"found {len(actual)}"
        )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restore holdout scenarios from a git ref"
    )
    parser.add_argument(
        "--ref",
        type=str,
        default="origin/main",
        help="Git ref to restore scenarios from (default: origin/main)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be restored without making changes",
    )
    args = parser.parse_args()

    repo_root = REPO_ROOT

    # Fetch latest from remote (so origin/main is current)
    print("Fetching latest from remote...")
    subprocess.run(
        ["git", "fetch", "origin"],
        cwd=str(repo_root),
        capture_output=True,
    )

    print(
        f"{'DRY RUN — ' if args.dry_run else ''}"
        f"Restoring holdout scenarios from '{args.ref}'..."
    )

    # Step 1: Restore scenario files
    restored = restore_scenarios(
        repo_root, args.ref, dry_run=args.dry_run
    )
    if restored:
        print(f"  Restored {len(restored)} scenario files")
    else:
        print("  No scenario files restored")
        return 1

    # Step 2: Restore Makefile targets
    targets = restore_makefile_targets(
        repo_root, args.ref, dry_run=args.dry_run
    )
    if targets:
        print(f"  Restored Makefile targets: {', '.join(targets)}")
    else:
        print("  No Makefile targets needed restoration")

    if args.dry_run:
        print("\nDry run complete — no changes made")
        return 0

    # Step 3: Verify
    failures = verify_restored(repo_root, expected_count=len(restored))
    if failures:
        print("\nVERIFICATION FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nVerification passed — scenarios fully restored")
    print("NOTE: Changes are in working tree, NOT committed.")
    print("  Stage and commit when ready: git add scenarios/ Makefile")

    return 0


if __name__ == "__main__":
    sys.exit(main())
