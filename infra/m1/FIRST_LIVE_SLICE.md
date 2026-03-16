# First Live Vertical Slice

This is the revised first live deployment order for M1.

Prerequisite:

- K3s HA foundation, API VIP, DNS, ingress class, and base namespaces are already in place

## Order

1. `ceph-csi`
   - fill in external Ceph config and secrets
   - install CephFS and RBD CSI drivers
   - apply `proxmox-cephfs` and `proxmox-ceph-rbd`
   - prove one CephFS PVC and one RBD PVC bind successfully

2. `postgres`
   - install CloudNativePG
   - apply the PostgreSQL cluster
   - provision bootstrap secrets and create `coder`, `temporal`, `temporal_visibility`, and `lab_runs`
   - prove RW service connectivity on `5432`

3. `nats`
   - install NATS with JetStream enabled
   - bind persistent file store on `proxmox-ceph-rbd`
   - create the `RUN_EVENTS` stream
   - prove ordered publish/consume and consumer replay

4. `temporal`
   - install Temporal against PostgreSQL
   - expose the frontend service internally
   - deploy the worker scaffold
   - prove worker-to-frontend connectivity on `7233`

5. `registry`
   - deploy the internal OCI registry on the service plane
   - bind persistence on `proxmox-ceph-rbd`
   - prove push and pull through the internal registry hostname

6. publish workspace image
   - push the canonical Sounio workspace image to the chosen registry
   - push the canonical lab runner image to the chosen registry
   - verify pull from workspace-plane nodes
   - keep `t560` only as seed/toolchain source

7. `coder`
   - install the Coder control plane
   - configure ingress, wildcard routing, and PostgreSQL access
   - register the Sounio template
   - prove workspace creation with CephFS-mounted `/workspace`

8. pilot workflow
   - submit `generic_agent_task`
   - verify events, artifacts, and resume summary
   - execute the disconnect/reconnect acceptance drill

## Exit gates

- `ceph-csi` exit gate: storage classes provision correctly against external Ceph
- `postgres` exit gate: application databases are reachable
- `nats` exit gate: JetStream retains ordered run events
- `temporal` exit gate: a worker can connect and a workflow can start
- `registry` exit gate: the internal registry is reachable and backed by persistent storage
- workspace image exit gate: cluster nodes can pull the image
- `coder` exit gate: workspace launch and reattachment both work
- pilot exit gate: reconnect returns a valid `ResumeSummary`
