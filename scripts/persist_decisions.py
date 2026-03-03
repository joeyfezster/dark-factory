#!/usr/bin/env python3
"""Persist PR review pack decisions to the cumulative decision log.

Extracts decisions from ReviewPackData JSON (or from rendered HTML as fallback)
and appends them to docs/decisions/decision_log.json.

Usage:
    python packages/dark-factory/scripts/persist_decisions.py --pr 6
    python packages/dark-factory/scripts/persist_decisions.py --pr 6 --data /tmp/pr6_data.json
    python packages/dark-factory/scripts/persist_decisions.py --pr 6 --dry-run
"""

from __future__ import annotations

import argparse
import json
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
DEFAULT_LOG = REPO_ROOT / "docs" / "decisions" / "decision_log.json"
REPO_SLUG = "joeyfezster/building_ai_w_ai"


def load_decision_log(path: Path) -> dict:
    """Load the existing decision log, or create the initial structure."""
    if path.exists():
        return json.loads(path.read_text())
    return {"version": 1, "decisions": []}


def next_global_seq(log: dict) -> int:
    """Compute the next globalSeq from the existing log."""
    if not log["decisions"]:
        return 1
    return max(d["globalSeq"] for d in log["decisions"]) + 1


def existing_ids(log: dict) -> set[str]:
    """Return the set of decision IDs already in the log."""
    return {d["id"] for d in log["decisions"]}


def extract_decisions_from_json(path: Path) -> tuple[list[dict], dict]:
    """Extract decisions and header from a ReviewPackData JSON file."""
    data = json.loads(path.read_text())
    return data.get("decisions", []), data.get("header", {})


def extract_decisions_from_html(path: Path) -> tuple[list[dict], dict]:
    """Extract decisions from rendered HTML by parsing the embedded DATA object.

    The review pack HTML contains a `const DATA = {"header":...};` block with
    the full ReviewPackData JSON. We anchor the regex to `"header"` to avoid
    matching `const DATA` references inside embedded diff data (which may
    contain source code of this very script).
    """
    html = path.read_text()
    # Anchor to the actual DATA object (first key is always "header").
    # The opening brace and "header" may be on separate lines.
    match = re.search(
        r'const DATA = (\{\s*"header".*?);\s*\n', html, re.DOTALL
    )
    if not match:
        print("ERROR: Could not find 'const DATA = {\"header\"...}' in HTML file.")
        sys.exit(1)

    # The embedded JSON may contain <\/script> escapes from the renderer.
    raw = match.group(1).replace(r"<\/script", "</script")

    data = json.loads(raw)
    return data.get("decisions", []), data.get("header", {})


def get_merge_timestamp(pr_number: int) -> str:
    """Get the merge timestamp for a PR via gh CLI.

    Fails explicitly if the timestamp cannot be determined — never
    fabricates a timestamp, as that would corrupt the decision log's
    chronology and auditability.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "mergedAt", "-q", ".mergedAt"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    # PR may not be merged yet — use HEAD commit timestamp as deterministic fallback
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%aI"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    print("ERROR: Could not determine merge timestamp from gh or git log.")
    print("  Ensure 'gh' is authenticated or run from within the git repo.")
    sys.exit(1)


def get_pr_url(pr_number: int) -> str:
    """Build the PR URL."""
    return f"https://github.com/{REPO_SLUG}/pull/{pr_number}"


def build_persisted_decision(
    raw: dict,
    pr_number: int,
    global_seq: int,
    merged_at: str,
    head_sha: str,
) -> dict:
    """Convert a raw Decision (from review pack) to the persisted format."""
    local_seq = raw["number"]
    zones_raw = raw.get("zones", "")
    zones = zones_raw.split() if isinstance(zones_raw, str) else zones_raw

    return {
        "id": f"PR{pr_number}-{local_seq}",
        "globalSeq": global_seq,
        "prNumber": pr_number,
        "title": raw["title"],
        "rationale": raw.get("rationale", ""),
        "body": raw.get("body", ""),
        "zones": zones,
        "files": raw.get("files", []),
        "verified": raw.get("verified", False),
        "mergedAt": merged_at,
        "prUrl": get_pr_url(pr_number),
        "headSha": head_sha,
        "status": "active",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Persist PR review pack decisions to the decision log"
    )
    parser.add_argument(
        "--pr", type=int, required=True, help="PR number whose decisions to persist"
    )
    parser.add_argument(
        "--data",
        type=str,
        default=None,
        help="Path to ReviewPackData JSON file (falls back to rendered HTML)",
    )
    parser.add_argument(
        "--log",
        type=str,
        default=None,
        help=f"Path to decision log (default: {DEFAULT_LOG})",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without modifying the log"
    )
    args = parser.parse_args()

    log_path = Path(args.log) if args.log else DEFAULT_LOG

    # --- Load decisions from source ---
    decisions: list[dict]
    header: dict

    if args.data:
        data_path = Path(args.data)
        if not data_path.exists():
            print(f"ERROR: Data file not found: {data_path}")
            return 1
        decisions, header = extract_decisions_from_json(data_path)
    else:
        auto_json_path = Path(f"/tmp/pr{args.pr}_review_pack_data.json")
        if auto_json_path.exists():
            print(f"No --data provided, auto-detected JSON: {auto_json_path}")
            decisions, header = extract_decisions_from_json(auto_json_path)
        else:
            html_path = REPO_ROOT / "docs" / f"pr{args.pr}_review_pack.html"
            if not html_path.exists():
                print(
                    "ERROR: No data file provided and defaults not found: "
                    f"{auto_json_path} or {html_path}"
                )
                print(f"  Provide --data /path/to/pr{args.pr}_review_pack_data.json")
                return 1
            print(f"No --data provided, extracting from HTML fallback: {html_path}")
            decisions, header = extract_decisions_from_html(html_path)

    if not decisions:
        print(f"No decisions found for PR #{args.pr}. Nothing to persist.")
        return 0

    # --- Load existing log ---
    log = load_decision_log(log_path)
    known = existing_ids(log)
    seq = next_global_seq(log)

    # --- Get metadata ---
    merged_at = get_merge_timestamp(args.pr)
    head_sha = header.get("headSha", "unknown")

    # --- Build and append ---
    added = 0
    skipped = 0
    for raw in decisions:
        local_seq = raw["number"]
        decision_id = f"PR{args.pr}-{local_seq}"

        if decision_id in known:
            print(f"  SKIP: {decision_id} already exists in log")
            skipped += 1
            continue

        persisted = build_persisted_decision(raw, args.pr, seq, merged_at, head_sha)

        if args.dry_run:
            print(f"\n  WOULD ADD: {decision_id} (globalSeq={seq})")
            print(f"    Title: {persisted['title']}")
            print(f"    Zones: {persisted['zones']}")
            print(f"    Files: {len(persisted['files'])}")
        else:
            log["decisions"].append(persisted)
            print(f"  ADD: {decision_id} (globalSeq={seq}) — {persisted['title']}")

        seq += 1
        added += 1

    # --- Write ---
    if not args.dry_run and added > 0:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n")
        print(f"\nPersisted {added} decisions from PR #{args.pr} to {log_path}")
    elif args.dry_run:
        print(f"\nDry run: {added} decisions would be added, {skipped} skipped")
    else:
        print(f"\nNo new decisions to add ({skipped} already in log)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
