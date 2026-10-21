# Agent Development Instructions

Development in this repository is workflow-first, not change-first.
Do not start by editing code. Load `development-workflow` for every
development task and follow it:

1. Understand the requirement first. Look facts up yourself; ask the user
   only for decisions, each with a recommended answer.
2. Workflow first, single-point second: run the matching `workflow-*`
   Skill for this kind of problem. Only if none fits, compose a single-point
   route for this task from the available Skills and native capabilities.
3. A new or changed route is approved by the user as part of the plan.
   Approved workflows are reused without re-approval.
4. Run the route. Propose updating a workflow when the run shows it should
   change, or solidifying a single-point route that will recur as a new
   workflow.

Follow each selected Skill's own instructions. Treat repository facts and
executable checks as stronger evidence than agent claims.

## Project-specific rules

### Language

Everything in this repository is written in English: code, comments, docstrings, UI text, tests, commit
messages, README and reports. The design documents under `docs/*-v1.md` are kept as their author wrote them.

### Boundaries

- kb-intake is a standalone component (intake-design §1). Consumers only read packages; kb-intake never imports or
  calls a consumer (kb is one of them; the import ban is enforced with `uv run lint-imports`).
- `contract/v1/` is the single source of the package schemas. Contract v1 only gains fields; a changed field
  means v2.
- What needs only the file belongs here; what needs a consumer's type configuration or domain knowledge does not.
- No sample-specific logic in `src/`: rules for one document series live in font profiles, and the profiles used
  by the regression tests live under `tests/regression/`.

### Delegation: subagents and workflows

1. **Split of work.** The main session owns planning, shared state (the package contract, pinned dependencies,
   model caches), decisions, integration, final verification and the report. Subagents only take bounded work
   that can be done independently.
2. **Good to delegate:** code review of each PR before merge, by a subagent that did not write the code;
   independent modules in parallel, each in its own git worktree and integrated by the main session; fact
   checking, writing missing tests, checking documentation.
3. **Not delegated:** changing `contract/`, pinned versions in `uv.lock`, the model cache, or anything that needs
   the user's decision. Only one actor writes shared state at a time.
4. **Workflows** are for fixed, parallel or repeated procedures (regression runs, PR review).
5. **Concurrency:** at most 4 subagents at a time. Use subscription quota; no batch model calls.
6. **Verification:** the main session re-checks a subagent's conclusions (re-runs tests, samples evidence) and
   never takes them on trust. Every delegation is registered in Tallyback with task, constraints, verification
   and settlement.
7. **Reporting:** each round's report lists the delegations: who did what, under which constraints, how it was
   verified, and the conclusion.

### Stability

- Pinned versions (`uv.lock`, and `constraints.txt` generated from it) are part of the output contract: Docling
  packages, PyMuPDF and the inference libraries are pinned as a set (intake-design §9.3). An upgrade goes through
  the regression gate (`tests/regression/`), never silently.
- No generative model is called while building a package. Model artefacts are read from local files only.
- Secrets (API keys) live in environment variables only, never in files.
