# Generic Agent Pilot Workflow

The pilot workflow is intentionally small but exercises the entire resume contract.

## Trigger

- the user runs `labctl run submit` from inside the Coder workspace

## Steps

1. `capture_workspace_context`
   - capture workspace path, repo path, task name, and top-level repo entries
   - persist a checkpoint before moving on
2. `run_agent_task`
   - produce a durable task artifact, stdout log, stderr log, and a checkpoint
   - emit ordered run events while the step is active
3. `write_final_summary`
   - produce a reconnect-oriented summary with artifact pointers and next actions

## Acceptance

- browser or SSH disconnect does not cancel the run
- workspace restart preserves `/workspace`
- worker restart does not lose the workflow state
- `labctl run resume <run_id>` returns enough context to continue work immediately
