# Code Quality Standards

Universal standards for all code written in this repository — whether by Codex (attractor), Claude Code (orchestrator), or humans. These are not guidelines; they are gates.

This file is the canonical source for quality rules. Referenced by `factory_fix.md`, Gate 0 review agent prompts (`review-prompts/`), and `SKILL.md`. Enforcement mechanism (which gates run what): see `SKILL.md`.

## Anti-Vacuous Test Rules

Every test must exercise real behavior through real code paths. Tests that pass by construction prove nothing and waste factory iterations.

1. **No mocking the system under test.** Mocks isolate external dependencies (network, filesystem, third-party APIs) — never the logic you're testing. If `@patch` targets the function the test claims to validate, the test is vacuous.
2. **No stub assertions.** `assert True`, `assert 1`, `assert not False`, and assertions against hardcoded expected values without running real logic are all vacuous.
3. **No tautological tests.** The expected value must not be computed by the same code being tested. `assert compute(x) == compute(x)` proves nothing.
4. **No zero-assertion tests.** Every `test_` function must contain at least one meaningful assertion or `pytest.raises` check.
5. **No excessive mocking.** If more than 50% of test setup is patches/mocks, you're testing the mocking framework. Redesign the test.
6. **The deletion test.** Would the test still pass if you replaced the implementation with `pass`? If yes, the test is vacuous.

## Anti-Gaming Rules

Implementations must solve the general problem, not the specific test inputs.

1. **No hardcoded lookup tables** matching known test cases. `is_prime(x): return x in {2, 3, 5, 7, 11, 13}` is not a prime checker.
2. **No overfitted implementations.** `if input == specific_value: return specific_output` is gaming. Implement general logic.
3. **No output-matching shortcuts.** If a function should compute a result, it must actually compute it — not return a cached or pre-recorded value.
4. **No overfitting to error messages.** When a scenario fails, fix the root cause. Making one specific assertion pass while breaking the general case is gaming.
5. **No test-detection.** Code must not behave differently when it detects pytest, CI environment variables, or test fixtures. Same code paths, always.
6. **No assertion-matching.** Fixing a specific assertion by hardcoding the expected value rather than fixing underlying logic is gaming.

## Implementation Honesty

Code must do what it claims to do, through real dependencies and real paths.

1. **Real imports.** Test files importing from `src/` must exercise the real module, not a local redefinition with the same name.
2. **Real configuration.** Config files must reflect actual runtime parameters. Test-only shortcuts that trivialize behavior (1 step, 1 episode) don't prove the system works.
3. **Real dependencies.** Docker builds include all packages the code needs at runtime. Skipping dependencies to speed up builds creates hollow artifacts.
4. **No import redirection.** Defining a local class/function that shadows a real import to avoid testing the real thing is architectural dishonesty.
5. **No dependency skipping.** Catching `ImportError` and silently degrading to a no-op is acceptable only for optional features, never for core functionality.
6. **No dead code.** Functions that exist solely to satisfy import checks but are never called in any real code path are dishonest artifacts.

## Test Hygiene

1. **Use `tmp_path` fixtures** for file operations — never touch the real filesystem.
2. **Real execution** over mocked execution — `subprocess.run` over mocked calls when testing command execution.
3. **Every assertion tests a meaningful property** — not just "doesn't crash."
4. **Tests must be independent** — no reliance on execution order or shared mutable state.
5. **Fixture scope matches test scope** — session-scoped fixtures only for truly expensive setup.

## Quality Gates

These must pass before any commit is considered complete:

| Gate | Command | What It Checks |
|------|---------|----------------|
| Lint | `make lint` | ruff check (style, imports, bugs) |
| Types | `make typecheck` | mypy (type correctness) |
| Tests | `make test` | pytest (full suite including attractor-built tests) |
| Docker | `docker build` | Build completes with real dependencies |
| Validate | `make validate` | All of the above + env-smoke |

## Non-Functional Requirements (Gate 2)

The factory supports pluggable NFR checks. Each NFR has a testable mechanism that runs without human code review:

| NFR | Mechanism | Status |
|-----|-----------|--------|
| Code quality | `ruff check` (extended rules) | Active |
| Complexity | `radon cc src/ --min C` (cyclomatic complexity) | Active |
| Dead code | `vulture src/` (unused code detection) | Active |
| Duplication | `jscpd src/` or `pylint --enable=duplicate-code` | Planned |
| Import hygiene | Custom check: orphan files, circular imports | Planned |
| Test coverage | `pytest --cov=src --cov-fail-under=60` | Planned |
| Maintainability | Radon maintainability index + LLM-as-judge | Planned |
| Security | `bandit -r src/` (vulnerability patterns) | Active |
| Performance | Scenario-level: training smoke <60s | Via Gate 3 |
| Reliability | Run scenario N times, check consistency | Planned |

NFR findings are non-blocking but tracked. They feed into the feedback loop and the LLM-as-judge's holistic evaluation.

## Automated Enforcement

These standards are enforced at multiple levels:

1. **`scripts/check_test_quality.py`** — AST-based scanner detecting tautological asserts, zero-assertion tests, excessive mocking, stub implementations, hardcoded returns, lookup tables.
2. **Gate 0: Adversarial Review (Agent Team)** — Parallel agent team catches vacuous tests, gaming, architectural dishonesty, spec violations before merge. Any CRITICAL finding blocks. See `SKILL.md` Step 4 for agent team composition.
3. **Gate 1: Deterministic CI** — `make lint && make typecheck && make test` must all pass.
4. **Gate 2: NFR checks** — pluggable non-functional requirement validators.
5. **LLM-as-Judge** — Claude Code reasons holistically through all gate outputs, factoring in NFR findings and trajectory.
