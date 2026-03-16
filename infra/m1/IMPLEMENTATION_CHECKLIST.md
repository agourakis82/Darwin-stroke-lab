# M1 Implementation Checklist

Supporting references:

- [`M1_NETWORK_CONTRACT.md`](/Users/demetriosagourakis/Documents/New project/infra/m1/M1_NETWORK_CONTRACT.md)
- [`CONNECTION_MATERIAL.md`](/Users/demetriosagourakis/Documents/New project/infra/m1/CONNECTION_MATERIAL.md)
- [`FIRST_LIVE_SLICE.md`](/Users/demetriosagourakis/Documents/New project/infra/m1/FIRST_LIVE_SLICE.md)
- [`APPLY_TIME_RISKS.md`](/Users/demetriosagourakis/Documents/New project/infra/m1/APPLY_TIME_RISKS.md)
- [`ACCEPTANCE_DRILL.md`](/Users/demetriosagourakis/Documents/New project/infra/m1/ACCEPTANCE_DRILL.md)

## Slice 1: K3s HA foundation

- [ ] Reserve three K3s server nodes and one API VIP.
- [ ] Install K3s with the shared config in [`k3s/ha/server-config.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/k3s/ha/server-config.yaml).
- [ ] Apply [`k3s/ha/kube-vip-daemonset.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/k3s/ha/kube-vip-daemonset.yaml).
- [ ] Apply [`bootstrap/namespaces.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/bootstrap/namespaces.yaml) and [`bootstrap/priorityclasses.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/bootstrap/priorityclasses.yaml).
- [ ] Label and taint nodes per [`bootstrap/node-labels.md`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/bootstrap/node-labels.md).
- Acceptance gate: losing any one server node does not take down the Kubernetes API.

## Slice 2: External Ceph consumption via ceph-csi

- [ ] Fill in external Ceph monitor and fsid details in [`ceph-csi/ceph-csi-configmap.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/ceph-csi/ceph-csi-configmap.yaml).
- [ ] Create CSI secrets from [`ceph-csi/secret-template.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/ceph-csi/secret-template.yaml).
- [ ] Install the CephFS and RBD CSI drivers using [`ceph-csi/cephfs-values.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/ceph-csi/cephfs-values.yaml) and [`ceph-csi/rbd-values.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/ceph-csi/rbd-values.yaml).
- [ ] Apply [`ceph-csi/storageclasses.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/ceph-csi/storageclasses.yaml).
- Acceptance gate: a CephFS PVC and an RBD PVC both bind and survive pod recreation against the external Proxmox/Ceph substrate.

## Slice 3: Shared service persistence

- [ ] Install CloudNativePG from [`postgres/operator.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/postgres/operator.yaml).
- [ ] Apply [`postgres/cluster.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/postgres/cluster.yaml).
- [ ] Create databases and app roles from [`postgres/databases.sql`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/postgres/databases.sql).
- [ ] Provision secrets per [`secrets/README.md`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/secrets/README.md).
- Acceptance gate: failover works and all three application databases are reachable.

## Slice 4: Durable run substrate

- [ ] Install NATS via [`nats/helmchart.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/nats/helmchart.yaml) and [`nats/helm-values.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/nats/helm-values.yaml).
- [ ] Apply [`nats/streams.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/nats/streams.yaml).
- [ ] Install Temporal via [`temporal/helmchart.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/temporal/helmchart.yaml) and [`temporal/helm-values.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/temporal/helm-values.yaml).
- [ ] Apply [`temporal/workers.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/temporal/workers.yaml).
- Acceptance gate: worker restart does not lose the workflow and the run event stream remains replayable.

## Slice 5: Publish workspace image + Coder

- [ ] Build and publish the workspace image from [`images/sounio-workspace/Dockerfile`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/images/sounio-workspace/Dockerfile).
- [ ] Install Coder using [`coder/helmchart.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/coder/helmchart.yaml) and [`coder/helm-values.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/coder/helm-values.yaml).
- [ ] Apply [`coder/rbac.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/coder/rbac.yaml).
- [ ] Register the template from [`coder/template-sounio/main.tf`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/coder/template-sounio/main.tf).
- Acceptance gate: restarting a workspace pod preserves `/workspace` and reconnect returns to the same workspace.

## Slice 6: Resume contract + pilot

- [ ] Review [`contracts/resume-contract.md`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/contracts/resume-contract.md) and [`contracts/openapi.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/contracts/openapi.yaml).
- [ ] Review [`SOURCE_OF_TRUTH_MATRIX.md`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/SOURCE_OF_TRUTH_MATRIX.md) and [`IDEMPOTENCY.md`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/IDEMPOTENCY.md).
- [ ] Validate the pilot flow in [`pilot/generic-agent-workflow.md`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/pilot/generic-agent-workflow.md) and the file layout in [`pilot/run-layout.md`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/pilot/run-layout.md).
- [ ] Run the local reference implementation with `labctl run submit` and `labctl run resume`.
- [ ] Execute [`ACCEPTANCE_DRILL.md`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/ACCEPTANCE_DRILL.md).
- Acceptance gate: a run survives client disconnect and reconnect returns a coherent `ResumeSummary`.
