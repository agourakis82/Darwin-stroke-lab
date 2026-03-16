# Sounio Stroke Lab

Base funcional para demonstrar, em ambiente local, a tese de que o framework `Sounio` com representacao hipercomplexa pode oferecer melhor apoio a isquemia precoce/ASPECTS em TC de cranio do que stacks convencionais, sob protocolo identico.

## O que esta implementado

- API local para:
  - criar estudos a partir de DICOM, PNG/JPG ou `.npy` de pesquisa;
  - executar analise com o runner `Sounio` ou baselines comparativos;
  - consultar o ultimo resultado de um estudo;
  - rodar benchmark comparativo completo e recuperar o relatorio;
  - submeter `runs` duraveis de workspace/agent, consultar eventos e recuperar um resumo de retomada.
- Pipeline comum e reproduzivel:
  - ingestao;
  - normalizacao estilo HU;
  - reamostragem volumetrica;
  - atlas ASPECTS sintetico;
  - derivacao de score regional e score total.
- Runner principal `Sounio` com dois modos:
  - fallback heuristico para demo/local exploration;
  - inferencia treinada por artefato regional, com tentativa de scoring no runtime oficial `Sounio`.
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
  - submeter e retomar `runs` locais via `sounio-stroke-lab run-submit` / `run-resume`;
  - operar a mesma superficie via `labctl`.
- Integracao real com o compilador oficial `souc` quando ele estiver disponivel no sistema ou em um checkout local do repositório oficial.

## Importante

Esta base **nao faz alegacoes clinicas reais**. O benchmark exige um manifest explicito de estudos reais com splits declarados. Sem esse manifest e sem dados clinicos validados localmente, nao existe "evidencia" embutida no repositório.

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

Quando o runtime e encontrado, o pipeline:

1. extrai features regionais ASPECTS em Python;
2. gera um programa `.sio` temporario para scoring regional;
3. executa o scoring no `souc`;
4. reconstrui o heatmap volumetrico no runner `Sounio`.

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

## Runs duraveis de workspace

O release `0.2.0` adiciona uma superficie de referencia para `generic_agent_task` com tres etapas duraveis:

1. capturar contexto do workspace;
2. executar a tarefa do agente com checkpoints e heartbeats;
3. escrever um resumo final de retomada.

Superficie HTTP:

- `POST /runs`
- `GET /runs/{run_id}`
- `GET /runs/{run_id}/resume-summary`
- `GET /runs/{run_id}/events`

CLI local rapida:

```bash
sounio-stroke-lab run-submit \
  --workspace-path /abs/path/to/workspace \
  --repo-path /abs/path/to/workspace/src \
  --task-name inventory-workspace
```

```bash
sounio-stroke-lab run-resume <run_id>
```

Helper equivalente:

```bash
labctl run submit \
  --workspace-path /abs/path/to/workspace \
  --repo-path /abs/path/to/workspace/src
```

```bash
labctl run resume <run_id>
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

Rodar validacao externa:

```bash
sounio-stroke-lab run-benchmark \
  --manifest /abs/path/to/train_manifest.json \
  --external-test-manifest /abs/path/to/external_test_manifest.json \
  --storage-root /abs/path/to/run_storage \
  --seed 13
```
