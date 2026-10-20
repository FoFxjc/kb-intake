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

### Boundaries (D49)

- kb-intake never imports kb (FoFxjc/kb), and kb never imports kb-intake. The two repositories are siblings,
  never nested, never a submodule or subtree. They meet only through intake packages written to a directory
  or zip (`KB_PACKAGES_DIR` in development) and validated against `contract/v1/`.
- `contract/v1/` is the single source of the package schemas. kb keeps a copy with the source commit noted;
  contract v1 only gains fields, a changed field means v2.
- Rule of thumb from intake-design §2: what needs only the file belongs here; what needs kb's type configuration
  or domain packs belongs to kb.
- The import ban is enforced with import-linter: `uv run lint-imports`.

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

- Pinned versions (`uv.lock`) are part of the output contract: `pymupdf` must stay at the version kb used for
  its baseline; Docling packages are pinned as a set (intake-design §9.3). An upgrade goes through the regression
  gate, never silently.
- No generative model is called while building a package. Model artefacts are read from local files only.
- Secrets (API keys) live in environment variables only, never in files.
