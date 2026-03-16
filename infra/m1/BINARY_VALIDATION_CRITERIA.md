# Binary Validation Criteria

These are the pass/fail gates for the first two live slices.

## Slice 01: Ceph CSI

Pass only if all are true:

- `storage-system` pods are all `Ready`
- `proxmox-cephfs` exists
- `proxmox-ceph-rbd` exists
- `m1-cephfs-smoke` PVC is `Bound`
- `m1-rbd-smoke` PVC is `Bound`
- `m1-cephfs-smoke` pod is `Ready`
- `m1-rbd-smoke` pod is `Ready`
- `cat /workspace/ok` returns `cephfs-ok`
- `cat /data/ok` returns `rbd-ok`

Fail immediately if any one is false.

## Slice 02: PostgreSQL

Pass only if all are true:

- CloudNativePG operator deployment is `Available`
- all `lab-postgres` pods are `Ready`
- all `lab-postgres` PVCs are `Bound`
- service `lab-postgres-rw` exists
- database `coder` exists
- database `temporal` exists
- database `temporal_visibility` exists
- database `lab_runs` exists
- `SELECT 1;` through `lab-postgres-rw` returns `1`

Fail immediately if any one is false.
