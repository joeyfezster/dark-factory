---
name: factory-orchestrate
description: Run the dark factory convergence loop. Use when the user says "run a factory crank", "start the factory", "orchestrate a crank", or similar. Orchestrates Codex via browser, manages holdout isolation, runs validation gates, and performs LLM-as-judge evaluation.
allowed-tools: Bash, Read, Write, Glob, Grep, Edit
---

# Dark Factory Orchestration — Claude Code as Orchestrator

You are the factory orchestrator. You run the convergence loop that turns specs into working software through iterative AI coding + validation.

## Script Discovery

All scripts referenced in this skill are in the `scripts/` directory adjacent to this SKILL.md.
From the monorepo root, these resolve to `packages/dark-factory/scripts/`.

Review prompts are in the `review-prompts/` directory adjacent to this SKILL.md (symlink to shared prompts).

## Crank Lifecycle

A factory crank progresses through three states:
- **IN PROGRESS** — iterating (Codex coding, gates running, feedback looping)
- **CONVERGED** — ALL scenarios passing, PR created with review pack, pending human merge decision
- **COMPLETE** — factory branch merged to main

A crank is NOT complete until the factory branch is merged to main. Convergence is necessary but not sufficient — the human accept/merge gate is what completes a crank.

## Prerequisites

- Chrome is logged into Codex (ChatGPT Plus account)
- Repository: `joeyfezster/building_ai_w_ai`
- Branch to base work on: confirm with user or default to `factory/v1`
- Max iterations: confirm with user or default to 5

**Convergence requires ALL scenarios passing.** There is no percentage threshold — a single failing scenario blocks convergence, regardless of cause (regression, merge conflict, transient issue, or new failure). The factory owns its output quality end-to-end. Never declare convergence with a failing scenario, and never excuse a failure by attributing it to a previous iteration or the base branch.

## The Loop

For each iteration:

### Step 1: Create Factory Branch
```bash
# First crank — create from base branch
git checkout -b df-crank-v01-{descriptor} {base_branch}
git push -u origin df-crank-v01-{descriptor}
```

Branch naming: `df-crank-vXX-{descriptor}` where XX is the crank version.

### Step 2: Strip Holdout
```bash
python packages/dark-factory/scripts/strip_holdout.py
git push
```

This deterministically removes `/scenarios/` and comments out scenario Makefile targets. Codex literally cannot see evaluation criteria.

Verify: `ls scenarios/` should fail (directory gone).

### Step 3: Invoke Codex via Browser

Open the Codex UI in Chrome. Provide:
- **Repository**: `joeyfezster/building_ai_w_ai`
- **Base branch**: `df-crank-vXX-{descriptor}` (the stripped branch)
- **Prompt**: Contents of `prompts/factory_fix.md` + the latest feedback file (`artifacts/factory/feedback_iter_N.md`)
- **Versions**: 1

Codex will create its own branch (named `codex-...`). Wait for it to finish.

### Step 4: Gate 0 — Two-Tier Code Review (Script + Agent Team)

Before merging Codex's changes, run a **full two-tier review**. This is the first line of defense — there is no point sending code to CI or later gates if Gate 0 finds critical issues.

1. Fetch Codex's branch: `git fetch origin`
2. Get the diff: `git diff df-crank-vXX...origin/codex-{branch}`

#### Tier 1: Deterministic Tool Checks

Run the Gate 0 deterministic runner. This executes all 5 tool checks in parallel and produces a JSON artifact:

```bash
python packages/dark-factory/scripts/run_gate0.py
# Produces: artifacts/factory/gate0_results.json
```

| Check | Tool | What It Catches |
|-------|------|-----------------|
| `code_quality` | ruff (extended) | Lint violations, style, import issues |
| `complexity` | radon | Cyclomatic complexity > threshold |
| `dead_code` | vulture | Unreachable code, unused functions |
| `security` | bandit | Security vulnerability patterns |
| `test_quality` | check_test_quality.py | Vacuous tests, stub assertions, mock abuse |

**Tier 1 is necessary but insufficient.** These tools operate at the AST/regex level. They catch the obvious stuff fast — but a sophisticated agent can game them. Tier 2 builds on tier 1 output.

#### Tier 2: LLM Semantic Review Agents

3. **Spawn the Tier 2 agent team.** Use `TeamCreate` and launch these 4 agents in parallel via the `Task` tool. Each agent receives: the diff, the tier 1 results (`gate0_results.json`), and its paradigm-specific review prompt from `review-prompts/`.

   | Agent | Paradigm Review Prompt | Paradigm |
   |-------|----------------------|----------|
   | `code-health-reviewer` | `code_health_review.md` | Code quality + complexity + dead code |
   | `security-reviewer` | `security_review.md` | Security vulnerabilities |
   | `test-integrity-reviewer` | `test_integrity_review.md` | Test quality and integrity |
   | `adversarial-reviewer` | `adversarial_review.md` | Holistic: gaming, spec violations, architectural dishonesty |

   Each agent also receives: `gate0_results.json` (tier 1 findings) + `docs/code_quality_standards.md` + the diff. The adversarial reviewer additionally receives `/specs/*.md`.

   **File persistence (non-negotiable):** Each agent MUST write its findings to `artifacts/factory/gate0_tier2_{paradigm}.md` (e.g., `gate0_tier2_code_health.md`, `gate0_tier2_security.md`, `gate0_tier2_test_integrity.md`, `gate0_tier2_adversarial.md`). This is in ADDITION to sending a message to the team lead. Findings survive context compaction only if they are on disk. SendMessage alone is not durable.

   **Stuck agent recovery:** If an agent becomes unresponsive (e.g., after context compaction or session restart), the orchestrator checks for its output file. If the file exists and is complete, the agent's work is recovered. If not, the orchestrator stops the stuck agent and re-spawns a replacement for that paradigm only — no need to re-run the entire team.

4. **Aggregate findings.** Collect all tier 2 agent outputs from `artifacts/factory/gate0_tier2_*.md` + tier 1 results from `gate0_results.json`. Each finding has a severity: CRITICAL, WARNING, or NIT. File artifacts are the source of truth — not messages, which may be lost to compaction.

5. **Fail-fast rule:** If **any tier** (1 or 2) reports a CRITICAL finding, Gate 0 fails. Do NOT proceed to later gates (1-3). However, **DO merge Codex's changes onto the factory branch** so that iteration N+1 is incremental — Codex iterates on its own code with feedback, rather than rebuilding from scratch. Compile all findings (from both tiers) as feedback and loop back to Step 3 with specific remediation instructions.

   **Gate 0 failure workflow:**
   ```bash
   # 1. Merge Codex's code (keep it for incremental iteration)
   git merge origin/codex-{branch} --no-ff -m "factory: merge codex iteration N (Gate 0 blocked — iterating)"
   # 2. Delete Codex's remote branch (consumed by merge, prevent branch pollution)
   git push origin --delete codex-{branch}
   # 3. Commit feedback file
   git add artifacts/factory/feedback_iter_N.md
   git commit -m "factory: Gate 0 feedback for iteration N"
   # 4. Push — Codex's next run sees its own code + feedback
   git push
   # 5. Loop back to Step 3 (invoke Codex again)
   ```

   **NEVER revert Codex's merge on Gate 0 failure.** Reverting forces Codex to rebuild from zero, wasting an iteration. The feedback is specific enough to guide incremental fixes. The code is "wrong but close" — keep it and steer.

**If clean or WARNING-only across both tiers**: Proceed to Step 5. WARNING findings are tracked — they feed into the LLM-as-judge evaluation in Step 10.

**Why two tiers, not just tools or just LLMs:** Tools are fast, cheap, and deterministic — they catch the obvious stuff in seconds. But they operate at the AST/regex level and can be gamed by a sophisticated agent. LLM agents review through the same paradigms at a higher caliber — they catch semantic dead code, meaningful test gaps, and subtle gaming that tools miss. Running both tiers means Gate 0 is fast AND deep. Neither tier alone is sufficient.

### Step 5: Merge Codex Changes
```bash
git merge origin/codex-{branch} --no-ff -m "factory: merge codex iteration N"
```

### Step 5a: Delete Codex's Remote Branch

**Immediately after merging**, delete Codex's branch from origin. The factory is branch-heavy — stale Codex branches pollute the namespace and create confusion about which branch is current. The code is preserved in the merge commit on the factory branch.

```bash
git push origin --delete codex-{branch}
```

This applies to EVERY merge — whether Gate 0 passes or fails. The Codex branch is consumed by the merge; it has no further purpose.

### Step 5b: Check CI Results

After pushing, check CI results. CI runs Gates 1-3 on every push to factory/** branches. Use CI results as early signal before running gates locally.

```bash
# Wait for CI to complete (typically 2-5 minutes)
gh run list --branch df-crank-vXX --limit 3

# Check the PR's checks (if PR exists)
gh pr checks <PR_NUMBER>
```

**Known `gh pr checks` behavior:**
- `gh pr checks` shows checks associated with the PR's **merge ref**, not the branch HEAD directly
- If a bot (GITHUB_TOKEN) pushes a commit that becomes PR HEAD, GitHub does NOT re-trigger CI workflows (prevents infinite loops). The PR may show "0 checks" even though CI ran fine on the previous commit.
- Workaround: If you see stale/missing checks, use `gh run list --branch <branch>` instead — this shows actual workflow runs regardless of the merge ref.
- Commits pushed by workflows using `GITHUB_TOKEN` do not trigger other workflows. This is a GitHub safety measure.
- If CI results conflict with your local gate results, investigate — don't just ignore the discrepancy.

**If CI fails**: Use the github.com's copilot's 'explain errors' as initial reference, check logs to validate and make up your own mind, compile actionable feedback (yourself), and loop back to Step 3.

### Step 6: Restore Holdout
```bash
python packages/dark-factory/scripts/restore_holdout.py
git add scenarios/ Makefile
git commit -m "factory: restore holdout scenarios for evaluation"
```

### Step 7: Gate 1 — Deterministic Validation
```bash
make lint && make typecheck && make test
```

"test" = *should be* the FULL pytest suite, including any tests Codex wrote (already reviewed in Gate 0).

**If fail**: Compile feedback (use this script as aid, but make sure you intervene if the feedback doesn't make sense: `python packages/dark-factory/scripts/compile_feedback.py --iteration N`), loop to Step 3.

### Step 8: Gate 2 — Non-Functional Requirements
```bash
make nfr-check
```

This runs all implemented NFR checks (code quality, complexity, dead code, security).

Gate 2 is **non-blocking** but findings are tracked and feed into:
- The feedback for the next Codex iteration
- Your LLM-as-judge evaluation in Step 10

### Step 9: Gate 3 — Behavioral Scenarios
```bash
python packages/dark-factory/scripts/run_scenarios.py --timeout 180
```

Produces `artifacts/factory/scenario_results.json` with satisfaction score.

**Hard gate: if ANY scenario fails, the crank has NOT converged.** Do not proceed to Step 10. Compile feedback and loop back to Step 3. The satisfaction score is a trajectory metric (is progress being made?) — it is NOT the convergence criterion. Convergence = zero failures.

### Step 10: LLM-as-Judge — Holistic Evaluation

You ARE the judge. All scenarios passed — but passing is necessary, not sufficient. Reason through:

1. **Satisfaction trajectory**: Is the score improving across iterations? Plateaued? Regressing?
2. **Failure patterns**: Are the same scenarios failing repeatedly? Different ones each time?
3. **Fix quality**: Do Codex's changes look like real solutions or gaming attempts? (Gate 0 caught the obvious ones, but look for subtle patterns across iterations)
4. **Gate 2 NFR findings**: Even though non-blocking, are there concerning patterns? Growing complexity? Dropping coverage?
5. **Systemic issues**: Is there something the score doesn't capture? An architectural problem that will cause future failures?
6. **Documentation currency**: Did this iteration's changes affect documented behavior? Check: Are specs in `/specs/` still accurate? Does the README reflect current state? Are factory docs (`dark_factory.md`, `code_quality_standards.md`) still correct? Stale documentation is technical debt — flag it in feedback if needed.

**If satisfied**: Proceed to Step 11.
**If not satisfied**: Compile feedback with your holistic assessment, loop to Step 3.

### Step 10b: Resolve Bot Reviewer Comments

After the PR is created, bot reviewers (Copilot, Codex connector) post comments recommending changes. The orchestrator evaluates and routes each one — the goal is to fix everything now, not carry tech debt.

**Workflow:**
1. Read all unresolved PR review threads (after CI completes — bots post after CI).
2. **Evaluate each comment.** Bot reviewers can be wrong. For each recommendation, reason about: Is it valid? Is it in scope? What severity does it actually warrant?
3. **Route by who can fix it:**
   - **Orchestrator's agent team** (non-product: infra, config, dependency compilation, docs, CI): Spawn an agent to fix it directly. Push the fix, resolve the thread.
   - **Attractor** (product code OR complex logic OR security issues OR code performance): Synthesize into `artifacts/factory/post_merge_feedback.md` — preserving the file path, line number, what was flagged, and the orchestrator's assessment. Then loop back to the attractor (new factory iteration via Step 3) with this feedback included.
   - **Invalid/false-positive**: Resolve the thread with a reply explaining why.

**Every thread resolution MUST include a reply comment** explaining how it was resolved — what action was taken, by whom (orchestrator vs attractor), and where the evidence lives (commit SHA, feedback file path). Never resolve a thread silently.

**Continuity across cranks:** When starting a new factory crank, check `artifacts/factory/post_merge_feedback.md` for items synthesized from the previous iteration's review comments. These must be included in the attractor's seed feedback so they are not dropped.

### Step 11: Create PR (Accept/Merge Gate)
```bash
gh pr create \
  --title "[Factory] df-crank-vXX converged at {score}%" \
  --body "$(cat <<'EOF'
## Dark Factory — Converged

**Satisfaction score: {score}%**
**Iterations: {N}**
**Gate 2 NFR status: {summary}**

### Accept/Merge Gate
This PR was produced by the dark factory convergence loop, orchestrated by Claude Code.

**Before merging, verify:**
- [ ] Satisfaction score meets your quality bar
- [ ] Review latest feedback for residual warnings
- [ ] Gate 2 NFR findings are acceptable
- [ ] No unexpected files or dependencies introduced

**To merge:** Approve and merge. The factory branch can then be deleted.
**To reject:** Close this PR and either adjust scenarios/specs or trigger another crank.
EOF
)" \
  --label factory-converged --label accept-merge-gate
```

### Step 12: Generate PR Review Pack

After creating the PR, invoke the `/pr-review-pack` skill to generate the interactive HTML review pack. This is how the human project lead reviews the factory's output — they review the report, not the code.

The review pack gives the project lead:
- Architecture diagram showing which zones were touched
- Adversarial findings (from Gate 0 agent team) graded by file
- CI performance with health classification
- Key decisions with zone-level traceability
- Convergence result (gate-by-gate status)
- Post-merge items with code snippets and failure/success scenarios
- Factory history (iteration timeline, gate findings per iteration)

```
/pr-review-pack {PR_NUMBER}
```

The review pack is the artifact that communicates factory status to the human. Without it, the accept/merge gate is a rubber stamp.

### Step 13: Post-Merge Persistence

After the project lead merges the PR:

1. **Persist decisions** to the cumulative log:
   ```bash
   python packages/dark-factory/scripts/persist_decisions.py --pr {PR_NUMBER}
   ```
   The script extracts decisions from the review pack HTML (or from the JSON intermediate if available via `--data`) and appends them to `docs/decisions/decision_log.json`. It is idempotent — safe to run multiple times.

2. **Create post-merge issues** (if applicable):
   ```bash
   python scripts/create_postmerge_issues.py --pr {PR_NUMBER}
   ```

3. **Commit and push** the updated decision log:
   ```bash
   git add docs/decisions/decision_log.json
   git commit -m "decisions: persist PR #{PR_NUMBER} decisions"
   git push
   ```

4. **Delete Codex's remote branch** (cleanup):
   ```bash
   git push origin --delete codex-{branch}
   ```

### Stall Protocol

If after 3+ iterations:
- Same scenario fails with same error → the spec or scenario may need adjustment. Escalate to the project lead.
- Score oscillates without converging → architectural issue. Escalate.
- Gate 0 keeps finding critical issues → attractor needs stronger constraints. Update `factory_fix.md`.

## Reference Files

- **Attractor prompt**: `prompts/factory_fix.md`
- **Gate 0 tier 1 runner**: `scripts/run_gate0.py` (deterministic tool checks → `gate0_results.json`)
- **Gate 0 tier 2 review prompts**: `review-prompts/` (all paradigm docs in this directory)
- **Code quality standards**: `docs/code_quality_standards.md`
- **NFR checks script**: `scripts/nfr_checks.py` (Gate 0 tier 1 static analysis + Gate 2 NFR framework)
- **Test quality scanner**: `scripts/check_test_quality.py` (Gate 0 tier 1)
- **PR review pack skill**: `packages/pr-review-pack/SKILL.md` (Step 12)
- **Decision log**: `docs/decisions/decision_log.json` (Step 13, cumulative archive)
- **Decision persistence**: `scripts/persist_decisions.py` (Step 13)
- **Specs**: `specs/*.md`
- **Factory docs**: `docs/dark_factory.md`
- **Factory architecture**: `docs/factory_architecture.html`

## Operational Knowledge

### Layered Defense Against Gaming
The factory's quality defense is layered — no single gate is sufficient:
1. **Gate 0 tier 1** (deterministic, `run_gate0.py`) — all 5 tool checks in parallel (ruff, radon, vulture, bandit, test-quality). Catches dead code, complexity, security issues, lint violations, and obvious vacuous test patterns. Fast, cheap, runs in seconds. Risk: AST/regex-level analysis can be fooled by sophisticated gaming.
2. **Gate 0 tier 2** (LLM agent team, parallel) — 4 specialized agents review through the same paradigms at semantic depth. The code-health, security, and test-integrity reviewers build on tier 1 output. The adversarial reviewer reads the full diff holistically against specs and quality standards. Catches what tools miss: semantic dead code, auth bypasses, tests that prove nothing, gaming, architectural dishonesty.
3. **Gate 3 holdout scenarios** — behavioral evaluation against criteria the attractor never sees (ground truth). If the code actually works, gaming doesn't matter.

Tier 1 and tier 2 run at Gate 0 before merge. Any CRITICAL finding from either tier stops the pipeline. No single layer is sufficient — tools catch the cheap stuff fast, LLM agents catch the clever stuff, and holdout scenarios verify actual behavior.

### Iteration → Commit Model
Each factory iteration produces ONE commit from Codex (via merge). This provides a clean diff for adversarial review and clear rollback boundaries. The commit message must include the iteration number for traceability.

### CI vs. Orchestrator Roles
CI (factory.yaml) runs validation-only on every push — Gates 1, 2, 3 + feedback compilation. CI does NOT drive the convergence loop. Claude Code drives the loop via this skill. CI results are INPUT to orchestration decisions, not orchestration themselves.

**Current CI structure:**
- `factory-self-test (push)` — factory script validation
- `factory-self-test (PR)` — PR-specific factory validation
- `factory-loop` — fallback convergence via Codex API (only with OPENAI_API_KEY)
- `validate (push)` — product code validation
- `validate (PR)` — PR-specific product validation

**Future consolidation note:** Once the factory runs regularly, `factory-loop` and `validate` could be consolidated into a single workflow with better separation. Current overlap provides coverage redundancy during proof-of-concept.

### NFR Gate Architecture
Gate 2 runs deterministic tool-based checks. Each check follows the pattern:
1. Run external tool (ruff, radon, vulture, bandit)
2. Parse output (JSON preferred, text fallback)
3. Map findings to severity (CRITICAL/WARNING/NIT/INFO)
4. Return structured findings

Adding a new check: write `check_<name>(repo_root: Path) -> list[NFRFinding]`, register in `NFR_CHECKS` dict. The factory picks it up automatically.

**Important:** All JSON parsing must include fallback handling for decode errors. Silent `pass` on JSONDecodeError hides tool failures — always emit at least a WARNING finding.

### Gate 2 and LLM-Based Review
Gate 2 should stay deterministic (tool-based). LLM-based review belongs in Gate 0 (adversarial review) and Step 10 (LLM-as-judge). Mixing deterministic and non-deterministic findings in the same gate creates confusion about what's reliable vs. advisory. If LLM-based checks are added, label findings as "advisory" and never let them block convergence alone.

### Holdout Stripping Scope
`strip_holdout.py` removes `/scenarios/` and Makefile scenario targets. It also strips review pack artifacts (`docs/pr_review_pack.html`, `docs/pr_diff_data.json`) — the attractor has no business seeing adversarial review findings from previous iterations. The attractor's information boundary is: specs + feedback + its own code. Nothing else.

### Git Hooks vs. CI Enforcement
`.githooks/pre-commit` runs ruff + mypy on staged Python files — a local speed bump. It is NOT enforced on clean clone; developers must run `make install-hooks`. CI is the enforcement layer. The hook catches issues before they hit CI, saving iteration time. Both exist because they serve different failure modes.
