#!/usr/bin/env python3
"""Feedback compiler for the dark factory convergence loop.

Reads scenario results, CI logs, and previous iteration feedback
to produce structured feedback markdown that Codex can act on.

Usage:
    python packages/dark-factory/scripts/compile_feedback.py
    python packages/dark-factory/scripts/compile_feedback.py --iteration 3
"""

from __future__ import annotations

import argparse
import glob
import json
import time
from pathlib import Path


def _get_repo_root() -> Path:
    """Walk up from this file to find the git repo root."""
    current = Path(__file__).resolve().parent
    for parent in [current, *current.parents]:
        if (parent / ".git").is_dir():
            return parent
    raise RuntimeError("Repo root not found -- no .git directory in any parent")


REPO_ROOT = _get_repo_root()


def load_scenario_results(
    path: Path,
) -> dict[str, object] | None:
    """Load scenario results JSON."""
    if not path.exists():
        return None
    data: dict[str, object] = json.loads(path.read_text())
    return data


def load_ci_log(path: Path) -> str:
    """Load CI output log."""
    if not path.exists():
        return "(no CI log available)"
    content = path.read_text()
    if len(content) > 10000:
        return (
            content[:5000]
            + "\n\n... [truncated] ...\n\n"
            + content[-5000:]
        )
    return content


def get_iteration_count(factory_dir: Path) -> int:
    """Read current iteration count."""
    count_file = factory_dir / "iteration_count.txt"
    if count_file.exists():
        try:
            return int(count_file.read_text().strip())
        except ValueError:
            return 0
    return 0


def get_previous_feedback(
    factory_dir: Path,
) -> list[tuple[int, str]]:
    """Load previous feedback files for trajectory tracking."""
    feedbacks: list[tuple[int, str]] = []
    pattern = str(factory_dir / "feedback_iter_*.md")
    for f in sorted(glob.glob(pattern)):
        path = Path(f)
        try:
            iter_num = int(path.stem.split("_")[-1])
        except ValueError:
            continue
        content = path.read_text()
        lines = content.splitlines()
        summary_lines: list[str] = []
        in_summary = False
        for line in lines:
            if line.startswith("## Summary"):
                in_summary = True
                continue
            if in_summary and line.startswith("## "):
                break
            if in_summary:
                summary_lines.append(line)
        feedbacks.append(
            (iter_num, "\n".join(summary_lines).strip())
        )
    return feedbacks


def _fmt_list(names: list[str]) -> str:
    """Format a list of scenario names for display."""
    return ", ".join(names)


def infer_causes(
    results: dict[str, object],
) -> list[str]:
    """Infer likely root causes from error patterns."""
    causes: list[str] = []
    result_list: list[dict[str, object]] = results.get(
        "results", []
    )  # type: ignore[assignment]

    import_errors: list[str] = []
    assertion_errors: list[str] = []
    timeout_errors: list[str] = []
    file_not_found: list[str] = []

    for r in result_list:
        if r.get("passed"):
            continue
        stderr = str(r.get("stderr", ""))
        stdout = str(r.get("stdout", ""))
        combined = stderr + stdout
        name = str(r.get("name", "unknown"))

        # Classify by primary error type (elif prevents double-counting)
        if "ModuleNotFoundError" in combined:
            import_errors.append(name)
        elif "ImportError" in combined:
            import_errors.append(name)
        elif "TIMEOUT" in combined:
            timeout_errors.append(name)
        elif "FileNotFoundError" in combined:
            file_not_found.append(name)
        elif "No such file" in combined:
            file_not_found.append(name)
        elif "AssertionError" in combined:
            assertion_errors.append(name)

    n = len(import_errors)
    if import_errors:
        causes.append(
            f"Import errors in {n} scenario(s): "
            f"{_fmt_list(import_errors)}. "
            "Likely missing module or wrong import path."
        )
    n = len(assertion_errors)
    if assertion_errors:
        causes.append(
            f"Assertion failures in {n} scenario(s): "
            f"{_fmt_list(assertion_errors)}. "
            "Check the specific assertion messages."
        )
    n = len(timeout_errors)
    if timeout_errors:
        causes.append(
            f"Timeouts in {n} scenario(s): "
            f"{_fmt_list(timeout_errors)}. "
            "Possible infinite loop or slow computation."
        )
    n = len(file_not_found)
    if file_not_found:
        causes.append(
            f"Missing files in {n} scenario(s): "
            f"{_fmt_list(file_not_found)}. "
            "Expected artifacts not being created."
        )
    if not causes:
        causes.append(
            "No clear pattern detected "
            "— review individual failure details below."
        )

    return causes


def compile_feedback(
    results: dict[str, object] | None,
    ci_log: str,
    iteration: int,
    previous_feedback: list[tuple[int, str]],
) -> str:
    """Compile all inputs into structured feedback markdown."""
    out: list[str] = []

    # Header
    ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    out.append(f"# Factory Feedback — Iteration {iteration}")
    out.append(f"Generated: {ts}")
    out.append("")

    # Summary
    out.append("## Summary")
    if results:
        total = results.get("total", 0)
        passed = results.get("passed", 0)
        failed = results.get("failed", 0)
        score = results.get("satisfaction_score", 0.0)
        out.append(
            f"- **Satisfaction score: {score:.0%}** "
            f"({passed}/{total} scenarios passed)"
        )
        out.append(
            f"- Passed: {passed} | Failed: {failed} "
            f"| Total: {total}"
        )
    else:
        out.append(
            "- **No scenario results available** "
            "— scenarios did not run (likely Layer 1 failure)"
        )
    out.append("")

    # Convergence trajectory
    if previous_feedback:
        out.append("## Convergence Trajectory")
        out.append("| Iteration | Summary |")
        out.append("|-----------|---------|")
        for iter_num, summary in previous_feedback:
            first = summary.splitlines()[0] if summary else ""
            first = first.replace("|", "\\|")
            out.append(f"| {iter_num} | {first} |")
        out.append("")

    # Inferred causes
    if results:
        causes = infer_causes(results)
        out.append("## Likely Root Causes")
        for i, cause in enumerate(causes, 1):
            out.append(f"{i}. {cause}")
        out.append("")

    # Failed scenario details
    if results:
        rlist: list[dict[str, object]] = results.get(
            "results", []
        )  # type: ignore[assignment]
        failed = [r for r in rlist if not r.get("passed")]
        if failed:
            out.append("## Failed Scenarios — Full Details")
            out.append("")
            for r in failed:
                name = r.get("name", "Unknown")
                cat = r.get("category", "unknown")
                code = r.get("exit_code", "N/A")
                dur = r.get("duration_seconds", 0)
                err = r.get("error_summary", "N/A")
                out.append(f"### {name}")
                out.append(f"**Category:** {cat}")
                out.append(f"**Exit code:** {code}")
                out.append(f"**Duration:** {dur}s")
                out.append(f"**Error summary:** {err}")
                out.append("")
                se = str(r.get("stderr", "")).strip()
                so = str(r.get("stdout", "")).strip()
                if se:
                    out.append("**stderr:**")
                    out.append(f"```\n{se}\n```")
                if so:
                    out.append("**stdout:**")
                    out.append(f"```\n{so}\n```")
                out.append("")

    # CI log
    if ci_log and ci_log != "(no CI log available)":
        out.append("## CI Log Output")
        out.append(f"```\n{ci_log}\n```")
        out.append("")

    # Instructions
    out.append("## Instructions for Coding Agent")
    out.append("")
    out.append("Fix the failures above. Priorities:")
    out.append("1. Import errors and missing modules first")
    out.append("2. File/artifact production issues next")
    out.append("3. Behavioral assertion failures last")
    out.append("")
    out.append("Constraints:")
    out.append(
        "- Do NOT modify /scenarios/, /scripts/, "
        "or /packages/dark-factory/workflows/factory.yaml"
    )
    out.append(
        "- Do NOT modify /specs/ — read them as requirements"
    )
    out.append(
        "- Keep changes minimal — fix what's broken, "
        "don't refactor"
    )
    out.append("")

    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile factory feedback"
    )
    parser.add_argument(
        "--iteration",
        type=int,
        default=None,
        help="Override iteration number (default: auto)",
    )
    parser.add_argument(
        "--factory-dir",
        type=str,
        default=None,
        help="Factory artifacts dir (default: artifacts/factory/)",
    )
    args = parser.parse_args()

    repo_root = REPO_ROOT
    if args.factory_dir:
        factory_dir = Path(args.factory_dir)
    else:
        factory_dir = repo_root / "artifacts" / "factory"
    factory_dir.mkdir(parents=True, exist_ok=True)

    # Determine iteration
    if args.iteration is not None:
        iteration = args.iteration
    else:
        iteration = get_iteration_count(factory_dir) + 1

    # Load inputs
    results = load_scenario_results(
        factory_dir / "scenario_results.json"
    )
    ci_log = load_ci_log(factory_dir / "ci_output.log")
    previous_feedback = get_previous_feedback(factory_dir)

    # Compile
    feedback = compile_feedback(
        results, ci_log, iteration, previous_feedback
    )

    # Write feedback file
    output_path = factory_dir / f"feedback_iter_{iteration}.md"
    output_path.write_text(feedback)

    # NOTE: iteration_count.txt is owned by the workflow/Makefile,
    # not by this script. Do not write it here to avoid double-increment.

    print(f"Feedback compiled: {output_path}")
    print(f"Iteration: {iteration}")
    if results:
        score = results.get("satisfaction_score", 0)
        print(f"Satisfaction: {score:.0%}")
    else:
        print("Satisfaction: N/A (no scenario results)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
