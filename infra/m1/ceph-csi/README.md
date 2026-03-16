# External Ceph Consumption

This directory is the default M1 storage path.

- source of truth: external Proxmox/Ceph
- Kubernetes role: consume storage through `ceph-csi`
- default storage classes:
  - `proxmox-cephfs` for workspaces and file artifacts
  - `proxmox-ceph-rbd` for PostgreSQL and NATS persistent state

Suggested install sequence:

1. keep the audited Ceph FSID, monitors, filesystem, and pool values from [`ceph-csi-configmap.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/ceph-csi/ceph-csi-configmap.yaml) and [`storageclasses.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/ceph-csi/storageclasses.yaml)
2. mint the CephX identities described in [`CSI_AUTH_PLAN.md`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/ceph-csi/CSI_AUTH_PLAN.md)
3. create secrets from [`secret-template.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/ceph-csi/secret-template.yaml)
4. install CephFS and RBD CSI drivers with the values files in this directory
5. apply [`storageclasses.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/ceph-csi/storageclasses.yaml)

Rejected default:

- do not stand up an in-cluster Ceph authority for M1
- do not apply `infra/m1/rook-ceph/` as the default storage path
