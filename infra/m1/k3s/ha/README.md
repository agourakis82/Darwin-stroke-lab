# K3s HA Bootstrap

This directory is the M1 reference configuration for a three-server K3s HA cluster with embedded etcd and a `kube-vip` API VIP. Do not treat any VM as the canonical development home; the canonical habitat is the Kubernetes workspace plane once Coder is online.

## Bootstrap order

1. Fill [`control-plane-identity.input.example.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/k3s/ha/control-plane-identity.input.example.yaml).
2. Copy [`server-config.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/k3s/ha/server-config.yaml) to `/etc/rancher/k3s/config.yaml` on every server.
3. Replace the API VIP and token placeholders in [`server-config.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/k3s/ha/server-config.yaml) and [`kube-vip-daemonset.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/k3s/ha/kube-vip-daemonset.yaml).
4. Install the first server with `INSTALL_K3S_EXEC=server`.
5. Join the other two servers with the same config and shared token.
6. Apply [`kube-vip-daemonset.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/k3s/ha/kube-vip-daemonset.yaml).
7. Apply the bootstrap namespaces and priority classes.

## Acceptance

- `kubectl get nodes` shows all servers as `Ready`
- the API VIP answers even after one server is shut down
- service and workspace nodes accept plane-specific scheduling after labels are applied
