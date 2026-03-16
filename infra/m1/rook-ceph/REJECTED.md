# Rejected Default For M1

These manifests are retained only as superseded exploratory scaffolding.

They are **not** the default M1 storage path because:

- Proxmox/Ceph already exists as the storage substrate
- Kubernetes must consume storage, not create a competing storage authority
- the default M1 path is `infra/m1/ceph-csi/` with external Ceph

Do not apply `infra/m1/rook-ceph/` as the default deployment path.
