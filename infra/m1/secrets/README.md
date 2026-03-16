# Secrets Policy

M1 assumes secrets are created out-of-band and never committed in plaintext.

Recommended reproducible path:

- keep only templates in git
- store real values in `sops` + `age` encrypted manifests or in an external secret store
- render the final Kubernetes `Secret` objects from a repeatable bootstrap step

Required secret names:

- `lab-postgres-app` in namespace `postgres`
- `coder-db-url` in namespace `coder`
- `temporal-db-url` in namespace `orchestration`
- `lab-runs-db-url` in namespace `lab-system`
- `coder-oauth` in namespace `coder` if external auth is enabled
- `ceph-csi-cephfs-secret` in namespace `storage-system`
- `ceph-csi-rbd-secret` in namespace `storage-system`
- `nats-auth` in namespace `messaging` if JetStream auth is enabled

Template sources:

- [`postgres/bootstrap-secrets.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/postgres/bootstrap-secrets.template.yaml)
- [`ceph-csi/secret-template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/secret-template.yaml)
- [`bootstrap/internal-ca-root-secret.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/bootstrap/internal-ca-root-secret.template.yaml)

Bootstrap note:

- the local Python reference implementation in this repo does not need these secrets
- the Kubernetes deployment path does
