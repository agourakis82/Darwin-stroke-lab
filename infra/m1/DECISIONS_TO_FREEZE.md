# Decisions To Freeze

Freeze these values now and do not reopen them during the first M1 apply wave.

## Frozen now

| Decision | Frozen value |
|---|---|
| Internal zone | `lab.internal` |
| External API hostname | `k3s-api.lab.internal` |
| External Coder hostname | `coder.lab.internal` |
| External Coder wildcard hostname | `*.coder.lab.internal` |
| External registry hostname | `registry.lab.internal` |
| Temporal external hostname | `none for M1` |
| Workspace image tag | `toolchain-v1` |
| Runner image tag | `toolchain-v1` |
| Workspace image name | `registry.lab.internal/sounio/workspace:toolchain-v1` |
| Runner image name | `registry.lab.internal/sounio/lab-runner:toolchain-v1` |
| CephFS CSI user name | `csi-cephfs` |
| CephFS CephX identity | `client.csi-cephfs` |
| RBD CSI user name | `csi-rbd` |
| RBD CephX identity | `client.csi-rbd` |
| PostgreSQL bootstrap owner secret | `lab-postgres-app` |
| Coder PostgreSQL URL secret | `coder-db-url` |
| Temporal PostgreSQL URL secret | `temporal-db-url` |
| Lab runs PostgreSQL URL secret | `lab-runs-db-url` |

## Must still be supplied manually

| Decision | Required value |
|---|---|
| K3s API VIP | one free VIP on `192.168.3.0/24` |
| Internal CA root certificate | PEM certificate for `m1-internal-root-ca` |
| Internal CA private key | PEM private key for `m1-internal-root-ca` |
| CephFS CSI key | key for `client.csi-cephfs` |
| RBD CSI key | key for `client.csi-rbd` |
| PostgreSQL owner password | value for `lab-postgres-app` |
| Coder DB password | value for `coder-db-url` |
| Temporal DB password | value for `temporal-db-url` |
| Lab runs DB password | value for `lab-runs-db-url` |

## M1 minimum exposure

- Keep `Temporal` internal-only for M1.
- Keep `PostgreSQL` internal-only for M1.
- Keep `NATS` internal-only for M1.
- Keep `Slurm` out of the first live slice.
- Do not add extra services before the acceptance drill passes.
