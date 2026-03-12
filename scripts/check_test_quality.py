#!/usr/bin/env python3
"""Test quality checker — detects vacuous tests and gaming patterns.

Scans test files for anti-patterns that indicate tests pass by
construction rather than exercising real behavior.

Usage:
    python scripts/check_test_quality.py
    python scripts/check_test_quality.py --strict
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from dataclasses import asdict, dataclass
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
class Finding:
    """A test quality finding."""

    file: str
    line: int
    severity: str  # CRITICAL, WARNING, NIT
    pattern: str
    detail: str


def check_file(path: Path) -> list[Finding]:
    """Check a single test file for anti-patterns."""
    findings: list[Finding] = []
    content = path.read_text()
    lines = content.splitlines()

    try:
        tree = ast.parse(content, filename=str(path))
    except SyntaxError:
        findings.append(Finding(
            file=str(path),
            line=0,
            severity="CRITICAL",
            pattern="syntax_error",
            detail="File has syntax errors — cannot be parsed",
        ))
        return findings

    # Pattern 1: assert True / assert False / assert 1
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if re.match(
            r"assert\s+(True|1|not\s+False|not\s+0)\s*$",
            stripped,
        ):
            findings.append(Finding(
                file=str(path),
                line=i,
                severity="CRITICAL",
                pattern="tautological_assert",
                detail=f"Tautological assertion: {stripped}",
            ))

    # Pattern 2: Test functions with no assertions
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith(
            "test_"
        ):
            has_assert = False
            for child in ast.walk(node):
                if isinstance(child, ast.Assert):
                    has_assert = True
                    break
                # pytest.raises counts as an assertion
                if isinstance(child, ast.Attribute) and (
                    child.attr == "raises"
                ):
                    has_assert = True
                    break
                # Check for assert method calls
                if isinstance(child, ast.Call):
                    func = child.func
                    if isinstance(func, ast.Attribute):
                        if func.attr.startswith("assert"):
                            has_assert = True
                            break
            if not has_assert:
                findings.append(Finding(
                    file=str(path),
                    line=node.lineno,
                    severity="WARNING",
                    pattern="no_assertions",
                    detail=(
                        f"Test '{node.name}' has no assertions "
                        "— may pass vacuously"
                    ),
                ))

    # Pattern 3: Excessive mocking (more than 3 @patch decorators)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith(
            "test_"
        ):
            patch_count = 0
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call):
                    func = decorator.func
                    if isinstance(func, ast.Attribute):
                        if func.attr == "patch":
                            patch_count += 1
                    elif isinstance(func, ast.Name):
                        if func.id == "patch":
                            patch_count += 1
                elif isinstance(decorator, ast.Attribute):
                    if decorator.attr == "patch":
                        patch_count += 1
            if patch_count >= 3:
                findings.append(Finding(
                    file=str(path),
                    line=node.lineno,
                    severity="WARNING",
                    pattern="excessive_mocking",
                    detail=(
                        f"Test '{node.name}' has {patch_count} "
                        "@patch decorators — likely testing mocks, "
                        "not real code"
                    ),
                ))

    # Pattern 4: Stub implementations in src/ (not test files)
    # This is only checked for non-test files
    if "/tests/" not in str(path):
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                body = node.body
                # Function body is just `pass`
                if (
                    len(body) == 1
                    and isinstance(body[0], ast.Pass)
                ):
                    findings.append(Finding(
                        file=str(path),
                        line=node.lineno,
                        severity="WARNING",
                        pattern="stub_implementation",
                        detail=(
                            f"Function '{node.name}' is a stub "
                            "(just `pass`)"
                        ),
                    ))
                # Function body is just `return True`
                if (
                    len(body) == 1
                    and isinstance(body[0], ast.Return)
                    and isinstance(body[0].value, ast.Constant)
                    and body[0].value.value is True
                ):
                    findings.append(Finding(
                        file=str(path),
                        line=node.lineno,
                        severity="CRITICAL",
                        pattern="hardcoded_return",
                        detail=(
                            f"Function '{node.name}' always "
                            "returns True — likely a stub"
                        ),
                    ))

    # Pattern 5: Hardcoded lookup tables in src/
    if "/tests/" not in str(path):
        for i, line in enumerate(lines, 1):
            # Detect `return x in {literal, literal, ...}`
            if re.search(
                r"return\s+\w+\s+in\s+\{[\d,\s]+\}",
                line,
            ):
                findings.append(Finding(
                    file=str(path),
                    line=i,
                    severity="WARNING",
                    pattern="lookup_table",
                    detail=(
                        "Function uses hardcoded set membership "
                        "— may be overfitted to test inputs"
                    ),
                ))

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check test quality for anti-patterns"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON (for automation)",
    )
    parser.add_argument(
        "--path",
        type=str,
        default=None,
        help="Specific path to check (default: tests/ and src/)",
    )
    args = parser.parse_args()

    repo_root = REPO_ROOT
    if args.path:
        paths = list(Path(args.path).rglob("*.py"))
    else:
        paths = (
            list((repo_root / "tests").rglob("*.py"))
            + list((repo_root / "src").rglob("*.py"))
        )

    all_findings: list[Finding] = []
    for path in paths:
        findings = check_file(path)
        all_findings.extend(findings)

    # Group by severity
    critical = [f for f in all_findings if f.severity == "CRITICAL"]
    warnings = [f for f in all_findings if f.severity == "WARNING"]
    nits = [f for f in all_findings if f.severity == "NIT"]

    if args.json:
        result = {
            "name": "test_quality",
            "tool": "check_test_quality.py",
            "status": "failed" if critical else "passed",
            "summary": (
                f"{len(all_findings)} findings ({len(critical)} critical)"
                if critical
                else f"{len(all_findings)} findings"
                if all_findings
                else "Clean"
            ),
            "findings": [asdict(f) for f in all_findings],
        }
        print(json.dumps(result, indent=2))
        return 1 if critical else 0

    if not all_findings:
        print("No test quality issues found.")
        return 0

    for finding in all_findings:
        print(
            f"[{finding.severity}] {finding.file}:{finding.line} "
            f"({finding.pattern}): {finding.detail}"
        )

    print(f"\nTotal: {len(critical)} critical, "
          f"{len(warnings)} warnings, {len(nits)} nits")

    if critical:
        return 1
    if args.strict and warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
