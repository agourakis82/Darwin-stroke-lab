# First Live Execution Sequence

This sequence starts only after [`PRE_APPLY_CHECKLIST.md`](/Users/demetriosagourakis/Documents/New project/infra/m1/PRE_APPLY_CHECKLIST.md) is fully green.

Assumed already true:

- K3s HA foundation is in place
- base namespaces and priority classes are applied
- internal CA and ingress certificates are ready
- all secret templates have been filled with real values

## 1. Ceph CSI

Apply:

- [`ceph-csi/ceph-csi-configmap.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/ceph-csi-configmap.yaml)
- filled [`ceph-csi/secret-template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/secret-template.yaml)
- install the CephFS CSI driver with [`ceph-csi/cephfs-values.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/cephfs-values.yaml)
- install the RBD CSI driver with [`ceph-csi/rbd-values.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/rbd-values.yaml)
- [`ceph-csi/storageclasses.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/storageclasses.yaml)

Gate:

- both `proxmox-cephfs` and `proxmox-ceph-rbd` exist
- CSI pods are `Running`

## 2. PVC test

Run:

- one CephFS PVC test in the workspace/storage path
- one RBD PVC test in the service/storage path

Gate:

- both PVCs bind successfully
- one test pod can mount each PVC type

## 3. PostgreSQL

Apply:

- [`postgres/operator.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/postgres/operator.yaml)
- filled [`postgres/bootstrap-secrets.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/postgres/bootstrap-secrets.template.yaml)
- [`postgres/cluster.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/postgres/cluster.yaml)

Gate:

- `lab-postgres-rw.postgres.svc.cluster.local:5432` is reachable
- `coder`, `temporal`, `temporal_visibility`, and `lab_runs` databases are usable

## 4. NATS

Apply:

- [`nats/helmchart.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/nats/helmchart.yaml)
- [`nats/streams.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/nats/streams.yaml)

Gate:

- NATS pods are `Running`
- JetStream is enabled
- the `RUN_EVENTS` stream exists and can replay

## 5. Temporal

Apply:

- [`temporal/helmchart.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/temporal/helmchart.yaml)
- [`temporal/workers.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/temporal/workers.yaml)

Gate:

- Temporal frontend is reachable at `temporal-frontend.orchestration.svc.cluster.local:7233`
- worker pods are `Running`
- a test workflow can start

## 6. Publish images

Apply first:

- [`registry/registry.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/registry/registry.template.yaml)

Then push:

- `registry.lab.internal/sounio/workspace:toolchain-v1`
- `registry.lab.internal/sounio/lab-runner:toolchain-v1`

Source:

- workspace image seed comes from the `t560` Podman toolchain image
- `t560` remains seed only, not the canonical runtime

Gate:

- `https://registry.lab.internal` is reachable inside the M1 network contract
- service-plane nodes can pull the runner image
- workspace-plane nodes can pull the workspace image

## 7. Coder

Apply:

- [`coder/helmchart.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/coder/helmchart.yaml)
- [`coder/rbac.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/coder/rbac.yaml)
- register the template from [`coder/template-sounio/main.tf`](/Users/demetriosagourakis/Documents/New project/infra/m1/coder/template-sounio/main.tf)

Gate:

- `https://coder.lab.internal` is reachable
- a workspace starts successfully
- `/workspace` is backed by `proxmox-cephfs`

## 8. Acceptance drill

Run exactly this acceptance:

1. submit a run from the Coder workspace
2. disconnect the client
3. confirm the run continues
4. reconnect
5. fetch `ResumeSummary`
6. confirm no duplicate completion occurred

Final M1 gate:

- the acceptance drill passes exactly once without manual repair
