#!/usr/bin/env python3
"""Scenario runner for the dark factory holdout evaluation.

Reads structured markdown scenarios from /scenarios/, executes their
evaluation methods, and produces a JSON report at
artifacts/factory/scenario_results.json.

Usage:
    python packages/dark-factory/scripts/run_scenarios.py
    python packages/dark-factory/scripts/run_scenarios.py --category environment
    python packages/dark-factory/scripts/run_scenarios.py --timeout 120
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _get_repo_root() -> Path:
    """Walk up from this file to find the git repo root."""
    current = Path(__file__).resolve().parent
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    raise RuntimeError("Repo root not found -- no .git directory in any parent")


REPO_ROOT = _get_repo_root()


@dataclass
class Scenario:
    """A parsed scenario from a markdown file."""

    name: str
    file_path: str
    category: str
    preconditions: list[str]
    behavioral_expectation: str
    evaluation_method: str
    pass_criteria: str
    evidence_required: list[str]


@dataclass
class ScenarioResult:
    """Result of running a single scenario."""

    name: str
    file_path: str
    category: str
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    error_summary: str = ""


@dataclass
class ScenarioReport:
    """Full report from running all scenarios."""

    timestamp: str
    total: int
    passed: int
    failed: int
    skipped: int
    satisfaction_score: float
    results: list[dict[str, object]] = field(default_factory=list)


def parse_scenario(path: Path) -> Scenario:
    """Parse a structured markdown scenario file."""
    content = path.read_text()

    def extract_section(heading: str) -> str:
        pattern = rf"## {heading}\s*\n(.*?)(?=\n## |\Z)"
        match = re.search(pattern, content, re.DOTALL)
        return match.group(1).strip() if match else ""

    # Extract name from H1
    name_match = re.search(r"# Scenario:\s*(.+)", content)
    name = name_match.group(1).strip() if name_match else path.stem

    category = extract_section("Category").strip().lower()

    preconditions_raw = extract_section("Preconditions")
    preconditions = [
        line.lstrip("- ").strip()
        for line in preconditions_raw.splitlines()
        if line.strip().startswith("-")
    ]

    behavioral_expectation = extract_section("Behavioral Expectation")

    eval_raw = extract_section("Evaluation Method")
    # Extract code block content
    code_match = re.search(r"```(?:bash|sh)?\s*\n(.*?)```", eval_raw, re.DOTALL)
    evaluation_method = code_match.group(1).strip() if code_match else eval_raw

    pass_criteria = extract_section("Pass Criteria")

    evidence_raw = extract_section("Evidence Required")
    evidence_required = [
        line.lstrip("- ").strip()
        for line in evidence_raw.splitlines()
        if line.strip().startswith("-")
    ]

    return Scenario(
        name=name,
        file_path=str(path),
        category=category,
        preconditions=preconditions,
        behavioral_expectation=behavioral_expectation,
        evaluation_method=evaluation_method,
        pass_criteria=pass_criteria,
        evidence_required=evidence_required,
    )


def run_scenario(scenario: Scenario, timeout: int, repo_root: Path) -> ScenarioResult:
    """Execute a single scenario's evaluation method."""
    start = time.time()

    # Guard: empty evaluation commands must not pass silently
    if not scenario.evaluation_method.strip():
        return ScenarioResult(
            name=scenario.name,
            file_path=scenario.file_path,
            category=scenario.category,
            passed=False,
            exit_code=-3,
            stdout="",
            stderr="EMPTY: evaluation_method is empty — cannot evaluate",
            duration_seconds=0.0,
            error_summary="Empty evaluation command",
        )

    try:
        result = subprocess.run(
            ["bash", "-c", scenario.evaluation_method],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(repo_root),
            env={**os.environ, "PYTHONPATH": str(repo_root)},
        )
        duration = time.time() - start
        passed = result.returncode == 0

        error_summary = ""
        if not passed:
            # Extract the last meaningful error line
            lines = (result.stderr + result.stdout).strip().splitlines()
            error_lines = [
                line
                for line in lines
                if "error" in line.lower()
                or "assert" in line.lower()
                or "FAIL" in line
                or "Traceback" in line
            ]
            if error_lines:
                error_summary = error_lines[-1]
            elif lines:
                error_summary = lines[-1]
            else:
                error_summary = "Unknown error"

        return ScenarioResult(
            name=scenario.name,
            file_path=scenario.file_path,
            category=scenario.category,
            passed=passed,
            exit_code=result.returncode,
            stdout=result.stdout[-5000:],  # Cap output size
            stderr=result.stderr[-5000:],
            duration_seconds=round(duration, 2),
            error_summary=error_summary,
        )

    except subprocess.TimeoutExpired:
        duration = time.time() - start
        return ScenarioResult(
            name=scenario.name,
            file_path=scenario.file_path,
            category=scenario.category,
            passed=False,
            exit_code=-1,
            stdout="",
            stderr=f"TIMEOUT: scenario exceeded {timeout}s limit",
            duration_seconds=round(duration, 2),
            error_summary=f"Timeout after {timeout}s",
        )
    except Exception as e:
        duration = time.time() - start
        return ScenarioResult(
            name=scenario.name,
            file_path=scenario.file_path,
            category=scenario.category,
            passed=False,
            exit_code=-2,
            stdout="",
            stderr=str(e),
            duration_seconds=round(duration, 2),
            error_summary=str(e),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run dark factory holdout scenarios")
    parser.add_argument(
        "--category",
        type=str,
        default=None,
        help="Filter by category (environment, training, etc.)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout per scenario in seconds (default: 300)",
    )
    parser.add_argument(
        "--scenarios-dir",
        type=str,
        default=None,
        help="Path to scenarios directory (default: <repo_root>/scenarios/)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON path (default: artifacts/factory/scenario_results.json)",
    )
    args = parser.parse_args()

    # Find repo root (where this script lives in scripts/)
    repo_root = REPO_ROOT
    if args.scenarios_dir:
        scenarios_dir = Path(args.scenarios_dir)
    else:
        scenarios_dir = repo_root / "scenarios"
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = (
            repo_root / "artifacts" / "factory" / "scenario_results.json"
        )

    # Discover and parse scenarios
    scenario_files = sorted(scenarios_dir.glob("*.md"))
    if not scenario_files:
        print(f"ERROR: No scenario files found in {scenarios_dir}", file=sys.stderr)
        return 1

    scenarios = [parse_scenario(f) for f in scenario_files]

    # Filter by category if requested
    if args.category:
        scenarios = [s for s in scenarios if s.category == args.category.lower()]
        if not scenarios:
            msg = f"No scenarios match category '{args.category}'"
            print(f"ERROR: {msg}", file=sys.stderr)
            return 1

    print(f"Running {len(scenarios)} scenario(s)...")
    print("=" * 60)

    results: list[ScenarioResult] = []
    for i, scenario in enumerate(scenarios, 1):
        print(f"\n[{i}/{len(scenarios)}] {scenario.name} ({scenario.category})")
        print("-" * 40)

        result = run_scenario(scenario, args.timeout, repo_root)
        results.append(result)

        status = "PASS" if result.passed else "FAIL"
        print(f"  {status} ({result.duration_seconds}s)")
        if not result.passed and result.error_summary:
            print(f"  Error: {result.error_summary}")

    # Compute satisfaction score
    passed_count = sum(1 for r in results if r.passed)
    failed_count = sum(1 for r in results if not r.passed)
    total = len(results)
    satisfaction = passed_count / total if total > 0 else 0.0

    # Build report
    report = ScenarioReport(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        total=total,
        passed=passed_count,
        failed=failed_count,
        skipped=0,
        satisfaction_score=round(satisfaction, 4),
        results=[asdict(r) for r in results],
    )

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(asdict(report), indent=2) + "\n")

    # Summary
    print("\n" + "=" * 60)
    print(f"RESULTS: {passed_count}/{total} passed | Satisfaction: {satisfaction:.0%}")
    print(f"Report: {output_path}")

    if failed_count > 0:
        print("\nFailed scenarios:")
        for r in results:
            if not r.passed:
                print(f"  - {r.name}: {r.error_summary}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
