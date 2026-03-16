# Internal OCI Registry

This is the simplest internal registry path suitable for M1.

Design:

- one replica
- namespace `registry`
- `ClusterIP` service
- internal ingress hostname
- persistence on `proxmox-ceph-rbd`
- TLS issued by the internal CA via `cert-manager`

Required hostname:

- `registry.lab.internal`

Required persistence:

- one PVC on `proxmox-ceph-rbd`
- default size in the template: `100Gi`

Image naming convention:

- `registry.lab.internal/sounio/workspace:toolchain-v1`
- `registry.lab.internal/sounio/lab-runner:toolchain-v1`

M1 security posture:

- keep the registry internal-only
- do not expose it publicly
- defer auth hardening until after the first live slice is proven
