# K8s Bootstrap

Este diretório guarda o bootstrap mínimo para reproduzir o stack que já validamos
no `k3s` da `DEVdesktop`.

## O que está versionado

- `base/namespace.yaml`
- `base/runtime-config.yaml`
- `base/dataset-pvc.yaml`
- `base/kueue.yaml`
- `base/api-deployment.yaml`
- `base/api-service.yaml`
- `base/kustomization.yaml`
- `base/secrets.example.yaml`
- `overlays/cephfs`
- `overlays/ceph-rbd`
- `overlays/devdesktop`
- `ceph-cutover.md`

## Ordem recomendada

1. Instalar o `Kueue` no cluster.
2. Criar os `Secrets` reais a partir de `base/secrets.example.yaml`.
3. Aplicar:

```bash
kubectl apply -k deploy/k8s/base
```

4. Popular o PVC `sounio-stroke-datasets` com manifests e volumes que os jobs
   vão enxergar em `/datasets`.

## Notas

- O `ClusterQueue`/`LocalQueue` usa `v1beta2`, que foi a versão efetivamente
  instalada e validada no cluster de dev.
- A cota inicial é propositalmente conservadora e deve ser ajustada quando a
  gente migrar do `k3s` single-node para a topologia maior com `Ceph`.
- `secrets.example.yaml` é só template e não deve ser aplicado sem revisão.
- `runtime-config.yaml` é o ponto de patch para endpoints como `MinIO`,
  `worker image`, PVC canônico e `shared path prefixes`.
- Para storage real, use preferencialmente `deploy/k8s/overlays/cephfs` quando
  o cluster tiver `CephFS` disponível. O overlay `ceph-rbd` fica como opção
  quando você quiser PVC `RWO` em bloco.
- Para o `k3s` single-node da `DEVdesktop`, use
  `deploy/k8s/overlays/devdesktop`, que já ajusta o endpoint do MinIO externo
  e o tamanho do PVC validado nesse host.
- A `DEVdesktop` já roda sobre disco de VM apoiado em Ceph no host. Isso
  significa que o ambiente de dev já é `Ceph-backed`, mas ainda não
  `Ceph-native` no Kubernetes: o PVC continua `local-path` e preso ao node do
  `k3s`.
- Para um cutover seguro de storage, siga [ceph-cutover.md](./ceph-cutover.md)
  e use os novos comandos `k8s-preflight`, `k8s-cutover-plan` e
  `render-dataset-copy-job`.
