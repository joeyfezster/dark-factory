#!/usr/bin/env python3
"""Gate 0 Tier 1 — Deterministic tool runner.

Runs all 5 tool checks in parallel, aggregates results into a single
JSON artifact. This is the deterministic tier of Gate 0 — fast, cheap,
binary pass/fail. Tier 2 (LLM semantic agents) builds on this output.

Checks:
    1. code_quality  — ruff extended rules (C90, S, SIM, RET, PTH, ERA)
    2. complexity    — radon cyclomatic complexity
    3. dead_code     — vulture unused code detection
    4. security      — bandit vulnerability patterns
    5. test_quality  — AST-based vacuous test / gaming detection

Usage:
    python scripts/run_gate0.py
    python scripts/run_gate0.py --json
    python scripts/run_gate0.py --output path.json

Exit codes:
    0 — no CRITICAL findings
    1 — at least one CRITICAL finding (Gate 0 tier 1 fails)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


def _get_repo_root() -> Path:
    """Walk up from this file to find the git repo root."""
    current = Path(__file__).resolve().parent
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    raise RuntimeError("Repo root not found -- no .git directory in any parent")


REPO_ROOT = _get_repo_root()
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "factory" / "gate0_results.json"
SCRIPT_DIR = Path(__file__).resolve().parent

# Each check: (name, command, description)
CHECKS: list[tuple[str, list[str], str]] = [
    (
        "code_quality",
        [
            sys.executable,
            str(SCRIPT_DIR / "nfr_checks.py"),
            "--check",
            "code_quality",
            "--json",
        ],
        "Extended ruff rules (complexity, security, simplification)",
    ),
    (
        "complexity",
        [
            sys.executable,
            str(SCRIPT_DIR / "nfr_checks.py"),
            "--check",
            "complexity",
            "--json",
        ],
        "Radon cyclomatic complexity (grade C+ flagged)",
    ),
    (
        "dead_code",
        [
            sys.executable,
            str(SCRIPT_DIR / "nfr_checks.py"),
            "--check",
            "dead_code",
            "--json",
        ],
        "Vulture unused code detection (80%+ confidence)",
    ),
    (
        "security",
        [
            sys.executable,
            str(SCRIPT_DIR / "nfr_checks.py"),
            "--check",
            "security",
            "--json",
        ],
        "Bandit security vulnerability patterns",
    ),
    (
        "test_quality",
        [sys.executable, str(SCRIPT_DIR / "check_test_quality.py"), "--json"],
        "AST-based vacuous test and gaming pattern detection",
    ),
]


def _run_check(
    name: str, cmd: list[str], description: str
) -> dict:
    """Run a single check subprocess, return parsed result."""
    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        elapsed = time.monotonic() - start

        # Parse JSON output
        stdout = result.stdout.strip()
        if not stdout:
            return {
                "name": name,
                "tool": description,
                "status": "passed",
                "summary": "Clean",
                "findings": [],
                "elapsed_s": round(elapsed, 2),
            }

        parsed = json.loads(stdout)

        # nfr_checks.py returns a list of NFRResult dicts
        # check_test_quality.py returns a single dict
        if isinstance(parsed, list):
            # Take the first (and only) result from nfr_checks.py --check X
            if parsed:
                check_result = parsed[0]
            else:
                check_result = {
                    "name": name,
                    "status": "passed",
                    "summary": "Clean",
                    "findings": [],
                }
        else:
            check_result = parsed

        check_result["elapsed_s"] = round(elapsed, 2)
        return check_result

    except subprocess.TimeoutExpired:
        return {
            "name": name,
            "tool": description,
            "status": "error",
            "summary": "Timeout after 120s",
            "findings": [
                {
                    "severity": "WARNING",
                    "message": f"Check '{name}' timed out after 120s",
                }
            ],
            "elapsed_s": 120.0,
        }
    except json.JSONDecodeError as e:
        return {
            "name": name,
            "tool": description,
            "status": "error",
            "summary": f"JSON parse error: {e}",
            "findings": [
                {
                    "severity": "WARNING",
                    "message": f"Could not parse JSON output: {e}",
                }
            ],
            "elapsed_s": round(time.monotonic() - start, 2),
        }
    except Exception as e:
        return {
            "name": name,
            "tool": description,
            "status": "error",
            "summary": f"Error: {e}",
            "findings": [
                {
                    "severity": "WARNING",
                    "message": f"Check failed: {e}",
                }
            ],
            "elapsed_s": round(time.monotonic() - start, 2),
        }


def run_all() -> dict:
    """Run all checks in parallel, return aggregated result."""
    start = time.monotonic()
    checks: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=len(CHECKS)) as pool:
        futures = {
            pool.submit(_run_check, name, cmd, desc): name
            for name, cmd, desc in CHECKS
        }
        for future in as_completed(futures):
            name = futures[future]
            checks[name] = future.result()

    total_elapsed = time.monotonic() - start

    # Aggregate
    all_findings = []
    for check in checks.values():
        all_findings.extend(check.get("findings", []))

    critical_count = sum(
        1
        for f in all_findings
        if f.get("severity") == "CRITICAL"
    )
    warning_count = sum(
        1
        for f in all_findings
        if f.get("severity") == "WARNING"
    )
    passed_count = sum(
        1 for c in checks.values() if c.get("status") == "passed"
    )
    failed_count = sum(
        1 for c in checks.values() if c.get("status") == "failed"
    )

    return {
        "timestamp": datetime.now().astimezone().isoformat(),
        "tier": "deterministic",
        "total_elapsed_s": round(total_elapsed, 2),
        "checks": checks,
        "summary": {
            "total_checks": len(CHECKS),
            "passed": passed_count,
            "failed": failed_count,
            "error": len(CHECKS) - passed_count - failed_count,
            "critical_findings": critical_count,
            "warning_findings": warning_count,
            "has_critical": critical_count > 0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gate 0 Tier 1 — run all deterministic tool checks in parallel"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON to stdout",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help=f"Write JSON to file (default when not --json: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    results = run_all()

    # Always write to file unless --json (stdout-only) is used
    output_path = args.output or (None if args.json else str(DEFAULT_OUTPUT))
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(json.dumps(results, indent=2) + "\n")

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        # Human-readable summary
        summary = results["summary"]
        print("=" * 60)
        print("GATE 0 TIER 1: Deterministic Tool Checks")
        print(f"  Elapsed: {results['total_elapsed_s']}s (parallel)")
        print("=" * 60)

        for name, check in results["checks"].items():
            status = check.get("status", "?")
            icon = {"passed": "PASS", "failed": "FAIL", "error": "ERR ", "skipped": "SKIP"}.get(
                status, "????"
            )
            elapsed = check.get("elapsed_s", "?")
            print(f"\n[{icon}] {name} ({elapsed}s): {check.get('summary', '')}")

            for f in check.get("findings", []):
                sev = f.get("severity", "?")
                if sev == "INFO":
                    continue
                loc = ""
                if f.get("file"):
                    loc = f" {f['file']}"
                    if f.get("line"):
                        loc += f":{f['line']}"
                msg = f.get("message") or f.get("detail", "")
                print(f"  [{sev}]{loc} {msg}")

        print(f"\n{'=' * 60}")
        print(
            f"Checks: {summary['passed']} passed, "
            f"{summary['failed']} failed, "
            f"{summary['error']} error"
        )
        print(
            f"Findings: {summary['critical_findings']} critical, "
            f"{summary['warning_findings']} warnings"
        )

        if summary["has_critical"]:
            print("\nGATE 0 TIER 1: FAILED (critical findings)")
        else:
            print("\nGATE 0 TIER 1: PASSED")

        if output_path:
            print(f"\nResults written to {output_path}")

    return 1 if results["summary"]["has_critical"] else 0


if __name__ == "__main__":
    sys.exit(main())
