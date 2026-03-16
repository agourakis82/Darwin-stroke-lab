# Pre-Apply Checklist

Use this as a binary gate. Every line must be true before the first apply.

- [ ] `lab.internal` is the frozen internal zone.
- [ ] `k3s-api.lab.internal` resolves to the final API VIP.
- [ ] `coder.lab.internal` resolves correctly.
- [ ] `*.coder.lab.internal` wildcard DNS is in place.
- [ ] `registry.lab.internal` resolves correctly.
- [ ] the chosen API VIP is on `192.168.3.0/24`.
- [ ] the API VIP is not already allocated elsewhere.
- [ ] `server-config.yaml` has no remaining `REPLACE_WITH_` values.
- [ ] `kube-vip-daemonset.yaml` has no remaining `REPLACE_WITH_` values.
- [ ] `internal-ca-root-secret.template.yaml` has real PEM content.
- [ ] `m1-ingress-certificates.template.yaml` contains the frozen hostnames.
- [ ] `ceph-csi/secret-template.yaml` has real keys and no `REPLACE_WITH_` values.
- [ ] `client.csi-cephfs` exists on the Ceph cluster.
- [ ] `client.csi-rbd` exists on the Ceph cluster.
- [ ] the CephFS subvolume group `csi` exists or is ready to be created before CSI use.
- [ ] `postgres/bootstrap-secrets.template.yaml` has no `REPLACE_WITH_` values.
- [ ] `registry/registry.template.yaml` uses `registry.lab.internal`.
- [ ] the workspace image exists at `registry.lab.internal/sounio/workspace:toolchain-v1`.
- [ ] the runner image exists at `registry.lab.internal/sounio/lab-runner:toolchain-v1`.
- [ ] `Temporal` remains internal-only for M1.
- [ ] no extra services were added beyond `ceph-csi`, `postgres`, `nats`, `temporal`, `registry`, and `coder`.
- [ ] no Slurm integration is included in the first live slice.
