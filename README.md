# Dark Factory

AI-driven convergence loop that iteratively builds software via AI coding agents. Specs define what the system should do. Scenarios evaluate holdout behavior. Feedback steers the agent. Code is treated as opaque weights -- correctness is inferred exclusively from externally observable behavior, never from source inspection.

## The Pattern

```
Seed --> Agent --> Validate --> Feedback --> Repeat until satisfied
```

The factory orchestrator (Claude Code) drives the loop. The coding agent (OpenAI Codex or any non-interactive coding agent) writes the code. The factory never inspects the code for correctness -- it only evaluates observable behavior through automated gates and holdout scenarios.

## Installation

Clone into your repo's Claude Code skills directory:

```bash
git clone https://github.com/joeyfezster/dark-factory.git .claude/skills/factory-orchestrate
```

The factory also uses the [PR Review Pack](https://github.com/joeyfezster/pr-review-pack) for generating merge-gate reports:

```bash
git clone https://github.com/joeyfezster/pr-review-pack.git .claude/skills/pr-review-pack
pip install -r .claude/skills/pr-review-pack/requirements.txt
```

## Prerequisites

**Required:**
- **Python 3.12+**
- **git**
- **gh CLI** (authenticated -- run `gh auth login`) -- used by `scripts/persist_decisions.py` for GitHub API calls
- **Claude Code** with Agent Teams enabled (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`)
- **OpenAI Codex** or any non-interactive coding agent that can work from a branch

**Optional (for Gate 0 Tier 1 and Gate 2 NFR checks):**
- `ruff` -- code quality (`pip install ruff`)
- `radon` -- cyclomatic complexity (`pip install radon`)
- `vulture` -- dead code detection (`pip install vulture`)
- `bandit` -- security scanning (`pip install bandit`)

Scripts handle missing optional tools gracefully -- checks are skipped with a warning, not a crash.

## Gate Structure

The factory validates every coding agent iteration through a sequence of gates. Each gate is harder to game than the last.

### Gate 0 -- Two-Tier Code Review

The first line of defense. No point sending code to CI if Gate 0 finds critical issues.

**Tier 1: Deterministic Tool Checks** -- Run via `scripts/run_gate0.py`, which executes 5 checks in parallel:

| Check | Tool | What It Catches |
|-------|------|-----------------|
| `code_quality` | ruff (extended) | Lint violations, style, import issues |
| `complexity` | radon | Cyclomatic complexity above threshold |
| `dead_code` | vulture | Unreachable code, unused functions |
| `security` | bandit | Security vulnerability patterns |
| `test_quality` | check_test_quality.py | Vacuous tests, stub assertions, mock abuse |

**Tier 2: LLM Semantic Review Agents** -- 4 review agents run in parallel via Claude Code Agent Teams:

| Agent | Focus |
|-------|-------|
| Code Health Reviewer | Code quality, complexity, dead code |
| Security Reviewer | Security vulnerabilities |
| Test Integrity Reviewer | Test quality and integrity |
| Adversarial Reviewer | Gaming, spec violations, architectural dishonesty |

Each agent reviews independently in a separate context window, structurally guaranteeing anti-anchoring. Tier 2 builds on Tier 1 output -- agents receive the deterministic findings as context.

**Fail-fast:** Any CRITICAL finding from either tier blocks the iteration. The coding agent's code is merged (not reverted) so the next iteration is incremental, and feedback is compiled with specific remediation instructions.

### Gate 1 -- CI: Lint + Typecheck + Tests

Standard CI pipeline: ruff, mypy, pytest. Runs automatically on push.

### Gate 2 -- NFR Checks

Non-functional requirements: code complexity thresholds, dead code detection, security scanning. Run via `scripts/nfr_checks.py`.

### Gate 3 -- Behavioral Scenarios (Holdout Evaluation)

The coding agent never sees these. Scenarios are stripped from the branch before the agent runs (`scripts/strip_holdout.py`) and restored after (`scripts/restore_holdout.py`). Each scenario defines a behavioral expectation and an executable evaluation method. All scenarios must pass for convergence -- no percentage thresholds, no exceptions.

### LLM-as-Judge

Holistic assessment of the iteration. Considers all gate results, warning-level findings, and overall trajectory. Produces a satisfaction score that feeds into feedback.

## Writing Specs

Specs live in `specs/` and define what the system should do. The coding agent reads these as its instructions. A spec is a markdown file with component-level requirements -- interfaces, behavior, constraints.

Example structure:
```markdown
# Component Name

## Purpose
What this component does and why.

## Interface
Public API, inputs, outputs, types.

## Behavior
What the component does under various conditions.

## Constraints
Performance, security, compatibility requirements.
```

## Writing Scenarios

Scenarios live in `scenarios/` and are the holdout evaluation criteria. **The coding agent never sees them.** They are stripped before the agent runs and restored for evaluation.

Each scenario is a markdown file with required sections:

```markdown
# Scenario: Name

## Category
{category}

## Preconditions
- What must be true before evaluation

## Behavioral Expectation
What the system should do, described in terms of observable behavior.

## Evaluation Method
```bash
# Executable script that tests the expectation
python -c "
# ... test code that asserts the expectation ...
print('PASS')
"
```

## Pass Criteria
What constitutes a pass (e.g., "exit code 0, prints PASS").

## Evidence Required
What artifacts prove the scenario passed.
```

Scenarios test observable behavior, not implementation details. The coding agent could implement the system any way it wants -- the scenarios only check that the behavior matches the spec.

## Permissions & Setup

### Browser Setup

The factory orchestrator drives Codex via Chrome. Ensure:
- Chrome is logged into Codex (ChatGPT Plus account)
- The repository is accessible from the Codex UI

### Claude Code Permissions

The skill's `allowed-tools` pre-approves `Bash`, `Read`, `Write`, `Glob`, `Grep`, `Edit` to avoid permission prompts during orchestration.

## Includes PR Review Pack

The Dark Factory includes the [PR Review Pack](https://github.com/joeyfezster/pr-review-pack) as the human decision artifact. When the factory converges (all scenarios passing), it generates a self-contained interactive HTML review pack. The project lead reviews the report, not the code. The review pack shows what changed, what the risks are, and what to watch post-merge.

## License

Apache 2.0 -- see [LICENSE](LICENSE) for details.
