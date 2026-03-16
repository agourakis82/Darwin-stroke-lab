# Operator Worksheet

Fill this worksheet before the first live apply. Keep the values out of git if they contain secrets.

## Manual inputs still required

| Input | Frozen context | Operator value |
|---|---|---|
| K3s API VIP | must be one free IP on `192.168.3.0/24` | `________________` |
| Internal CA cert PEM | goes into [`bootstrap/internal-ca-root-secret.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/bootstrap/internal-ca-root-secret.template.yaml) | `provided out of band` |
| Internal CA key PEM | goes into [`bootstrap/internal-ca-root-secret.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/bootstrap/internal-ca-root-secret.template.yaml) | `provided out of band` |
| CephFS CSI key | for `client.csi-cephfs` / user id `csi-cephfs` | `________________` |
| RBD CSI key | for `client.csi-rbd` / user id `csi-rbd` | `________________` |
| PostgreSQL bootstrap owner password | secret `lab-postgres-app` | `________________` |
| PostgreSQL password for `coder` | secret `coder-db-url` | `________________` |
| PostgreSQL password for `temporal` | secret `temporal-db-url` | `________________` |
| PostgreSQL password for `lab_runs` | secret `lab-runs-db-url` | `________________` |

## Frozen values already fixed

| Item | Frozen value |
|---|---|
| Internal zone | `lab.internal` |
| K3s API hostname | `k3s-api.lab.internal` |
| Coder hostname | `coder.lab.internal` |
| Coder wildcard hostname | `*.coder.lab.internal` |
| Registry hostname | `registry.lab.internal` |
| Temporal external UI | `not exposed in M1` |
| Ceph FSID | `f591bae9-eec5-4ae0-abfe-466ed7528c9e` |
| Ceph monitors | `10.100.100.2`, `10.100.100.4`, `10.100.100.3` |
| CephFS name | `cephfs` |
| CephFS data pool | `cephfs_data` |
| CephFS subvolume group | `csi` |
| RBD pool | `rbd_ssd` |
| Workspace image | `registry.lab.internal/sounio/workspace:toolchain-v1` |
| Runner image | `registry.lab.internal/sounio/lab-runner:toolchain-v1` |

## Files to update from this worksheet

- [`k3s/ha/control-plane-identity.input.example.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/k3s/ha/control-plane-identity.input.example.yaml)
- [`k3s/ha/server-config.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/k3s/ha/server-config.yaml)
- [`k3s/ha/kube-vip-daemonset.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/k3s/ha/kube-vip-daemonset.yaml)
- [`bootstrap/internal-ca-root-secret.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/bootstrap/internal-ca-root-secret.template.yaml)
- [`ceph-csi/secret-template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/secret-template.yaml)
- [`postgres/bootstrap-secrets.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/postgres/bootstrap-secrets.template.yaml)

## Stop gate

Do not start slice 01 until:

- every blank above has a real value
- the updated files contain no remaining `REPLACE_WITH_` markers for the values used by slice 01
