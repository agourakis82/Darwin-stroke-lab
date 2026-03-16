# Ceph CSI Auth Plan

This file fixes the M1 Ceph CSI auth shape against the audited external Ceph cluster.

## Audited Ceph values

- `clusterID`: `f591bae9-eec5-4ae0-abfe-466ed7528c9e`
- monitors:
  - `10.100.100.2:3300`
  - `10.100.100.4:3300`
  - `10.100.100.3:3300`
  - `10.100.100.2:6789`
  - `10.100.100.4:6789`
  - `10.100.100.3:6789`
- filesystem: `cephfs`
- metadata pool: `cephfs_metadata`
- data pool: `cephfs_data`
- RBD pool: `rbd_ssd`
- planned CephFS subvolume group for CSI: `csi`

## Secret material required

CephFS secret:

- secret name: `ceph-csi-cephfs-secret`
- namespace: `storage-system`
- exact keys:
  - `adminID`
  - `adminKey`
  - `userID`
  - `userKey`

RBD secret:

- secret name: `ceph-csi-rbd-secret`
- namespace: `storage-system`
- exact keys:
  - `userID`
  - `userKey`

M1 minimal secret policy:

- the same CephFS identity may be used for `adminID` and `userID` in M1
- the same RBD identity may be used for both provisioner and node-stage operations in M1
- all secret values must use the Ceph user id without the `client.` prefix

## Minimal CephX identities

CephFS:

- cephx identity name: `client.csi-cephfs`
- purpose: create, expand, delete, and mount CephFS CSI-backed subvolumes
- scope:
  - filesystem: `cephfs`
  - subvolume group: `csi`
  - pools: `cephfs_metadata`, `cephfs_data`

Minimum functional capability shape:

- monitor read access
- manager read/write access for CephFS subvolume lifecycle
- MDS read/write access scoped to the CSI-managed path
- OSD read/write access scoped to the CephFS pools

RBD:

- cephx identity name: `client.csi-rbd`
- purpose: create, map, expand, and delete CSI-backed RBD images
- scope:
  - pool: `rbd_ssd`

Minimum functional capability shape:

- monitor `profile rbd`
- manager `profile rbd pool=rbd_ssd`
- OSD `profile rbd pool=rbd_ssd`

## Preparation tasks before apply

- mint `client.csi-cephfs` on the external Ceph cluster
- mint `client.csi-rbd` on the external Ceph cluster
- prepare the CephFS subvolume group `csi`
- fill [`secret-template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/secret-template.yaml)
- verify that both identities can be used from Kubernetes nodes that reach `10.100.100.0/24`
