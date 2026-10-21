"""`kb-intake serve` (intake-design-v2 §6): HTTP API + static web UI in one process.

Steps run one at a time in a background thread of the same process (runner_thread); a start while a step runs is
refused with 409. State is the job store of the work directory (kb_intake.jobs, SQLite), shared with the CLI;
confirmed packages go to the delivery directory. The UI only uses the API; kb-intake never calls a consumer.
"""
