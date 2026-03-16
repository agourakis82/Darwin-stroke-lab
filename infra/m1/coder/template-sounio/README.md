# Sounio Coder Template

This template provisions a workspace pod in namespace `workspace-plane` and mounts a per-user CephFS PVC at `/workspace`.

Template assumptions:

- the Coder control plane already runs in namespace `coder`
- the cluster has a `proxmox-cephfs` storage class backed by external Ceph via `ceph-csi`
- the workspace image is built from [`../../images/sounio-workspace/Dockerfile`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/images/sounio-workspace/Dockerfile)
- `t560-proxmox` is only the seed source for the initial toolchain image

Expected workflow:

1. import this template into Coder
2. create a workspace
3. work under `/workspace/src`
4. submit pilot runs with `labctl run submit`
