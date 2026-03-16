# Idempotency Boundaries

## Safe-to-replay operations

- `GET /runs/{run_id}`
- `GET /runs/{run_id}/events`
- `GET /runs/{run_id}/resume-summary`
- event-stream replay from JetStream consumers
- recalculating a `ResumeSummary` from durable state
- re-reading CephFS artifacts and checkpoints
- Temporal activity retries before a step reaches `completed`

## Terminal operations

These transitions must happen once per `(run_id, step.seq)` or `(run_id)`:

- `StepStatus.completed`
- `StepStatus.failed`
- `RunStatus.completed`
- `RunStatus.failed`
- final completion event emission

## Duplicate-completion prevention

M1 should prevent duplicate completion with three guards:

1. deterministic identity
   Use stable keys: `(run_id)`, `(run_id, step.seq)`, and deterministic artifact paths under `.lab/runs/<run_id>/`.
2. compare-and-set state transitions
   Only allow `pending -> running -> completed|failed`. Once a step is `completed`, later retries must no-op.
3. completion after durable writes
   Persist checkpoint/artifact bytes first, then update metadata, then emit the completion event.

## Practical policy for M1

- step start may replay safely only if the step is still `pending` or `running`
- step body may retry safely only if outputs are written to deterministic paths or keyed by content hash
- completion writes must check current metadata state before writing terminal status
- consumers should dedupe completion by `(run_id, step.seq, event_type)` and ignore duplicate terminal events

## Current M0.5 status

The local reference implementation already keeps completed steps immutable in practice by running a single worker thread per run. M1 must harden this with database-level compare-and-set semantics and JetStream/Temporal consumer dedupe.
