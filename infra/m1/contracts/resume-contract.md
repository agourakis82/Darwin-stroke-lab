# Resume Contract

The M1 resume contract is the minimum durable surface required to survive hours of user disconnection without losing execution context.

## Durable boundaries

- `Run`, `Step`, and `ResumeSummary` are durable metadata records.
- `Artifact` is durable file-backed state referenced by metadata.
- `RunEvent` is an ordered replay stream.
- completed steps are immutable
- failed or running steps may update heartbeat, checkpoint, and resume hints

## Ordering guarantees

1. create `Run`
2. create all declared `Step` records in `pending`
3. append `run.submitted`
4. before each step starts, update `Run.current_step_seq`, mark the step `running`, and append `step.started`
5. before each step completes, persist artifacts/checkpoints first, then mark the step `completed`, then append `step.completed`
6. only after all steps complete may the run move to `completed`

## Minimal M1 workflow shape

- step 1: capture workspace context
- step 2: run the generic agent task
- step 3: write the final summary

## ResumeSummary contract

`ResumeSummary` must be small and directly useful to a human. It includes:

- current run status
- current step and last completed step
- latest heartbeat
- workspace URI
- recent artifacts
- recent events
- concrete instructions to resume from the same execution context

## Local reference vs target runtime

This repository now implements the contract locally with SQLite plus disk-backed artifacts so the API and CLI can be exercised immediately. The target M1 runtime maps the same contract to PostgreSQL, Temporal, NATS JetStream, and CephFS.
