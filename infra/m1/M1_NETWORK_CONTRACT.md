# M1 Network Contract

This document defines the minimum viable network contract required to turn the current M1 scaffold into a runnable distributed slice.

It intentionally excludes:

- service mesh
- RDMA or GPUDirect
- full Network Operator rollout
- advanced multi-networking
- any VM-centric runtime path

## Scope split

- `M0.5`: local reference semantics with no cluster network dependency beyond local process execution
- `M1`: K3s-based distributed runtime that requires a stable control-plane VIP, working ingress DNS, east-west pod connectivity, and external Ceph reachability

## Control-plane VIP path

Control-plane traffic path:

1. operator or automation targets `https://k3s-api.lab.internal:6443`
2. DNS resolves `k3s-api.lab.internal` to the shared K3s API VIP
3. `kube-vip` advertises that VIP on the control-plane L2/L3 segment
4. the VIP forwards to the active K3s API server on TCP `6443`

Minimum requirement now:

- one stable VIP on the control-plane network
- one DNS record for that VIP
- all K3s servers can advertise or receive VIP traffic on the same broadcast/routable segment

Deferred later:

- split-horizon DNS
- external load balancer
- BGP-based VIP advertisement

## Required DNS names

Required now:

- `k3s-api.lab.internal` -> K3s control-plane VIP
- `coder.lab.internal` -> ingress entrypoint for the Coder control plane
- `*.coder.lab.internal` -> wildcard ingress for Coder workspace reattachment/session routing
- `registry.lab.internal` -> internal or reachable registry for the workspace image

In-cluster service discovery relies on Kubernetes DNS and must work for:

- `lab-postgres-rw.postgres.svc.cluster.local`
- `nats.messaging.svc.cluster.local`
- `temporal-frontend.orchestration.svc.cluster.local`

Deferred later:

- public DNS
- split operator/user endpoints
- externalized auth endpoints

## Ingress endpoints

Required now:

- `https://coder.lab.internal`
  Purpose: user login, workspace launch, workspace reattachment after disconnect
- `https://*.coder.lab.internal`
  Purpose: workspace access and reconnect routing managed by Coder
- `https://registry.lab.internal`
  Purpose: internal workspace and worker image push/pull path

Not required now:

- dedicated public endpoint for NATS
- dedicated public endpoint for PostgreSQL
- external Temporal web endpoint

## East-west assumptions

Required now:

- all pods can resolve `*.svc.cluster.local`
- service-plane pods can reach each other over the cluster pod/service network
- workspace-plane pods can reach service-plane ClusterIPs for:
  - Coder control plane
  - run/resume API
  - NATS client endpoint if the pilot emits live events directly
  - Temporal frontend only if the workspace-side component needs it
- control-plane nodes can reach service-plane nodes and vice versa for cluster operations

Assumption:

- the default K3s CNI provides flat east-west connectivity without additional policy enforcement

Deferred later:

- NetworkPolicies
- dedicated storage VLANs inside Kubernetes
- traffic classes or QoS beyond node/plane separation

## Storage network assumptions

External Proxmox/Ceph is the storage authority.

Required now:

- every node that can mount workspace or service PVCs can reach all required Ceph monitor endpoints
- CSI controller pods and node plugins can reach Ceph monitors over the storage network
- CephFS and RBD clients can authenticate with the provided CSI secrets
- workspace-plane nodes can mount CephFS volumes
- service-plane nodes can mount RBD volumes for PostgreSQL and NATS persistence

Assumption:

- the Ceph monitor network is routable from Kubernetes nodes without overlay translation or NAT that breaks long-lived storage sessions

Deferred later:

- dedicated storage NICs
- jumbo frame tuning
- bandwidth isolation between workspace and storage traffic

## Plane separation

Control plane:

- K3s servers
- API VIP
- embedded etcd
- no user workspaces

Service plane:

- `ceph-csi` controllers and node plugins
- CloudNativePG
- NATS JetStream
- Temporal
- Coder control plane
- ingress and cert-manager

Workspace plane:

- Coder workspaces only
- CephFS-mounted `/workspace`
- browser/session reattachment path terminates at Coder ingress and returns to the same durable workspace state

Batch plane:

- reserved only
- not required for the first live slice

ClusterIP-only in M1:

- PostgreSQL
- NATS JetStream
- Temporal frontend and web
- ceph-csi controllers
- run/resume backends

## Minimal port matrix

| Path | Port / proto | Required now | Notes |
|---|---|---|---|
| client -> K3s API VIP | `6443/tcp` | yes | Kubernetes API |
| K3s server <-> K3s server | `2379-2380/tcp` | yes | embedded etcd peer/client traffic |
| K3s node <-> K3s node | `8472/udp` or CNI-specific | yes | only if current K3s CNI uses VXLAN; verify actual CNI behavior |
| client -> ingress | `443/tcp` | yes | Coder and internal registry |
| client -> ingress | `80/tcp` | optional | redirect to TLS if used |
| workspace/browser -> `coder.lab.internal` | `443/tcp` | yes | workspace launch and reattachment |
| service-plane pod -> PostgreSQL | `5432/tcp` | yes | CloudNativePG RW service |
| service-plane pod -> NATS | `4222/tcp` | yes | client and JetStream API |
| NATS pod <-> NATS pod | `6222/tcp` | yes | NATS cluster routes |
| operator -> NATS monitor | `8222/tcp` | optional | monitoring only if exposed |
| service-plane pod -> Temporal frontend | `7233/tcp` | yes | workflow client/worker traffic |
| operator -> Temporal web | `8080/tcp` behind ingress | deferred | operator UI after M1 |
| node -> Ceph monitor | `3300/tcp` | recommended | Ceph msgr2 |
| node -> Ceph monitor | `6789/tcp` | yes until confirmed otherwise | Ceph msgr1/current placeholder in scaffold |

## Required now vs deferred later

Required now:

- stable API VIP
- working DNS for API, Coder, and wildcard workspace routing
- ingress TLS for Coder and the internal registry
- flat east-west connectivity across control, service, and workspace planes
- reachability from Kubernetes nodes to external Ceph monitors
- port-level access for PostgreSQL, NATS, Temporal, and Ceph

Deferred later:

- dedicated service/workspace subnets
- NetworkPolicies
- service mesh
- RDMA, GPUDirect, or high-performance fabric features
- advanced observability endpoints and traffic segmentation
