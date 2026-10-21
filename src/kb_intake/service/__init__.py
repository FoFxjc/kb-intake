"""`kb-intake serve` (intake-design §3.2): HTTP API + web UI in one process.

Jobs run one at a time in a background thread of the same process (no queue, no worker process): a submission
while a job runs is refused with 409. Local state is one SQLite file (`<data>/intake.db`); packages go to
`<data>/packages`, uploads to `<data>/uploads`, the model-output cache to `<data>/cache` unless the configuration
says otherwise. The UI only uses the API; kb-intake never calls a consumer.
"""
