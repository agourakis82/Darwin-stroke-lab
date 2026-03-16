# Fill Sequence

Fill the remaining files in this order. Do not skip ahead.

## 1. Control-plane identity

Files:

- [`k3s/ha/control-plane-identity.input.example.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/k3s/ha/control-plane-identity.input.example.yaml)
- [`k3s/ha/server-config.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/k3s/ha/server-config.yaml)
- [`k3s/ha/kube-vip-daemonset.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/k3s/ha/kube-vip-daemonset.yaml)

Fill now:

- `api_vip`
- shared K3s token placeholder
- `tls-san` VIP placeholder
- kube-vip `address`
- kube-vip `vip_interface` if it differs from `vmbr0`

Stop condition:

- `k3s-api.lab.internal` and the chosen VIP are both final.

## 2. Internal CA and ingress certs

Files:

- [`bootstrap/internal-ca-root-secret.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/bootstrap/internal-ca-root-secret.template.yaml)
- [`bootstrap/internal-ca-clusterissuer.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/bootstrap/internal-ca-clusterissuer.template.yaml)
- [`bootstrap/m1-ingress-certificates.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/bootstrap/m1-ingress-certificates.template.yaml)

Fill now:

- CA cert PEM
- CA key PEM
- confirm `coder.lab.internal`
- confirm `*.coder.lab.internal`
- confirm `registry.lab.internal`

Stop condition:

- all TLS materials are real and no placeholder PEM blocks remain.

## 3. Ceph CSI secrets

Files:

- [`ceph-csi/secret-template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/secret-template.yaml)
- [`ceph-csi/CSI_AUTH_PLAN.md`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/CSI_AUTH_PLAN.md)

Fill now:

- CephFS key for `csi-cephfs`
- RBD key for `csi-rbd`

Verify against frozen values:

- `clusterID=f591bae9-eec5-4ae0-abfe-466ed7528c9e`
- monitors on `10.100.100.2`, `10.100.100.4`, `10.100.100.3`
- `fsName=cephfs`
- `pool=cephfs_data`
- `subvolumeGroup=csi`
- `rbd pool=rbd_ssd`

Stop condition:

- both CSI secrets are real and match the frozen user names.

## 4. PostgreSQL bootstrap secrets

Files:

- [`postgres/bootstrap-secrets.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/postgres/bootstrap-secrets.template.yaml)

Fill now:

- bootstrap owner password
- `coder` DB password
- `temporal` DB password
- `lab_runs` DB password

Stop condition:

- all four secrets have real values and no `REPLACE_WITH_` strings remain.

## 5. Registry manifest

Files:

- [`registry/registry.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/registry/registry.template.yaml)
- [`registry/README.md`](/Users/demetriosagourakis/Documents/New project/infra/m1/registry/README.md)

Confirm now:

- hostname remains `registry.lab.internal`
- TLS secret remains `registry-internal-tls`
- PVC size remains `100Gi` or is intentionally changed before first apply

Stop condition:

- registry manifest matches the frozen hostname and chosen persistence size.
