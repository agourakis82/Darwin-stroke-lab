# Fill Inputs: Slice 01

Enter only these values for slice 01.

## Values to enter

| File | Field | Enter this exact value |
|---|---|---|
| [`ceph-csi/secret-template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/secret-template.yaml) | `adminKey` under `ceph-csi-cephfs-secret` | CephX key for `client.csi-cephfs` |
| [`ceph-csi/secret-template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/secret-template.yaml) | `userKey` under `ceph-csi-cephfs-secret` | the same CephX key for `client.csi-cephfs` |
| [`ceph-csi/secret-template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/secret-template.yaml) | `userKey` under `ceph-csi-rbd-secret` | CephX key for `client.csi-rbd` |

Do not change these frozen values:

- `adminID: csi-cephfs`
- `userID: csi-cephfs`
- `userID: csi-rbd`
- Ceph FSID, monitor IPs, CephFS name, CephFS data pool, CephFS subvolume group, and RBD pool

## Validate the edits

Run:

```bash
./infra/m1/PRE_FLIGHT_SLICE_01.sh
```

Binary pass criteria:

- script exits `0`
- no `REPLACE_WITH_` markers remain in [`ceph-csi/secret-template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/secret-template.yaml)
- `adminKey` and CephFS `userKey` are both non-empty and equal
- RBD `userKey` is non-empty
- manifest references are internally consistent
