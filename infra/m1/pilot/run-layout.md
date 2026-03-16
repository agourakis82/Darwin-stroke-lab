# CephFS Run Layout

Each workspace gets a durable run root inside the mounted CephFS volume:

```text
/workspace
└── .lab
    └── runs
        └── <run_id>
            ├── artifacts
            │   ├── context
            │   │   └── workspace-context.json
            │   ├── task
            │   │   └── agent-output.json
            │   └── summary
            │       └── resume-summary.md
            ├── checkpoints
            │   ├── step-1.json
            │   └── step-2.json
            └── logs
                ├── step-2.stdout.log
                └── step-2.stderr.log
```

Rules:

- checkpoints are durable and step-scoped
- artifacts are human-consumable first, machine-consumable second
- logs are append-only outputs, not authoritative state
- the resume summary points back to the artifacts and recent events instead of duplicating them
