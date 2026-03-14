Use este overlay quando o cluster expõe um `StorageClass` CephFS compatível com leitura compartilhada.

Padrão esperado:
- `StorageClass`: `rook-cephfs`
- `AccessMode`: `ReadWriteMany`

Aplicação:

```bash
kubectl apply -k deploy/k8s/overlays/cephfs
```

Se o nome do `StorageClass` no cluster for diferente, ajuste `kustomization.yaml` antes de aplicar.
