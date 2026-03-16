# Sounio Workspace Image

This image is seeded from the existing `t560-proxmox` Podman toolchain image, but only as a bootstrap source.

- `M0.5`: local Podman-backed reference semantics and toolchain seed
- `M1`: Kubernetes-native workspace runtime in Coder

## Build flow

1. Export or push the `t560` fallback image `localhost/sounio-dev:toolchain-v1` into a registry reachable by the cluster.
2. Build this Dockerfile with `BASE_IMAGE` pointing at that registry image.
3. Publish the resulting image as the canonical Coder workspace image, using the M1 naming convention:
   - `registry.REPLACE_WITH_INTERNAL_ZONE/sounio/workspace:toolchain-v1`

The final canonical habitat is the Coder workspace in Kubernetes, not the `t560` Podman container.
