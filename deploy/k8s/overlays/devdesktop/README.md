# DEVdesktop overlay

Este overlay existe para o `k3s` single-node da `DEVdesktop`, onde:

- o `MinIO` roda fora do cluster, no host `192.168.3.225:19010`
- o PVC de datasets foi validado com `2Gi` em `local-path`
- a API deve ficar disponivel no proprio host em `:18080` via `hostPort`

Observacao:

- o disco da VM ja e hospedado sobre Ceph/SSD no ambiente maior
- portanto, este overlay ja valida um fluxo `Ceph-backed` no nivel do host
- mesmo assim, do ponto de vista do Kubernetes, o PVC ainda e `local-path`
- trate a `DEVdesktop` como baseline de dev, nao como rollout final de storage CSI

Aplicacao:

```bash
kubectl apply -k deploy/k8s/overlays/devdesktop
```

Uso esperado:

- bootstrap rapido do cluster de dev
- smoke tests do backend `kubernetes`
- validacao de `Kueue`, API, jobs de benchmark e `agent runs`

Quando o ambiente migrar para storage real, prefira os overlays `cephfs` ou
`ceph-rbd`.
