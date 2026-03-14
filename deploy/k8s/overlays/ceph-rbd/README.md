Use este overlay quando o dataset PVC precisa rodar sobre bloco Ceph (`RBD`) em vez de `CephFS`.

Padrão esperado:
- `StorageClass`: `rook-ceph-block`
- `AccessMode`: `ReadWriteOnce`

Aplicação:

```bash
kubectl apply -k deploy/k8s/overlays/ceph-rbd
```

Para múltiplos pods lendo o mesmo dataset ao mesmo tempo, prefira `CephFS`.
