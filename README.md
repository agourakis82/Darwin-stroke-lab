# Sounio Stroke Lab

Base funcional do primeiro lab aplicado de um stack maior de pesquisa: um nucleo de plataforma para agentes, benchmarks, artefatos, tracing, MCP e orquestracao cientifica, hoje ancorado no dominio de AVC porque stroke foi o primeiro caso serio implementado.

O nome do pacote ainda e `sounio_stroke_lab`, mas a direcao arquitetural ja e mais ampla:

- **platform core** para agentic workflows, manifests, storage, traces, briefs e MCP;
- **stroke lab** como primeiro dominio aplicado e benchmarkado;
- **Sounio** como kernel cientifico integrado ao runtime experimental.

Isso importa porque o objetivo do repositorio nao e virar um demo isolado de AVC nem se confundir com o repo upstream da linguagem `Sounio`. A meta e evoluir para um research OS reutilizavel, sem perder o lab de stroke e sem abandonar a disciplina benchmark-first.

## Rewrite Lane: Sounio + F#

Esta branch do repositorio agora tambem contem a trilha paralela da reescrita para `Sounio + F#`, sem desligar o lab atual:

- contratos congelados em [contracts/README.md](/Users/demetriosagourakis/Documents/New%20project/contracts/README.md)
- solution F# em [fsharp/DarwinResearchOs.sln](/Users/demetriosagourakis/Documents/New%20project/fsharp/DarwinResearchOs.sln)
- semantica portada de `campaigns -> programs -> portfolio`, store em memoria e workers locais de jobs na lane F#
- API F# agora resolve storage por configuracao e expoe endpoints de rewrite para `jobs`, `benchmark-runs`, `campaigns`, `programs` e `portfolio`
- API F# agora tambem expõe `/rewrite/sounio-runtime`, que valida `souc`, `stdlib` e um kernel minimo local do repo
- esse endpoint agora tambem carrega o `libsounio_runtime` nativo para exercitar o ABI epistemic/uncertainty do runtime diretamente em F#, inventaria o novo caminho `SNIO/session/kernel` do upstream, e deixa explicito quando o binario buildavel ainda nao acompanha o source tree novo
- a rewrite lane agora tambem expõe `/rewrite/sounio-runtime/abi`, que inventaria os exports reais do dylib, separa FFI científica source-only do que já está host-loadable, e já reconhece o novo caminho oficial de embedding via `SNIO + souc --serve`
- API F# agora ja tem o primeiro write path operacional em `POST /rewrite/sounio-runtime/run`, agora `SNIO`-preferred e `CLI`-fallback: ele tenta primeiro o embedding ABI novo do `Sounio` e so cai para `souc check/run` quando o binario atual ainda nao consegue servir `serve_entry.sio`
- esse write path agora tambem entende `requireSnio`: quando o embedding ABI é obrigatório e nao fecha, a lane falha explicitamente em vez de cair silenciosamente para CLI
- API F# agora ja tem tambem o primeiro caminho operacional em fila: `POST /rewrite/jobs/benchmark` submete no Postgres e `Darwin.ResearchOs.Worker` drena essa fila sem Python no meio
- a mesma fila agora tambem cobre `Sounio` runtime jobs em `POST /rewrite/jobs/sounio-runtime`, com artifacts e metadata `snio_attempted/snio_used` persistidos pelo worker F#
- `campaigns` e `programs` da lane F# agora ja podem usar a mesma fila: `POST /rewrite/campaigns/queued`, reconciliacao live em `GET /rewrite/campaigns/{id}`, e `POST /rewrite/programs/{id}/campaigns` para abrir campaigns enfileiradas ja vinculadas ao programa
- `campaigns` e `programs` da lane F# agora tambem emitem `deployment-ready plans` de cluster: `GET /rewrite/campaigns/{id}/k8s-plan` renderiza os jobs ativos e os follow-ups launch-ready, e `GET /rewrite/programs/{id}/k8s-plan` agrega esses planos no nivel do programa
- esses `k8s-plans` agora tambem podem ser materializados e despachados na propria lane F#: `POST /rewrite/campaigns/{id}/k8s-plan/materialize`, `POST /rewrite/programs/{id}/k8s-plan/materialize`, `POST /rewrite/campaigns/{id}/k8s-plan/launch` e `POST /rewrite/programs/{id}/k8s-plan/launch`
- `k8s_dispatch` agora tambem tem caminho operacional real na lane F#: o worker F# usa `kubectl` para `submit/poll/cancel` de `Job`, e `POST /rewrite/jobs/{id}/cancel` marca cancelamento para o loop do dispatcher
- o renderer K8s de benchmark da lane F# agora preserva `manifest`, `external manifest`, `train split`, `test split` e `seed`, para que o rollout de cluster nao perca os knobs do experimento
- a mesma borda F# agora tambem pode lancar `sounio_runtime_remote` direto em cluster via `POST /rewrite/jobs/sounio-runtime/launch-k8s`, e a imagem do worker aceita um bundle opcional de `Sounio` em `/opt/sounio`
- o smoke reproduzivel dessa trilha ficou documentado em [fsharp/CLUSTER_SMOKE.md](/Users/demetriosagourakis/Documents/New%20project/fsharp/CLUSTER_SMOKE.md), com helpers em [prepare_cluster_sounio_bundle.sh](/Users/demetriosagourakis/Documents/New%20project/scripts/prepare_cluster_sounio_bundle.sh), [fsharp_cluster_smoke.py](/Users/demetriosagourakis/Documents/New%20project/scripts/fsharp_cluster_smoke.py) e o wrapper de um comando [run_fsharp_cluster_smoke.sh](/Users/demetriosagourakis/Documents/New%20project/scripts/run_fsharp_cluster_smoke.sh)
- o repo agora tambem expoe targets diretos em [Makefile](/Users/demetriosagourakis/Documents/New%20project/Makefile): `make smoke-cluster-sounio`, `make smoke-cluster-benchmark` e as variantes `*-fast` para reusar bundle/imagem/import ja prontos
- a mesma trilha agora tambem pode ser disparada do GitHub Actions via [.github/workflows/fsharp-cluster-smoke.yml](/Users/demetriosagourakis/Documents/New%20project/.github/workflows/fsharp-cluster-smoke.yml), pensado para runner self-hosted macOS com Docker, .NET, Python, SSH para `DEVdesktop` e checkout local do `Sounio`
- baseline probabilistico em [stan/README.md](/Users/demetriosagourakis/Documents/New%20project/stan/README.md)
- dossier SOTA/SOTT em [docs/rewrite/sota_sott_dossier.md](/Users/demetriosagourakis/Documents/New%20project/docs/rewrite/sota_sott_dossier.md)
- RFC para o dev do Sounio em [docs/rewrite/sounio_platform_rfc.md](/Users/demetriosagourakis/Documents/New%20project/docs/rewrite/sounio_platform_rfc.md)
- RFC curto focado no gap de kernel ABI em [docs/rewrite/sounio_native_kernel_abi_rfc.md](/Users/demetriosagourakis/Documents/New%20project/docs/rewrite/sounio_native_kernel_abi_rfc.md)

Comandos principais:

```bash
python3 scripts/freeze_contracts.py --output-root contracts
export PATH="$HOME/.dotnet:$PATH"
dotnet build fsharp/DarwinResearchOs.sln
dotnet test fsharp/DarwinResearchOs.sln
```

## O que esta implementado

- API local para:
  - criar estudos a partir de DICOM, PNG/JPG ou `.npy` de pesquisa;
  - executar analise com o runner `Sounio` ou baselines comparativos;
  - consultar o ultimo resultado de um estudo;
  - rodar benchmark comparativo completo e recuperar o relatorio;
  - submeter benchmark como job local assíncrono, consultar status e pedir cancelamento;
  - agrupar multiplos benchmarks em `campaigns` e reconciliar o progresso por polling;
  - gerar follow-ups estruturados por `campaign` e disparar a proxima rodada a partir da melhor evidencia atual;
  - gerar `experiment plans` estruturados por `campaign`, com hipotese, coorte-alvo, criterios de sucesso e baseline obrigatorio;
  - derivar `plan reports` comparaveis entre campaigns, com status `ready/watch/blocked` e notas de aceite;
  - agregar campaigns em um `portfolio` global para ranquear evidencia e prontidao do programa inteiro;
  - agrupar campaigns em `programs` tematicos por hipotese ou objetivo cientifico;
  - consultar `jobs` locais e refs de artefatos persistidos por owner;
  - enfileirar runs agentic de pesquisa, clinicos ou dual-track;
  - consultar eventos SSE, health de servidores MCP e briefs de pesquisa.
- Pipeline comum e reproduzivel:
  - ingestao;
  - normalizacao estilo HU;
  - reamostragem volumetrica;
  - atlas ASPECTS sintetico;
  - derivacao de score regional e score total.
- Runner principal `Sounio` com dois modos:
  - core analitico volumetrico no runtime oficial `Sounio` para feature extraction, scoring regional e geracao do heatmap;
  - fallback Python apenas quando o runtime oficial nao estiver disponivel ou falhar.
- Benchmark baseado em manifest explicito com:
  - treino no split `train` e avaliacao no split `test`;
  - validacao interna por split ou validacao externa com manifest de teste separado;
  - comparacao entre `Sounio`, Python, Julia e C++;
  - ablacoes treinadas do runner `Sounio`;
  - metricas clinicas, de interpretabilidade, metricas oficiais estilo ISLES e comparacoes pareadas com IC/p-value;
  - manifesto JSON, protocolo estruturado de estudo, leaderboard composto, auditorias CLAIM/TRIPOD-AI, manuscrito rascunho, artefatos de modelo, relatorio Markdown, figuras PNG, export case-level e metricas estratificadas.
- CLI para:
  - construir manifest a partir de ISLES24;
  - construir manifest a partir do AISD;
  - construir manifest de coorte DICOM local;
  - construir manifest a partir de CSV/JSONL;
  - validar manifest;
  - disparar benchmark local;
  - rodar um worker local dedicado de benchmark;
  - renderizar um benchmark job em formato K8s sem submeter ao cluster;
  - enfileirar runs agentic;
  - subir servidores MCP locais em `stdio`.
- Agentic OS local com:
  - `LabDirectorAgent`, `ResearchManagerAgent` e `ClinicalManagerAgent`;
  - fila duravel em SQLite para runs autonomos;
  - auditoria de tool calls, guardrails, handoffs, traces e artefatos;
  - servidores MCP locais para filesystem, dataset, benchmark, clinical, research e Sounio.
- Contratos de plataforma ja extraidos para:
  - `platform_core`, com agent runs, traces, MCP e briefs;
  - `job_backend`, com backend local explicito, worker de benchmark separado e renderer K8s-shaped para a proxima fase;
  - `state_store` e `object_store`, separando estado persistente de artefatos;
  - `stroke_lab`, com analise, benchmark e logica especifica de AVC;
  - `service`, como facade de compatibilidade;
  - `jobs` e `artifact refs` persistidos com fallback SQLite/filesystem e suporte dev-local a Postgres/MinIO.
- Research OS mais profundo com:
  - combinacao de corpus local curado e pesquisa publica segura;
  - redacao automatica de paths locais, referencias DICOM/NIfTI/NPY e identificadores sensiveis antes de qualquer tool publica;
  - fallback automatico para corpus local quando `OPENAI_API_KEY` nao estiver disponivel.
  - wrappers MCP por `run_id`, para que os agentes oficiais trabalhem com contexto resolvido no servidor sem receber paths locais brutos.
- Integracao real com o compilador oficial `souc` quando ele estiver disponivel no sistema ou em um checkout local do repositório oficial.

## Importante

Esta base **nao faz alegacoes clinicas reais**. O benchmark exige um manifest explicito de estudos reais com splits declarados. Sem esse manifest e sem dados clinicos validados localmente, nao existe "evidencia" embutida no repositório.

## Essencia arquitetural

Se o repositorio evoluir e perder qualquer um destes pontos, houve drift:

- `Sounio` continua sendo o kernel cientifico, nao o repo inteiro;
- claims cientificas continuam presas a `manifest -> run -> artifact -> metric -> report -> claim`;
- infraestrutura reutilizavel deve ser extraida para a plataforma, nao duplicada dentro do codigo de stroke;
- workloads pesados devem evoluir para scheduler/K8s/HPC, e nao ficar presos a execucao local sincrona;
- tracing, auditoria e guardrails precisam continuar de ponta a ponta.

O documento de referencia para isso e [AGENTS.md](/Users/demetriosagourakis/Documents/New%20project/AGENTS.md).

## Integracao com o runtime oficial

O runner principal tenta localizar `souc` nesta ordem:

- `SOUNIO_SOUC_PATH`
- `souc` no `PATH`
- `~/sounio/compiler/target/release/souc`
- `~/sounio-lang-sounio/compiler/target/release/souc`

O stdlib e localizado por `SOUNIO_STDLIB_PATH` ou por um checkout local de `sounio`.

Regra de aceitacao:

- binario explicitamente apontado por `SOUNIO_SOUC_PATH` ou presente no `PATH`: aceito como instalacao oficial presumida;
- checkout local: so e aceito se o `origin` for `github.com/sounio-lang/sounio`, o worktree estiver limpo e o commit local for exatamente o `HEAD` atual do GitHub.

Quando o runtime e encontrado, o pipeline principal:

1. entrega ao `Sounio` os volumes pre-processados e o atlas ASPECTS;
2. extrai no proprio `.sio` as features hipercomplexas usadas pelo runner;
3. calcula no `Sounio` os scores regionais e o heatmap volumetrico final;
4. devolve ao shell Python apenas os artefatos finais para API, storage e benchmark.

No caminho treinado por artefato, o `Sounio` tambem executa a inferencia volumetrica e a reconstrucao do heatmap; Python fica responsavel apenas pelo IO cientifico, orquestracao, API e tooling de benchmark.

Se o runtime nao estiver disponivel, ou se o checkout local estiver fora da versao oficial do GitHub, o projeto cai para um fallback Python com a mesma formula.

## Instalar

```bash
python3 -m pip install -e ".[dev]"
```

## CLI

Construir manifest ISLES24:

```bash
sounio-stroke-lab build-isles24-manifest \
  --dataset-root /abs/path/to/isles24 \
  --output /abs/path/to/isles24_manifest.json
```

Construir manifest de coorte DICOM local:

```bash
sounio-stroke-lab build-dicom-cohort-manifest \
  --dataset-root /abs/path/to/local_ncct_cases \
  --output /abs/path/to/local_dicom_manifest.json \
  --dataset-name local_ncct \
  --dataset-version local-ncct-v1
```

Construir manifest AISD:

```bash
sounio-stroke-lab build-aisd-manifest \
  --image-root /abs/path/to/aisd/images \
  --mask-root /abs/path/to/aisd/masks \
  --output /abs/path/to/aisd_manifest.json
```

Construir manifest a partir de indice CSV/JSONL:

```bash
sounio-stroke-lab build-index-manifest \
  --index /abs/path/to/cases.csv \
  --output /abs/path/to/index_manifest.json \
  --dataset-name local_index \
  --dataset-version local-index-v1
```

Validar manifest:

```bash
sounio-stroke-lab validate-manifest \
  --manifest /abs/path/to/benchmark_manifest.json
```

## Executar a API

```bash
uvicorn sounio_stroke_lab.main:app --reload
```

Por padrao, a API sobe **sem** worker embutido de benchmark. Isso deixa o
processo da API mais fiel ao modelo de control plane. Para desenvolvimento
local rapido, voce pode ativar um worker embutido:

```bash
SOUNIO_STROKE_START_EMBEDDED_BENCHMARK_WORKER=1 \
uvicorn sounio_stroke_lab.main:app --reload
```

Ou, no caminho recomendado desta fatia, subir um worker separado:

```bash
sounio-stroke-lab run-benchmark-worker --storage-root /abs/path/to/run_storage
```

Se voce chamar `POST /benchmark/runs` sem nenhum worker ativo, a API agora falha
rapido com uma mensagem explicita em vez de ficar presa esperando sem contexto.

## Stack local tipo control plane

Variaveis principais desta fatia:

- `SOUNIO_STROKE_DB_URL`
- `SOUNIO_STROKE_OBJECT_STORE`
- `SOUNIO_STROKE_MINIO_ENDPOINT`
- `SOUNIO_STROKE_MINIO_ACCESS_KEY`
- `SOUNIO_STROKE_MINIO_SECRET_KEY`
- `SOUNIO_STROKE_MINIO_BUCKET`
- `SOUNIO_STROKE_JOB_BACKEND`
- `SOUNIO_STROKE_AGENT_BACKEND`
- `SOUNIO_STROKE_START_EMBEDDED_BENCHMARK_WORKER`
- `SOUNIO_STROKE_API_TOKEN` / `SOUNIO_STROKE_API_TOKENS`
- `SOUNIO_STROKE_K8S_NAMESPACE`
- `SOUNIO_STROKE_K8S_LOCAL_QUEUE`
- `SOUNIO_STROKE_K8S_WORKER_IMAGE`
- `SOUNIO_STROKE_K8S_DATASET_PVC`
- `SOUNIO_STROKE_K8S_SHARED_PATH_PREFIXES`
- `SOUNIO_STROKE_K8S_DB_SECRET_NAME`
- `SOUNIO_STROKE_K8S_MINIO_SECRET_NAME`

Defaults:

- sem `SOUNIO_STROKE_DB_URL`: SQLite local
- sem `SOUNIO_STROKE_OBJECT_STORE=minio`: filesystem local
- `SOUNIO_STROKE_JOB_BACKEND=local`: jobs locais para worker separado
- `SOUNIO_STROKE_JOB_BACKEND=kubernetes`: submissao/poll/cancel de `Job` K8s usando o mesmo contrato de job do backend local
- sem `SOUNIO_STROKE_AGENT_BACKEND`: herda o backend de jobs; no cluster, isso permite subir `agent runs` como `Kubernetes Job`
- se `SOUNIO_STROKE_API_TOKEN(S)` estiver configurado, toda rota fora de `/health` e `/readyz` exige `Authorization: Bearer <token>`

Subir o stack local com Postgres + MinIO + API + worker:

```bash
docker compose up --build
```

Se o host ja tiver portas ocupadas, o `docker-compose.yml` agora aceita override
das portas publicadas sem alterar os servicos internos:

```bash
SOUNIO_STROKE_POSTGRES_HOST_PORT=15432 \
SOUNIO_STROKE_MINIO_API_HOST_PORT=19010 \
SOUNIO_STROKE_MINIO_CONSOLE_HOST_PORT=19011 \
SOUNIO_STROKE_API_HOST_PORT=18000 \
docker compose up --build
```

Isso sobe:

- `postgres`
- `minio`
- `minio-init`
- `api`
- `benchmark-worker`

Smoke end-to-end do stack local:

```bash
python3 scripts/dev_stack_smoke.py
```

O script sobe o stack, gera um manifest fixture dentro do workspace, submete um
benchmark job via API, espera a conclusao e derruba tudo no final.

Bateria local reproduzivel de jobs no Mac, usando o mesmo stack `Postgres +
MinIO + api + benchmark-worker`:

```bash
python3 scripts/local_job_battery.py --profile stress-light --profile stress-medium
python3 scripts/local_job_battery.py --profile stress-heavy
python3 scripts/local_job_battery.py --profile agentic-e2e --build
```

Perfis disponiveis:

- `stress-light`: 5 benchmark jobs enfileirados com 1 worker, usando a fixture mini
- `stress-medium`: 4 benchmark jobs maiores com 2 workers, usando a fixture completa
- `stress-heavy`: 8 benchmark jobs maiores com 3 workers, usando a fixture completa
- `agentic-e2e`: 1 run dual-surface que exercita agentes + benchmark + artifacts + traces

Os relatórios ficam em `.local-battery/<timestamp>/results/` em `json` e `markdown`,
incluindo resumo de `CPU%` e memória observada nos containers do stack local.

## Primeiro smoke K3s via SSH

Para um single-node K3s de dev, o caminho que validamos foi:

1. subir `postgres + minio` pelo `docker compose` em portas livres do host;
2. criar `namespace`, `secrets` e `PVC` no K3s;
3. gerar a fixture de benchmark dentro do PVC montado em `/datasets`;
4. rodar a submissao com `SOUNIO_STROKE_JOB_BACKEND=kubernetes`.

Ponto importante: o CLI `run-benchmark` e `run-agent` agora so ligam worker
embutido quando o backend for `local`. No backend `kubernetes`, a submissao nao
consome o job localmente; ela apenas cria o `Job` do cluster e espera o resultado
persistido no Postgres.

O backend K8s tambem sincroniza estado de `Workload` do `Kueue` para dentro do
`JobRecord.result_payload`, incluindo nome do workload, `clusterQueue`,
admissao e finalizacao.

Os manifests minimos para reproduzir o bootstrap do cluster ficam em:

```text
deploy/k8s/base
```

Para storage real, os overlays de PVC ficam em:

```text
deploy/k8s/overlays/cephfs
deploy/k8s/overlays/ceph-rbd
```

No seu ambiente, a recomendacao e usar `CephFS` para o dataset PVC compartilhado.
Para o `k3s` da `DEVdesktop`, existe agora um overlay dedicado em:

```text
deploy/k8s/overlays/devdesktop
```

Ele alinha o bootstrap com o estado real validado nesse host:

- `MinIO` externo em `192.168.3.225:19010`
- PVC local de `2Gi`
- imagem local `sounio-stroke-lab-worker:k3s-dev`

Observacao importante: na `DEVdesktop`, o disco da VM ja fica sobre SSD/Ceph no
host maior. Entao esse ambiente ja e `Ceph-backed` no nivel fisico, mas o
Kubernetes continua vendo o PVC como `local-path`. Para dev isso e suficiente;
para o cluster maior, a migracao so vale a pena quando houver `StorageClass`
Ceph nativa via CSI.

O bootstrap K8s agora ja inclui:

- `ConfigMap` runtime com backend K8s/MinIO/PVC
- `Deployment` `sounio-stroke-api`
- `Service` `sounio-stroke-api`
- `ServiceAccount`/RBAC para a API submeter `Jobs` e consultar `Workloads`

Para preparar um cutover de storage antes de aplicar os overlays:

```bash
sounio-stroke-lab k8s-preflight \
  --namespace darwin-genomics \
  --pvc-name sounio-stroke-datasets \
  --target-storage-class rook-cephfs \
  --overlay-mode cephfs
```

O preflight agora tambem lista as `StorageClass` disponiveis no cluster, para
ficar obvio quando o bloqueio ainda e de infraestrutura e nao do app.

```bash
sounio-stroke-lab k8s-cutover-plan \
  --namespace darwin-genomics \
  --pvc-name sounio-stroke-datasets \
  --target-storage-class rook-cephfs \
  --overlay-mode cephfs
```

Se voce quiser copiar `/datasets` antes de recriar o PVC canonico:

```bash
sounio-stroke-lab render-dataset-copy-job \
  --namespace darwin-genomics \
  --source-pvc sounio-stroke-datasets \
  --target-pvc sounio-stroke-datasets-ceph \
  > dataset-copy-job.json
```

O runbook operacional desse cutover ficou em:

```text
deploy/k8s/ceph-cutover.md
```

## Rodar testes

```bash
pytest
```

## Fluxo rapido

1. Criar um estudo:

```bash
curl -X POST http://127.0.0.1:8000/studies \
  -F "files=@sample.npy"
```

2. Rodar analise `Sounio`:

```bash
curl -X POST http://127.0.0.1:8000/studies/<study_id>/analyze \
  -H "content-type: application/json" \
  -d '{"model_family":"sounio_hypercomplex","include_baseline_comparison":true}'
```

3. Rodar benchmark treinado:

```bash
curl -X POST http://127.0.0.1:8000/benchmark/runs \
  -H "content-type: application/json" \
  -d '{"dataset_manifest_path":"/abs/path/benchmark_manifest.json","seed":13}'
```

3b. Submeter benchmark como job e acompanhar:

```bash
curl -X POST http://127.0.0.1:8000/benchmark/jobs \
  -H "content-type: application/json" \
  -d '{"dataset_manifest_path":"/abs/path/benchmark_manifest.json","seed":13}'
```

```bash
curl http://127.0.0.1:8000/jobs/<job_id>
```

```bash
curl -X POST http://127.0.0.1:8000/jobs/<job_id>/cancel
```

3c. Criar uma campaign de benchmark:

```bash
curl -X POST http://127.0.0.1:8000/campaigns \
  -H "content-type: application/json" \
  -d '{
    "name":"small-realistic-smoke",
    "objective":"Compare small manifests and harvest benchmark artifacts.",
    "benchmark_specs":[
      {"label":"fixture-main-seed-11","request":{"dataset_manifest_path":"/abs/path/benchmark_manifest.json","seed":11}},
      {"label":"fixture-mini-seed-12","request":{"dataset_manifest_path":"/abs/path/small_benchmark_manifest.json","seed":12}}
    ]
  }'
```

```bash
curl http://127.0.0.1:8000/campaigns/<campaign_id>
```

```bash
curl http://127.0.0.1:8000/campaigns/<campaign_id>/followup
```

```bash
curl -X POST http://127.0.0.1:8000/campaigns/<campaign_id>/followup \
  -H "content-type: application/json" \
  -d '{"proposal_id":"winner-reproducibility-sweep"}'
```

```bash
curl http://127.0.0.1:8000/campaigns/<campaign_id>/plans
```

```bash
curl http://127.0.0.1:8000/campaigns/<campaign_id>/plan-reports
```

```bash
curl -X POST http://127.0.0.1:8000/programs \
  -H "content-type: application/json" \
  -d '{"name":"lesion-sensitivity-program","objective":"Track campaigns for cortical lesion sensitivity.","hypothesis":"A focused Sounio sweep improves cortical sensitivity while preserving ASPECTS calibration."}'
```

```bash
curl http://127.0.0.1:8000/programs
```

```bash
curl -X POST http://127.0.0.1:8000/programs/<program_id>/campaigns \
  -H "content-type: application/json" \
  -d '{"name":"program-campaign","objective":"First campaign inside the program.","benchmark_specs":[{"label":"seed-21","request":{"dataset_manifest_path":"/abs/path/benchmark_manifest.json","seed":21}}]}'
```

```bash
curl -X POST http://127.0.0.1:8000/programs/<program_id>/campaigns/attach \
  -H "content-type: application/json" \
  -d '{"campaign_id":"<campaign_id>"}'
```

```bash
curl http://127.0.0.1:8000/programs/<program_id>
```

```bash
curl http://127.0.0.1:8000/portfolio/campaigns
```

```bash
curl http://127.0.0.1:8000/portfolio/programs
```

```bash
curl -X POST http://127.0.0.1:8000/campaigns/<campaign_id>/plans/<plan_id>/campaign
```

```bash
curl -X POST http://127.0.0.1:8000/campaigns/<campaign_id>/plans/<plan_id>/agent-run \
  -H "content-type: application/json" \
  -d '{"notes":"Execute this experiment plan through the research agent."}'
```

```bash
curl http://127.0.0.1:8000/artifacts/campaign/<campaign_id>
```

```bash
curl -X POST http://127.0.0.1:8000/campaigns/<campaign_id>/brief \
  -H "content-type: application/json" \
  -d '{"question":"Summarize the campaign evidence and recommend the next experiment."}'
```

4. Reutilizar o artefato treinado do benchmark em um estudo:

```bash
curl -X POST http://127.0.0.1:8000/studies/<study_id>/analyze \
  -H "content-type: application/json" \
  -d '{"model_family":"sounio_hypercomplex","include_baseline_comparison":true,"model_artifact_path":"/abs/path/to/artifacts/<run_id>/models/sounio_hypercomplex.json"}'
```

Rodar benchmark pela CLI:

```bash
sounio-stroke-lab run-benchmark \
  --manifest /abs/path/to/benchmark_manifest.json \
  --storage-root /abs/path/to/run_storage \
  --seed 13
```

Rodar o worker local dedicado:

```bash
sounio-stroke-lab run-benchmark-worker \
  --storage-root /abs/path/to/run_storage
```

Processar um unico job ja persistido:

```bash
sounio-stroke-lab run-benchmark-worker \
  --storage-root /abs/path/to/run_storage \
  --job-id <job_id>
```

Renderizar um job K8s-shaped:

```bash
sounio-stroke-lab render-benchmark-job \
  --storage-root /abs/path/to/run_storage \
  --job-id <job_id>
```

Executar um agent run especifico dentro de um worker/pod:

```bash
sounio-stroke-lab run-agent-worker \
  --run-id <run_id> \
  --job-id <job_id>
```

Enfileirar um run agentic dual:

```bash
sounio-stroke-lab run-agent \
  --objective "Build a benchmark-backed research brief and clinical copilot summary." \
  --surface dual \
  --manifest /abs/path/to/benchmark_manifest.json \
  --study-file /abs/path/to/study.npy
```

Subir um servidor MCP local:

```bash
sounio-stroke-lab run-mcp-server \
  --name darwin-research \
  --storage-root /abs/path/to/run_storage
```

Rodar validacao externa:

```bash
sounio-stroke-lab run-benchmark \
  --manifest /abs/path/to/train_manifest.json \
  --external-test-manifest /abs/path/to/external_test_manifest.json \
  --storage-root /abs/path/to/run_storage \
  --seed 13
```

Disparar um run agentic pela API:

```bash
curl -X POST http://127.0.0.1:8000/agent/runs \
  -H "content-type: application/json" \
  -d '{"objective":"Build a benchmark-backed research brief and clinical copilot summary.","surface":"dual","dataset_manifest_path":"/abs/path/benchmark_manifest.json","study_file_paths":["/abs/path/study.npy"]}'
```

Quando a API estiver exposta apenas pela tailnet do host, a chamada fica igual,
mas usando o nome/IP do Tailscale e bearer token:

```bash
curl -X POST http://devdesktop:18080/agent/runs \
  -H "authorization: Bearer <token>" \
  -H "content-type: application/json" \
  -d '{"objective":"Run the research branch on Kubernetes.","surface":"research","dataset_manifest_path":"/datasets/fixture-mini/small_benchmark_manifest.json"}'
```

Consultar traces persistidos do run:

```bash
curl http://127.0.0.1:8000/agent/runs/<run_id>/trace
```

## Pesquisa publica e tracing

- `darwin-research.search_public_literature` usa o caminho oficial do OpenAI Agents SDK com `WebSearchTool` quando `OPENAI_API_KEY` estiver configurada.
- Sem credencial, o sistema nao falha: ele registra o modo `fallback_local_corpus` e continua com o corpus curado local.
- Todo `AgentStep` tambem vira `TraceEvent` persistido em SQLite e em `artifacts/<run_id>/traces/trace_events.jsonl`.
- O endpoint SSE `/agent/runs/{id}/events` continua sendo o stream operacional; `/agent/runs/{id}/trace` expõe o espelho persistido de eventos/traces.
- Quando `OPENAI_API_KEY` estiver presente, o worker tenta primeiro o runtime oficial `openai-agents` com MCP local (`MCPServerStdio`) e cai para o runtime deterministico apenas se o caminho oficial falhar.
- Os MCP tools usados por agentes retornam payloads seguros: ids, metricas resumidas e refs relativas ao storage, em vez de paths absolutos.
