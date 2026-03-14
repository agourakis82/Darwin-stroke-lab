# Ceph Cutover Runbook

Este runbook assume que o cluster já tem:

- namespace `darwin-genomics`
- `Kueue` funcionando
- `ServiceAccount`/RBAC do `sounio-stroke-runner`
- `Secrets` `sounio-stroke-db`, `sounio-stroke-minio` e `sounio-stroke-api`

Nota de escopo:

- na `DEVdesktop`, o disco da VM ja pode estar apoiado em Ceph no host
- isso nao substitui `StorageClass` Ceph nativa no Kubernetes
- use este runbook quando o cluster realmente expuser `CephFS` ou `RBD` via CSI

## 1. Rodar o preflight

```bash
sounio-stroke-lab k8s-preflight \
  --namespace darwin-genomics \
  --pvc-name sounio-stroke-datasets \
  --target-storage-class rook-cephfs \
  --overlay-mode cephfs
```

Se o objetivo for `RBD`:

```bash
sounio-stroke-lab k8s-preflight \
  --namespace darwin-genomics \
  --pvc-name sounio-stroke-datasets \
  --target-storage-class rook-ceph-block \
  --overlay-mode ceph-rbd
```

## 2. Gerar o plano de cutover

```bash
sounio-stroke-lab k8s-cutover-plan \
  --namespace darwin-genomics \
  --pvc-name sounio-stroke-datasets \
  --target-storage-class rook-cephfs \
  --overlay-mode cephfs
```

## 3. Drenar submissões novas

- escalar `sounio-stroke-api` para `0`
- esperar os `Jobs` ativos terminarem
- confirmar que não há `agent runs`/`benchmark jobs` novos entrando

## 4. Preservar o conteúdo de `/datasets`

Criar um PVC temporário no storage de destino, por exemplo
`sounio-stroke-datasets-ceph`, e renderizar o job de cópia:

```bash
sounio-stroke-lab render-dataset-copy-job \
  --namespace darwin-genomics \
  --source-pvc sounio-stroke-datasets \
  --target-pvc sounio-stroke-datasets-ceph \
  > dataset-copy-job.json

kubectl apply -f dataset-copy-job.json
kubectl wait --for=condition=complete --timeout=30m job/sounio-stroke-dataset-copy -n darwin-genomics
```

## 5. Recriar o PVC canônico com Ceph

Escolha um overlay:

- `deploy/k8s/overlays/cephfs`
- `deploy/k8s/overlays/ceph-rbd`

Depois recrie o PVC `sounio-stroke-datasets` com o overlay apropriado. Se você
usou um PVC temporário para cópia, promova esse conteúdo de volta antes de
reativar a API.

## 6. Subir a API novamente

```bash
kubectl scale deployment/sounio-stroke-api -n darwin-genomics --replicas=1
kubectl rollout status deployment/sounio-stroke-api -n darwin-genomics
```

## 7. Smoke final

- `GET /health`
- `GET /readyz`
- um `POST /benchmark/jobs`
- um `POST /agent/runs`

O cutover só fecha quando os artefatos voltarem a cair no MinIO e o `Kueue`
admitir e concluir um job novo no storage Ceph.
