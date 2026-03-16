# M1 Bootstrap Blockers

This document converts the current `NO-GO` result into a fillable bootstrap package for M1.

Scope:

- no manifests are applied here
- no VM-centric runtime path is introduced
- external Proxmox/Ceph remains the storage authority
- `t560` Podman remains fallback/template seed only

## Blocker Breakdown

| Blocker group | Why it is still blocked | Exact missing inputs | Prepared next files |
|---|---|---|---|
| control-plane identity | K3s is live only on `t560` and has no shared API VIP yet | internal zone, API VIP on `192.168.3.0/24`, three server node names, shared K3s token | [`k3s/ha/control-plane-identity.input.example.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/k3s/ha/control-plane-identity.input.example.yaml), [`k3s/ha/server-config.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/k3s/ha/server-config.yaml), [`k3s/ha/kube-vip-daemonset.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/k3s/ha/kube-vip-daemonset.yaml) |
| DNS / TLS | no lab DNS exists yet and no internal CA path is fixed | internal zone, ingress DNS records, root CA material, ingress class, TLS secret names | [`bootstrap/internal-ca-root-secret.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/bootstrap/internal-ca-root-secret.template.yaml), [`bootstrap/internal-ca-clusterissuer.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/bootstrap/internal-ca-clusterissuer.template.yaml), [`bootstrap/m1-ingress-certificates.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/bootstrap/m1-ingress-certificates.template.yaml) |
| external Ceph CSI auth | Ceph substrate is known, but CSI identities and keys are not | CephFS user id/key, RBD user id/key, CephFS subvolume group | [`ceph-csi/CSI_AUTH_PLAN.md`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/CSI_AUTH_PLAN.md), [`ceph-csi/secret-template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/secret-template.yaml) |
| PostgreSQL bootstrap secrets | CNPG can be deployed, but app/user bootstrap and connection secrets are still undefined | owner password, per-app passwords, exact URLs, secret delivery path | [`postgres/bootstrap-secrets.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/postgres/bootstrap-secrets.template.yaml), [`secrets/README.md`](/Users/demetriosagourakis/Documents/New project/infra/m1/secrets/README.md) |
| internal image registry | workspace and worker images have no cluster-reachable OCI registry yet | registry hostname, TLS cert, PVC size, image naming convention | [`registry/README.md`](/Users/demetriosagourakis/Documents/New project/infra/m1/registry/README.md), [`registry/registry.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/registry/registry.template.yaml) |

## Control-Plane Identity

Required artifacts for the K3s HA VIP bootstrap:

- one shared API DNS name
- one shared API VIP on the `192.168.3.0/24` management network
- one VIP interface common to all control-plane nodes
- one shared K3s token distributed out of band
- one list of the three HA server nodes
- one copy of [`server-config.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/k3s/ha/server-config.yaml) per server
- one kube-vip DaemonSet with the final VIP/interface values

Minimum kube-vip bootstrap inputs:

- `api_dns_name`
- `api_vip`
- `vip_interface`
- `vip_network=192.168.3.0/24`
- `kube_vip_mode=arp`
- `kube_vip_image=ghcr.io/kube-vip/kube-vip:v0.8.1`
- `server_nodes`
- `k3s_shared_token_secret_name`

Evidence-backed constraints:

- keep the VIP on the management network only
- use `vmbr0` as the candidate VIP interface if the current audited nodes are chosen
- do not invent the final VIP until it is explicitly assigned

## DNS / TLS

Minimum externally reachable M1 endpoints:

- `k3s-api.<internal-zone>` for operators and automation
- `coder.<internal-zone>` for the Coder control plane
- `*.coder.<internal-zone>` for workspace routing and reattachment
- `registry.<internal-zone>` for the internal OCI registry

ClusterIP-only in M1:

- PostgreSQL
- NATS JetStream
- Temporal frontend and web
- ceph-csi controllers
- lab run API and worker backends

Preferred TLS path:

- `cert-manager`
- one internal CA `ClusterIssuer`
- one wildcard Coder certificate
- one registry certificate
- K3s API certificate SANs handled in [`server-config.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/k3s/ha/server-config.yaml), not through ingress

## External Ceph CSI Auth

Audited Ceph values already fixed:

- `PROXMOX_CEPH_FSID=f591bae9-eec5-4ae0-abfe-466ed7528c9e`
- Ceph monitors:
  - `10.100.100.2`
  - `10.100.100.4`
  - `10.100.100.3`
- `CEPHFS_NAME=cephfs`
- `CEPHFS_METADATA_POOL=cephfs_metadata`
- `CEPHFS_DATA_POOL=cephfs_data`
- `RBD_POOL=rbd_ssd`
- `CEPHFS_SUBVOLUME_GROUP=csi`

Exact secret material still required:

- `ceph-csi-cephfs-secret`
  - `adminID`
  - `adminKey`
  - `userID`
  - `userKey`
- `ceph-csi-rbd-secret`
  - `userID`
  - `userKey`

Minimal CephX roles required:

- one dedicated CephFS identity scoped to filesystem `cephfs` and subvolume group `csi`
- one dedicated RBD identity scoped to pool `rbd_ssd`
- do not reuse `client.admin`

Role shape for M1:

- `client.csi-cephfs`
  - must provision, expand, and delete CephFS subvolumes in `cephfs`
  - must be able to set the CephFS quota/layout metadata needed by CSI on the `csi` subvolume group
- `client.csi-rbd`
  - must provision, map, resize, and delete RBD images in pool `rbd_ssd`
  - should use the standard `profile rbd` capability shape scoped to `rbd_ssd`

## PostgreSQL Bootstrap Secrets

Bootstrap secret set required for M1:

- `lab-postgres-app` in namespace `postgres`
- `coder-db-url` in namespace `coder`
- `temporal-db-url` in namespace `orchestration`
- `lab-runs-db-url` in namespace `lab-system`

Recommended reproducible secret-management path:

- commit only templates to the repo
- keep real values in `sops` + `age` encrypted manifests or in an external secret store
- render the final Kubernetes `Secret` objects from a repeatable bootstrap step
- do not commit plaintext credentials

## Internal Image Registry

Simplest M1 path:

- single-replica in-cluster OCI registry
- namespace `registry`
- `ClusterIP` service plus internal ingress
- persistence on `proxmox-ceph-rbd`
- internal CA TLS

Required hostname:

- `registry.<internal-zone>`

Naming convention:

- `registry.<internal-zone>/sounio/workspace:toolchain-v1`
- `registry.<internal-zone>/sounio/lab-runner:toolchain-v1`

Auth policy for M1:

- simplest path is no auth inside the trusted internal network
- defer auth hardening until after the first live slice is proven

## Revised GO Criteria

Move from `NO-GO` to `GO` only when all of the following are true:

- a real API VIP on `192.168.3.0/24` is assigned
- `k3s-api`, `coder`, `*.coder`, and `registry` resolve in the chosen internal zone
- internal CA material exists and cert-manager templates are filled
- the CephFS and RBD CSI identities and keys exist
- the CephFS subvolume group `csi` is planned and ready to be created
- PostgreSQL bootstrap secrets exist in the chosen secret-management system
- the registry hostname, PVC size, and TLS secret name are fixed
- the workspace image and lab runner image naming convention is fixed

## Next Files To Fill

- [`k3s/ha/control-plane-identity.input.example.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/k3s/ha/control-plane-identity.input.example.yaml)
- [`bootstrap/internal-ca-root-secret.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/bootstrap/internal-ca-root-secret.template.yaml)
- [`bootstrap/internal-ca-clusterissuer.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/bootstrap/internal-ca-clusterissuer.template.yaml)
- [`bootstrap/m1-ingress-certificates.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/bootstrap/m1-ingress-certificates.template.yaml)
- [`ceph-csi/CSI_AUTH_PLAN.md`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/CSI_AUTH_PLAN.md)
- [`postgres/bootstrap-secrets.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/postgres/bootstrap-secrets.template.yaml)
- [`registry/registry.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/registry/registry.template.yaml)
