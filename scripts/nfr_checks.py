#!/usr/bin/env python3
"""Non-Functional Requirements (NFR) checker — Gate 2.

Extensible framework for running non-blocking quality checks beyond
lint/typecheck/test. Each check produces findings that feed into
feedback and the LLM-as-judge's holistic evaluation.

Adding a new NFR check:
    1. Create a function: def check_<name>(repo_root: Path) -> list[NFRFinding]
    2. Register it in NFR_CHECKS dict at the bottom of this file
    3. The factory loop will pick it up automatically

Usage:
    python packages/dark-factory/scripts/nfr_checks.py
    python packages/dark-factory/scripts/nfr_checks.py --check complexity
    python packages/dark-factory/scripts/nfr_checks.py --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


def _get_repo_root() -> Path:
    """Walk up from this file to find the git repo root."""
    current = Path(__file__).resolve().parent
    for parent in [current, *current.parents]:
        if (parent / ".git").is_dir():
            return parent
    raise RuntimeError("Repo root not found -- no .git directory in any parent")


REPO_ROOT = _get_repo_root()


@dataclass
class NFRFinding:
    """A single NFR finding."""

    nfr: str  # Which NFR this belongs to
    severity: str  # CRITICAL, WARNING, NIT, INFO
    message: str  # Human-readable description
    file: str = ""  # Optional: specific file
    line: int = 0  # Optional: specific line
    metric: str = ""  # Optional: metric name
    value: str = ""  # Optional: metric value
    threshold: str = ""  # Optional: threshold that was exceeded


@dataclass
class NFRResult:
    """Result of running one NFR check."""

    name: str
    status: str  # passed, failed, skipped, error
    findings: list[NFRFinding]
    tool: str  # What tool was used
    summary: str  # One-line summary


def _run_tool(
    cmd: list[str], repo_root: Path
) -> subprocess.CompletedProcess[str]:
    """Run a tool, returning the result without raising on failure."""
    try:
        return subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        result = subprocess.CompletedProcess(
            cmd, returncode=-1, stdout="", stderr=f"Tool not found: {cmd[0]}"
        )
        return result
    except subprocess.TimeoutExpired:
        result = subprocess.CompletedProcess(
            cmd, returncode=-2, stdout="", stderr=f"Timeout: {' '.join(cmd)}"
        )
        return result


# ── NFR Check Implementations ──────────────────────────────


def check_code_quality(repo_root: Path) -> list[NFRFinding]:
    """Extended code quality beyond basic lint.

    Uses ruff with broader rule selection including:
    - C90: McCabe complexity
    - S: Security (bandit-equivalent)
    - SIM: Simplification suggestions
    - RET: Return statement issues
    """
    findings: list[NFRFinding] = []

    result = _run_tool(
        [
            "ruff",
            "check",
            "src/",
            "--select",
            "C90,S,SIM,RET,PTH,ERA",
            "--no-fix",
            "--output-format",
            "json",
        ],
        repo_root,
    )

    if result.returncode == -1:
        findings.append(
            NFRFinding(
                nfr="code_quality",
                severity="INFO",
                message="ruff not available — install with: pip install ruff",
            )
        )
        return findings

    if result.stdout.strip():
        try:
            issues = json.loads(result.stdout)
            for issue in issues:
                findings.append(
                    NFRFinding(
                        nfr="code_quality",
                        severity="WARNING",
                        message=f"{issue.get('code', '?')}: {issue.get('message', 'unknown')}",
                        file=issue.get("filename", ""),
                        line=issue.get("location", {}).get("row", 0),
                    )
                )
        except json.JSONDecodeError:
            # Fall back to text output
            for line in result.stdout.strip().splitlines():
                findings.append(
                    NFRFinding(
                        nfr="code_quality",
                        severity="WARNING",
                        message=line.strip(),
                    )
                )

    return findings


def check_complexity(repo_root: Path) -> list[NFRFinding]:
    """Cyclomatic complexity via radon.

    Flags functions with complexity grade C or worse.
    """
    findings: list[NFRFinding] = []

    result = _run_tool(
        ["radon", "cc", "src/", "--min", "C", "--json"],
        repo_root,
    )

    if result.returncode == -1:
        findings.append(
            NFRFinding(
                nfr="complexity",
                severity="INFO",
                message="radon not available — install with: pip install radon",
            )
        )
        return findings

    if result.stdout.strip():
        try:
            data = json.loads(result.stdout)
            for filepath, blocks in data.items():
                for block in blocks:
                    findings.append(
                        NFRFinding(
                            nfr="complexity",
                            severity="WARNING",
                            message=(
                                f"{block.get('type', '?')} '{block.get('name', '?')}' "
                                f"has complexity {block.get('complexity', '?')} "
                                f"(grade {block.get('rank', '?')})"
                            ),
                            file=filepath,
                            line=block.get("lineno", 0),
                            metric="cyclomatic_complexity",
                            value=str(block.get("complexity", "")),
                            threshold="C",
                        )
                    )
        except json.JSONDecodeError:
            # Fall back to text output
            for line in result.stdout.strip().splitlines():
                findings.append(
                    NFRFinding(
                        nfr="complexity",
                        severity="WARNING",
                        message=line.strip(),
                    )
                )

    return findings


def check_dead_code(repo_root: Path) -> list[NFRFinding]:
    """Dead code detection via vulture."""
    findings: list[NFRFinding] = []

    result = _run_tool(
        ["vulture", "src/", "--min-confidence", "80"],
        repo_root,
    )

    if result.returncode == -1:
        findings.append(
            NFRFinding(
                nfr="dead_code",
                severity="INFO",
                message="vulture not available — install with: pip install vulture",
            )
        )
        return findings

    if result.stdout.strip():
        for line in result.stdout.strip().splitlines():
            # vulture output: "src/file.py:10: unused function 'foo' (90% confidence)"
            findings.append(
                NFRFinding(
                    nfr="dead_code",
                    severity="WARNING",
                    message=line.strip(),
                )
            )

    return findings


def check_security(repo_root: Path) -> list[NFRFinding]:
    """Security vulnerability detection via bandit."""
    findings: list[NFRFinding] = []

    result = _run_tool(
        ["bandit", "-r", "src/", "-f", "json", "-q"],
        repo_root,
    )

    if result.returncode == -1:
        findings.append(
            NFRFinding(
                nfr="security",
                severity="INFO",
                message="bandit not available — install with: pip install bandit",
            )
        )
        return findings

    if result.stdout.strip():
        try:
            data = json.loads(result.stdout)
            for issue in data.get("results", []):
                severity_map = {
                    "HIGH": "CRITICAL",
                    "MEDIUM": "WARNING",
                    "LOW": "NIT",
                }
                findings.append(
                    NFRFinding(
                        nfr="security",
                        severity=severity_map.get(
                            issue.get("issue_severity", ""), "WARNING"
                        ),
                        message=(
                            f"{issue.get('test_id', '?')}: "
                            f"{issue.get('issue_text', 'unknown')}"
                        ),
                        file=issue.get("filename", ""),
                        line=issue.get("line_number", 0),
                    )
                )
        except json.JSONDecodeError:
            # Fall back to text output
            for line in result.stdout.strip().splitlines():
                findings.append(
                    NFRFinding(
                        nfr="security",
                        severity="WARNING",
                        message=line.strip(),
                    )
                )

    return findings


# ── NFR Registry ────────────────────────────────────────────
# Add new checks here. The key is the check name, the value is
# (function, tool_name, description).

NFR_CHECKS: dict[str, tuple[object, str, str]] = {
    "code_quality": (
        check_code_quality,
        "ruff (extended)",
        "Extended code quality: complexity, security, simplification, return patterns",
    ),
    "complexity": (
        check_complexity,
        "radon",
        "Cyclomatic complexity analysis (grade C+ flagged)",
    ),
    "dead_code": (
        check_dead_code,
        "vulture",
        "Unused code detection (80%+ confidence)",
    ),
    "security": (
        check_security,
        "bandit",
        "Security vulnerability patterns",
    ),
    # ── Planned checks (uncomment when ready) ──
    # "duplication": (check_duplication, "jscpd", "Code duplication detection"),
    # "coverage": (check_coverage, "pytest-cov", "Test coverage (60% minimum)"),
    # "import_hygiene": (check_imports, "custom", "Orphan files, circular imports"),
    # "maintainability": (check_maintainability, "radon mi", "Maintainability index"),
    # "reliability": (check_reliability, "custom", "Scenario consistency across runs"),
}


def run_checks(
    repo_root: Path,
    selected: str | None = None,
) -> list[NFRResult]:
    """Run NFR checks and return results."""
    results: list[NFRResult] = []

    for name, (check_fn, tool, _description) in NFR_CHECKS.items():
        if selected and name != selected:
            continue

        try:
            findings = check_fn(repo_root)  # type: ignore[operator]
            # Determine status
            has_critical = any(f.severity == "CRITICAL" for f in findings)
            has_warning = any(f.severity == "WARNING" for f in findings)
            info_only = all(f.severity == "INFO" for f in findings)

            if info_only and findings:
                status = "skipped"
                summary = findings[0].message
            elif has_critical:
                status = "failed"
                n_crit = sum(1 for f in findings if f.severity == "CRITICAL")
                summary = f"{len(findings)} findings ({n_crit} critical)"
            elif has_warning:
                status = "passed"  # Non-blocking
                summary = f"{len(findings)} warnings"
            elif not findings:
                status = "passed"
                summary = "Clean"
            else:
                status = "passed"
                summary = f"{len(findings)} findings"

            results.append(
                NFRResult(
                    name=name,
                    status=status,
                    findings=findings,
                    tool=tool,
                    summary=summary,
                )
            )
        except Exception as e:
            results.append(
                NFRResult(
                    name=name,
                    status="error",
                    findings=[
                        NFRFinding(
                            nfr=name,
                            severity="WARNING",
                            message=f"Check failed: {e}",
                        )
                    ],
                    tool=tool,
                    summary=f"Error: {e}",
                )
            )

    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Non-Functional Requirement checks (Gate 2)"
    )
    parser.add_argument(
        "--check",
        type=str,
        default=None,
        help=f"Run a specific check ({', '.join(NFR_CHECKS.keys())})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Write JSON results to file",
    )
    args = parser.parse_args()

    repo_root = REPO_ROOT
    results = run_checks(repo_root, selected=args.check)

    if args.json or args.output:
        output = json.dumps(
            [asdict(r) for r in results], indent=2
        )
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(output + "\n")
            print(f"Results written to {args.output}")
        else:
            print(output)
    else:
        # Human-readable output
        print("=" * 60)
        print("GATE 2: Non-Functional Requirements")
        print("=" * 60)

        total_findings = 0
        for r in results:
            icon = {
                "passed": "PASS",
                "failed": "FAIL",
                "skipped": "SKIP",
                "error": "ERR ",
            }.get(r.status, "????")
            print(f"\n[{icon}] {r.name} ({r.tool}): {r.summary}")

            for f in r.findings:
                if f.severity == "INFO":
                    continue
                loc = ""
                if f.file:
                    loc = f" {f.file}"
                    if f.line:
                        loc += f":{f.line}"
                print(f"  [{f.severity}]{loc} {f.message}")
                total_findings += 1

        print(f"\n{'=' * 60}")
        print(f"Total: {total_findings} findings across {len(results)} checks")
        print("NOTE: Gate 2 is non-blocking. Findings feed into feedback.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
