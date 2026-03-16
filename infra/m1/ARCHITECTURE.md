# Resume-Capable Lab Architecture

## State split

- `M0.5` is the local reference implementation of resume semantics in this repository.
  It uses SQLite plus disk-backed artifacts to prove the contract, API shape, and pilot workflow.
- `M1` is the distributed cluster-backed runtime.
  It uses K3s, external Ceph consumed via `ceph-csi`, CloudNativePG, NATS JetStream, Temporal, and Coder.

`t560-proxmox` remains fallback/template seed only for the initial workspace image/toolchain. It is not the canonical habitat in either M0.5 or M1.

## Storage authority

Proxmox/Ceph is the sole storage authority for M1.

- Kubernetes must consume CephFS and RBD from the existing external Ceph substrate.
- Kubernetes must not create a competing Ceph authority by default.
- Any `rook-ceph` assets under `infra/m1/rook-ceph/` are rejected for the default M1 path and retained only as superseded exploratory scaffolding.

## Plane layout

- `control plane`: K3s servers, embedded etcd, `kube-vip`, cluster-critical add-ons only.
- `workspace plane`: Coder workspace pods only, each mounting a per-user CephFS volume at `/workspace`.
- `service plane`: `ceph-csi` controllers/node plugins, CloudNativePG, Coder control plane, Temporal, Temporal workers, NATS JetStream, ingress, cert-manager, and the resume API.
- `batch/HPC plane`: reserved for Slurm and future offload; not part of the next runnable M1 slice.

## Durable execution model

- CephFS is the durable file substrate for workspace state, checkpoints, logs, and artifacts.
- PostgreSQL is the durable metadata substrate for runs, steps, and resume summaries.
- NATS JetStream is the ordered event substrate for run events and replay.
- Temporal is the workflow orchestration substrate for retries and long-lived execution state.
- Resume summary generation is a derived read model over the durable sources above; it is never a primary source of truth.

## Updated implementation order

The first live vertical slice is tracked in [`FIRST_LIVE_SLICE.md`](/Users/demetriosagourakis/Documents/New project/infra/m1/FIRST_LIVE_SLICE.md).

1. Install `ceph-csi` for external Ceph, create `proxmox-cephfs` and `proxmox-ceph-rbd`, and validate PVC provisioning against the existing substrate.
2. Install CloudNativePG and create the shared PostgreSQL cluster plus `coder`, `temporal`, and `lab_runs` databases.
3. Install NATS with JetStream.
4. Install Temporal and the lab worker deployment.
5. Publish the Sounio workspace image from the `t560` seed image into the chosen registry.
6. Install Coder and register the template from `infra/m1/coder/template-sounio/`.
7. Validate the generic agent pilot workflow and run the acceptance drill in [`ACCEPTANCE_DRILL.md`](/Users/demetriosagourakis/Documents/New project/infra/m1/ACCEPTANCE_DRILL.md).

## Next runnable deployment slice

The next runnable slice is:

1. `infra/m1/k3s/ha/`
2. `infra/m1/bootstrap/`
3. `infra/m1/ceph-csi/`
4. `infra/m1/postgres/`
5. `infra/m1/nats/`
6. `infra/m1/temporal/`
7. `infra/m1/coder/`

This slice intentionally excludes:

- in-cluster Ceph as the default storage path
- any VM-centric runtime path
- Slurm and batch/HPC execution from the first runnable deployment

## Top 5 apply-time risks

Tracked in [`APPLY_TIME_RISKS.md`](/Users/demetriosagourakis/Documents/New project/infra/m1/APPLY_TIME_RISKS.md).

## Recommended pilot workflow

Use the `generic_agent_task` pilot from a Sounio Coder workspace:

1. Open the workspace and work under `/workspace/src`.
2. Submit a run with `labctl run submit`.
3. Let the run progress independently of the browser or SSH session.
4. Reconnect with `labctl run resume <run_id>` and inspect `.lab/runs/<run_id>/`.
