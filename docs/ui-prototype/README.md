# UI prototype (reference only)

Approved by Peter on 2026-10-21. The interactive version lives as a claude.ai artifact ("kb-intake UI prototype"); these are its sources.

- `Main.dc.html` - Labels & inventory
- `Workflow.dc.html` - Workflow & approval
- `Review.dc.html` - Review inbox
- `Deliver.dc.html` - Confirm & deliver

They are Design Component files (HTML markup plus a small logic class) and do not run on their own. Read them for layout, wording, states and interactions; implement the real UI in React + Vite per `docs/intake-ui-llm-design-v1.md` §5. Numbers are from the OCBC dry run; `[n]` marks values not measured yet.
