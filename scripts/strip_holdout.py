#!/usr/bin/env python3
"""Strip holdout scenarios from the working tree.

Deterministic script that removes /scenarios/ and scenario-related
Makefile targets before providing a branch to the attractor (Codex).

This is a factory gate — non-circumventable. The attractor literally
cannot see evaluation criteria because they don't exist on its branch.

Usage:
    python packages/dark-factory/scripts/strip_holdout.py              # strip and commit
    python packages/dark-factory/scripts/strip_holdout.py --dry-run    # show what would be removed
    python packages/dark-factory/scripts/strip_holdout.py --no-commit  # strip but don't commit
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _get_repo_root() -> Path:
    """Walk up from this file to find the git repo root."""
    current = Path(__file__).resolve().parent
    for parent in [current, *current.parents]:
        if (parent / ".git").is_dir():
            return parent
    raise RuntimeError("Repo root not found -- no .git directory in any parent")


REPO_ROOT = _get_repo_root()

MARKER = "[factory:holdout-stripped]"

# Makefile targets that reference scenarios (removed during stripping)
SCENARIO_TARGETS = [
    "run-scenarios",
    "compile-feedback",
]

# Review pack artifacts (stripped to prevent attractor from seeing review findings)
REVIEW_PACK_PATTERNS = [
    "docs/pr*_review_pack.html",
    "docs/pr*_diff_data.json",
    "docs/pr*_review_pack.approval.json",
]


def strip_scenarios(repo_root: Path, dry_run: bool = False) -> list[str]:
    """Remove /scenarios/ directory entirely.

    Returns list of removed paths for logging.
    """
    scenarios_dir = repo_root / "scenarios"
    removed: list[str] = []

    if scenarios_dir.exists():
        for f in sorted(scenarios_dir.rglob("*")):
            if f.is_file():
                removed.append(str(f.relative_to(repo_root)))
        if not dry_run:
            shutil.rmtree(scenarios_dir)
    else:
        print("WARNING: /scenarios/ directory not found — already stripped?")

    return removed


def strip_review_pack(repo_root: Path, dry_run: bool = False) -> list[str]:
    """Remove review pack artifacts that contain adversarial review findings.

    The attractor should not see review pack data — it contains
    information about the review process, adversarial findings, and
    detailed analysis that could influence gaming strategies.

    Returns list of removed file paths.
    """
    removed: list[str] = []
    for pattern in REVIEW_PACK_PATTERNS:
        for full_path in repo_root.glob(pattern):
            rel_path = str(full_path.relative_to(repo_root))
            removed.append(rel_path)
            if not dry_run:
                full_path.unlink()
    return removed


def strip_makefile_targets(
    repo_root: Path, dry_run: bool = False
) -> list[str]:
    """Comment out scenario-related Makefile targets.

    Instead of deleting lines (which makes restoration harder),
    we wrap them in a clearly-marked block comment.

    Returns list of target names that were commented out.
    """
    makefile = repo_root / "Makefile"
    if not makefile.exists():
        return []

    content = makefile.read_text()
    commented_targets: list[str] = []

    for target in SCENARIO_TARGETS:
        # Match the target and its recipe (indented lines following it)
        pattern = rf"^({target}:.*(?:\n\t.*)*)"
        match = re.search(pattern, content, re.MULTILINE)
        if match:
            original = match.group(1)
            replacement = (
                f"# {MARKER} — stripped by strip_holdout.py\n"
                + "\n".join(f"# {line}" for line in original.splitlines())
                + f"\n# end {MARKER}"
            )
            if not dry_run:
                content = content.replace(original, replacement)
            commented_targets.append(target)

    if commented_targets and not dry_run:
        makefile.write_text(content)

    return commented_targets


def verify_stripped(repo_root: Path) -> list[str]:
    """Verify that stripping was complete.

    Returns list of verification failures (empty = success).
    """
    failures: list[str] = []

    scenarios_dir = repo_root / "scenarios"
    if scenarios_dir.exists():
        remaining = [f for f in scenarios_dir.rglob("*") if f.is_file()]
        if remaining:
            failures.append(
                f"scenarios/ still has {len(remaining)} files: "
                + ", ".join(f.name for f in remaining[:5])
            )
        elif scenarios_dir.is_dir():
            # Directory exists but is empty — still a signal to Codex
            failures.append(
                "scenarios/ directory still exists (should be fully removed)"
            )

    # Verify review pack artifacts are removed
    for pattern in REVIEW_PACK_PATTERNS:
        for full_path in repo_root.glob(pattern):
            rel_path = str(full_path.relative_to(repo_root))
            failures.append(f"Review pack artifact still exists: {rel_path}")

    return failures


def git_commit_strip(repo_root: Path) -> bool:
    """Commit the stripping as a marker commit.

    Returns True if commit was created, False if nothing to commit.
    """
    subprocess.run(
        ["git", "add", "-A"],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
    )

    # Check if there's anything to commit
    result = subprocess.run(
        ["git", "diff", "--staged", "--quiet"],
        cwd=str(repo_root),
        capture_output=True,
    )
    if result.returncode == 0:
        print("Nothing to commit — working tree already clean")
        return False

    subprocess.run(
        [
            "git",
            "commit",
            "-m",
            f"{MARKER} Strip holdout scenarios before attractor\n\n"
            "Deterministic removal of /scenarios/ and related Makefile targets.\n"
            "Attractor (Codex) cannot see evaluation criteria on this branch.\n"
            "Restore with: python packages/dark-factory/scripts/restore_holdout.py",
        ],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Strip holdout scenarios from working tree"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without making changes",
    )
    parser.add_argument(
        "--no-commit",
        action="store_true",
        help="Strip but don't create a git commit",
    )
    args = parser.parse_args()

    repo_root = REPO_ROOT

    print(f"{'DRY RUN — ' if args.dry_run else ''}Stripping holdout scenarios...")

    # Step 1: Remove scenarios
    removed = strip_scenarios(repo_root, dry_run=args.dry_run)
    if removed:
        print(f"  Removed {len(removed)} scenario files")
        for f in removed:
            print(f"    - {f}")
    else:
        print("  No scenario files found")

    # Step 1b: Remove review pack artifacts
    review_removed = strip_review_pack(repo_root, dry_run=args.dry_run)
    if review_removed:
        print(f"  Removed review pack artifacts: {', '.join(review_removed)}")

    # Step 2: Comment out Makefile targets
    commented = strip_makefile_targets(repo_root, dry_run=args.dry_run)
    if commented:
        print(f"  Commented out Makefile targets: {', '.join(commented)}")

    if args.dry_run:
        print("\nDry run complete — no changes made")
        return 0

    # Step 3: Verify
    failures = verify_stripped(repo_root)
    if failures:
        print("\nVERIFICATION FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nVerification passed — scenarios fully stripped")

    # Step 4: Commit (optional)
    if not args.no_commit:
        if git_commit_strip(repo_root):
            print(f"Committed with marker: {MARKER}")
        else:
            print("No commit needed")

    return 0


if __name__ == "__main__":
    sys.exit(main())
