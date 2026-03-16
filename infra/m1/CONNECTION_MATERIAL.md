# Connection Material Checklist

This file is now evidence-backed from the read-only SSH audit captured on `2026-03-16`.

Audit evidence:

- Raw outputs: [`infra/m1/audit/20260316-135532`](/Users/demetriosagourakis/Documents/New project/infra/m1/audit/20260316-135532)
- Node audits: [`t560-proxmox-root.txt`](/Users/demetriosagourakis/Documents/New project/infra/m1/audit/20260316-135532/nodes/t560-proxmox-root.txt), [`r770-proxmox.txt`](/Users/demetriosagourakis/Documents/New project/infra/m1/audit/20260316-135532/nodes/r770-proxmox.txt), [`r740-proxmox.txt`](/Users/demetriosagourakis/Documents/New project/infra/m1/audit/20260316-135532/nodes/r740-proxmox.txt), [`5860-proxmox.txt`](/Users/demetriosagourakis/Documents/New project/infra/m1/audit/20260316-135532/nodes/5860-proxmox.txt)
- Cluster audits: [`ceph-status.txt`](/Users/demetriosagourakis/Documents/New project/infra/m1/audit/20260316-135532/cluster/ceph-status.txt), [`k3s-status.txt`](/Users/demetriosagourakis/Documents/New project/infra/m1/audit/20260316-135532/cluster/k3s-status.txt), [`dns-k3s-hints.txt`](/Users/demetriosagourakis/Documents/New project/infra/m1/audit/20260316-135532/cluster/dns-k3s-hints.txt), [`r770-k3s-agent-hints.txt`](/Users/demetriosagourakis/Documents/New project/infra/m1/audit/20260316-135532/cluster/r770-k3s-agent-hints.txt)

Important framing:

- `M0.5` remains the local reference implementation of resume semantics.
- `M1` is the distributed cluster-backed runtime we are preparing here.
- The `t560` Podman image remains fallback/template seed only, not the canonical cluster habitat.

## Real node names and observed networks

Observed cluster members from Proxmox quorum:

- `t560-proxmox`
- `r770-proxmox`
- `r740-proxmox`
- `5860-proxmox`

Observed per-node network reality:

| Node | Management plane | Storage plane | Batch plane | Observed running services |
|---|---|---|---|---|
| `t560-proxmox` | `vmbr0` -> `192.168.3.169/24`, MTU `1500` | `vmbr100` -> `10.100.100.2/24`, MTU `9000` | `vmbr200` -> `10.200.0.2/24`, MTU `9000` | `ceph-mon`, `ceph-mgr`, `ceph-mds`, `ceph-osd`, `k3s`, `slurmctld`, `slurmd` |
| `r770-proxmox` | `vmbr0` -> `192.168.3.228/24`, MTU `1500` | `vmbr2` -> `10.100.100.1/24`, MTU `9000` | `vmbr1` -> `10.200.0.1/24`, MTU `9000` | `ceph-mds`, `ceph-osd`, `k3s-agent` |
| `r740-proxmox` | `vmbr0` -> `192.168.3.168/24`, MTU `1500` | `vmbr100` -> `10.100.100.4/24`, MTU `9000` | `vmbr200` -> `10.200.0.4/24`, MTU `9000` | `ceph-mon`, `ceph-mgr`, `ceph-osd`, `slurmd` |
| `5860-proxmox` | `vmbr0` -> `192.168.3.207/24`, MTU `1500` | `vmbr100g` -> `10.100.100.3/24`, MTU `9000` | `vmbr200` -> `10.200.0.3/24`, MTU `1500` | `ceph-mon`, `ceph-mgr`, `ceph-osd` |

Evidence-backed shared segments:

- Management segment: `192.168.3.0/24` on `vmbr0` across all four nodes.
- Storage/Ceph segment: `10.100.100.0/24` across all four nodes.
- Batch/HPC segment: `10.200.0.0/24` across all four nodes.
- Network note: `5860-proxmox` has `vmbr200` at MTU `1500` while the other nodes expose the batch segment at MTU `9000`.

Placement reality today:

- Current live K3s server: `t560-proxmox`
- Current live K3s agent: `r770-proxmox`
- `r740-proxmox` and `5860-proxmox` are not joined to the current K3s cluster

## Control-plane VIP

Evidence-backed current state:

- There is no verified control-plane VIP today.
- The only verified live K3s control-plane node is `t560-proxmox` at `192.168.3.169`.
- The current kubeconfig on `t560-proxmox` points to `https://127.0.0.1:6443`, not to a shared VIP.
- All candidate nodes do share the same management segment, so a future VIP can plausibly live on `192.168.3.0/24`.

Still required before apply:

- `K3S_API_VIP`
- `VIP_INTERFACE`
- explicit selection of the three HA control-plane nodes

Evidence-backed constraint:

- If the Proxmox/Ceph nodes are reused for the K3s HA slice, the obvious shared VIP-reachable interface is `vmbr0`.

## DNS

Evidence-backed current state:

- Host FQDNs currently resolve under `.local`, for example `t560-proxmox.local` and `r770-proxmox.local`.
- `t560-proxmox` uses:
  - `search local`
  - `nameserver 192.168.3.1`
  - `nameserver 8.8.8.8`
  - `nameserver 1.1.1.1`
- `r770-proxmox` currently uses Tailscale-managed DNS:
  - `nameserver 100.100.100.100`
  - `search tail21cbc4.ts.net local`
- `/etc/hosts` on `t560-proxmox` contains static entries for the four node hostnames and their management/storage IPs.
- No evidence was found for:
  - `k3s-api.lab.internal`
  - `coder.lab.internal`
  - `*.coder.lab.internal`
  - `temporal.lab.internal`
  - `registry.lab.internal`

Still required before apply:

- the actual internal zone name
- `k3s-api` A/AAAA record or equivalent internal DNS record
- `coder` and wildcard Coder DNS records
- `temporal` and `registry` records if they will be externally reachable in M1

## TLS / cert assumptions

Evidence-backed current state:

- No cert issuer, wildcard certificate, or ingress TLS secret path was evidenced by the audit.

Still required before apply:

- issuer choice
- coverage for `k3s-api`, `coder`, `*.coder`, and any exposed `temporal` endpoint
- whether the cluster uses an internal CA, DNS-01, or pre-provisioned TLS secrets

## External Ceph monitor endpoints

Evidence-backed values:

- `PROXMOX_CEPH_FSID=f591bae9-eec5-4ae0-abfe-466ed7528c9e`
- `CEPH_MON_1=10.100.100.2`
- `CEPH_MON_2=10.100.100.4`
- `CEPH_MON_3=10.100.100.3`
- `CEPH_MON_PORTS=v2:3300 and v1:6789`
- `CEPHFS_NAME=cephfs`
- `CEPHFS_METADATA_POOL=cephfs_metadata`
- `CEPHFS_DATA_POOL=cephfs_data`
- `RBD_POOL=rbd_ssd`
- Additional non-primary RBD pool present: `archive_hdd`

Observed Ceph placement:

- Mon quorum: `t560-proxmox`, `r740-proxmox`, `5860-proxmox`
- Mgr active: `t560-proxmox`
- MDS active/standby present
- OSDs are distributed across all four nodes

Health note from audit:

- Ceph is reachable and functional, but `ceph -s` is `HEALTH_WARN` due to minor degraded/remapped objects and scrub timing.

Where it is used:

- [`ceph-csi/ceph-csi-configmap.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/ceph-csi-configmap.yaml)
- [`ceph-csi/storageclasses.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/storageclasses.yaml)

## CSI connection material

Evidence-backed current state:

- The storage authority is external Proxmox/Ceph and is reachable on the `10.100.100.0/24` storage network.
- FSID, monitor endpoints, CephFS name, CephFS pools, and RBD pool are now known.

Intentionally not collected in the read-only audit:

- `CEPHFS_KEY`
- `RBD_KEY`

Still required before apply:

- `CEPHFS_USER`
- `RBD_USER`
- the actual CephX keys for those identities
- confirmation that those identities have the minimum caps required by `ceph-csi`

Evidence status:

- No safe, evidence-backed `ceph-csi` client identity or key material was collected during this audit.

Where it is used:

- [`ceph-csi/secret-template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/secret-template.yaml)

## Current K3s / Kubernetes state

Evidence-backed current state:

- Current K3s nodes:
  - `t560-proxmox` -> `Ready`, `control-plane`, `192.168.3.169`
  - `r770-proxmox` -> `Ready`, worker, `192.168.3.228`
- Version: `v1.34.5+k3s1`
- Current cluster is not yet HA.
- Current cluster has Cilium running.
- No Kubernetes Ingress resources exist yet.
- `t560-proxmox` runs `k3s server` with:
  - `--flannel-backend=none`
  - `--disable-network-policy`
  - `--disable=traefik`
- `r770-proxmox` runs `k3s-agent` and has `default-runtime: nvidia`

Implication for M1:

- The current K3s install is a useful live substrate reference, but it is not yet the intended HA M1 runtime.

## PostgreSQL bootstrap secrets

Evidence-backed current state:

- No live CloudNativePG deployment or PostgreSQL bootstrap secret material was evidenced in the current cluster audit.

Still required before apply:

- `lab-postgres-app` secret contents
- `coder-db-url`
- `temporal-db-url`
- `lab-runs-db-url`
- password and URL shape expected by each chart or deployment

Decision still required:

- whether secrets will be managed by External Secrets, Sealed Secrets, or a manual bootstrap path

## Image registry and image names

Evidence-backed current state:

- No cluster-reachable image registry hostname was evidenced in the current audit.
- No Kubernetes pull secret path was evidenced in the current audit.
- The only currently proven Sounio toolchain image is the host-local Podman seed on `t560`:
  - `localhost/sounio-dev:toolchain-v1`

Implication:

- `localhost/sounio-dev:toolchain-v1` is not pullable by cluster nodes and cannot serve as the final Coder workspace image without being published to a reachable registry.

Still required before apply:

- reachable registry hostname
- published workspace image name
- published Temporal worker / lab runner image name
- pull secret strategy if the registry is private

## Coder access assumptions

Evidence-backed current state:

- No Coder deployment exists yet.
- No Coder ingress exists yet.
- No wildcard workspace DNS exists yet.

Still required before apply:

- `CODER_ACCESS_URL`
- `CODER_WILDCARD_ACCESS_URL`
- auth provider choice
- `coder-oauth` secret contents if external auth is required in the first live slice

## Ready-to-apply gate

Current status from evidence:

- `NO-GO` for `kubectl apply` / Helm install of the first live M1 slice.

Ready enough to proceed with pre-apply generation:

- real node names
- management and storage network inventory
- live K3s baseline inventory
- external Ceph FSID, monitor endpoints, CephFS name, and pool names

Still missing before first apply:

- HA control-plane VIP
- lab DNS records
- TLS issuer/cert plan
- Ceph CSI client identities and keys
- PostgreSQL bootstrap secrets
- cluster-reachable registry and published images
