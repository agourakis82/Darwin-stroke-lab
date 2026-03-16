# First Acceptance Drill

Goal:

Prove the first real M1 user story:

`run starts -> client disconnects -> run continues -> reconnect returns ResumeSummary`

## Preconditions

- K3s HA is healthy through the API VIP
- `ceph-csi` is installed and both storage classes provision successfully
- CloudNativePG, NATS, Temporal, and Coder are healthy
- the Sounio workspace template launches successfully
- the pilot run API and worker path are reachable

## Drill steps

1. Open a Sounio workspace in Coder and enter `/workspace/src`.
2. Start a pilot run:
   ```bash
   labctl --api-base https://coder.lab.internal run submit --workspace-path /workspace --repo-path /workspace/src --task-name inventory-workspace
   ```
3. Record the returned `run_id`.
4. Confirm the run reaches `running`:
   ```bash
   labctl --api-base https://coder.lab.internal run resume <run_id>
   ```
   Expectation:
   - status is `running` or later
   - current step is present
   - recent events are populated
5. Simulate client loss by closing the browser tab or terminating the interactive client session for at least several minutes.
   Do not delete the workspace or stop cluster workloads.
6. While disconnected, verify from an operator session that:
   - the run remains active or completes
   - new JetStream events continue to appear
   - Temporal workflow state remains live
   - artifacts/checkpoints continue to land on CephFS
7. Reconnect to Coder and reopen the same workspace.
8. Fetch the run summary:
   ```bash
   labctl --api-base https://coder.lab.internal run resume <run_id>
   ```
9. Verify:
   - the command returns a `ResumeSummary`
   - `workspace_uri` still points to the same durable workspace
   - `recent_events` shows progress during the disconnected period
   - `recent_artifacts` includes durable outputs or checkpoints
   - if the run completed, `last_completed_step` is terminal and no duplicate completion exists

## Pass criteria

- the run is not canceled by client disconnect
- reconnect lands back in the same workspace state
- `ResumeSummary` is sufficient to continue work immediately
- no duplicate completed step or duplicate terminal run completion is observed

## Fail criteria

- reconnect lands in a fresh or empty workspace
- the run stops solely because the client disconnected
- `ResumeSummary` cannot be generated after reconnect
- duplicate terminal step completion or duplicate final completion is visible
