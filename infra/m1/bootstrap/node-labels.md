# Node Labels And Taints

Apply the following labels before deploying M1 workloads:

```bash
kubectl label node <server-1> lab.sounio.io/plane=control
kubectl label node <server-2> lab.sounio.io/plane=control
kubectl label node <server-3> lab.sounio.io/plane=control

kubectl label node <service-1> lab.sounio.io/plane=service
kubectl label node <service-2> lab.sounio.io/plane=service

kubectl label node <workspace-1> lab.sounio.io/plane=workspace
kubectl label node <workspace-2> lab.sounio.io/plane=workspace

kubectl label node <batch-1> lab.sounio.io/plane=batch
```

Recommended taints:

```bash
kubectl taint node <server-1> node-role.kubernetes.io/control-plane=:NoSchedule
kubectl taint node <server-2> node-role.kubernetes.io/control-plane=:NoSchedule
kubectl taint node <server-3> node-role.kubernetes.io/control-plane=:NoSchedule

kubectl taint node <batch-1> lab.sounio.io/plane=batch:NoSchedule
```

Scheduling intent:

- control-plane nodes: only K3s and cluster-critical add-ons
- service-plane nodes: `ceph-csi` controllers, PostgreSQL, Coder control plane, Temporal, NATS, ingress
- workspace-plane nodes: Coder workspaces only
- batch-plane nodes: Slurm-only later, not part of the M1 pilot

Important:

- external Proxmox/Ceph is the only storage authority for M1
- Kubernetes consumes that storage via `ceph-csi`; it does not host Ceph daemons by default
