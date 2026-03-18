# Darwin Research OS F# Lane

This is the parallel rewrite lane for the Darwin Research OS.

Goals of this lane:

- replace the Python control plane with a typed F# implementation
- keep the current REST semantics stable during migration
- keep `Sounio` as the scientific kernel
- keep `Stan` as the probabilistic baseline lane

Current status:

- Wave 1 is active: contracts are frozen from Python and the F# solution skeleton is in place.
- Wave 2 has started: campaigns, programs, portfolio semantics, an in-memory research store, local object-store parity, and local benchmark/agent workers now exist in the F# lane with parity-oriented tests.
- The F# API now resolves state/object storage and exposes store-backed rewrite endpoints for jobs, benchmark runs, campaigns, programs, and portfolio summaries.
- The F# API now also probes the local Sounio runtime through `/rewrite/sounio-runtime`, distinguishing a usable native toolchain from a promotion-grade clean checkout.
- The F# API now also exposes `/rewrite/sounio-runtime/abi`, which inventories the current dylib exports, distinguishes source-only scientific FFI from host-loadable runtime exports, and tracks the new upstream SNIO kernel/session embedding ABI.
- The runtime diagnostics now also distinguish what is already exported by the host-loadable dylib from what exists only in the newer native backend sources, so we can see the exact scientific FFI export gap.
- That runtime probe now consumes the native `libsounio_runtime` ABI directly for epistemic/uncertainty operations, inventories the new upstream SNIO session/kernel embedding surface, and reports explicitly when the buildable `souc` binary still lags the source tree.
- The local SNIO client layer now mirrors the official upstream `interop/fsharp` surface from `Sounio`, so Darwin is no longer maintaining a separate ad hoc protocol subset; the Darwin-specific layer is now focused on runtime discovery, probing, and fallback policy.
- The first write path is now live in the F# lane through `POST /rewrite/sounio-runtime/run`, which is now `SNIO`-preferred and `CLI`-fallback: it will try the upstream session/kernel embedding path first and only fall back to `souc check/run` when the selected binary root cannot serve the new `serve_entry.sio` flow yet.
- That direct Sounio write path now also supports strict mode through `RequireSnio`: if the upstream SNIO/session path is required and cannot be used successfully, the lane fails explicitly instead of silently falling back to CLI execution.
- The first queued operational path is now live too: `POST /rewrite/jobs/benchmark` submits a benchmark job, and `Darwin.ResearchOs.Worker` drains the same Postgres-backed queue without Python in the loop.
- The queued operational model now also covers `Sounio` kernel runs: `POST /rewrite/jobs/sounio-runtime` enqueues a `sounio_runtime` job, and the F# worker persists stdout/diagnostics/report artifacts with `snio_attempted` and `snio_used` metadata.
- `campaigns` and `programs` can now ride the same queue model: `POST /rewrite/campaigns/queued` submits benchmark jobs only, `GET /rewrite/campaigns/{id}` reconciles them against live jobs/runs, and `POST /rewrite/programs/{id}/campaigns` opens a queued campaign already attached to a program.
- `campaigns` and `programs` now also emit deployment-ready cluster plans: `GET /rewrite/campaigns/{id}/k8s-plan` renders the active workload plus launchable follow-up jobs, and `GET /rewrite/programs/{id}/k8s-plan` rolls those plans up at program level without going back through Python.
- Those cluster plans can now be persisted and dispatched from the F# lane too: `POST /rewrite/campaigns/{id}/k8s-plan/materialize` and `POST /rewrite/programs/{id}/k8s-plan/materialize` persist JSON artifacts, while `POST /rewrite/campaigns/{id}/k8s-plan/launch` and `POST /rewrite/programs/{id}/k8s-plan/launch` stage `k8s_dispatch` jobs in the rewrite store.
- `k8s_dispatch` now has a real worker path: `Darwin.ResearchOs.Worker` runs a `kubectl`-backed dispatcher that submits, polls, and cancels cluster `Job`s, and `POST /rewrite/jobs/{id}/cancel` now flips `cancel_requested` for the worker loop.
- The K8s benchmark renderer now preserves the experiment knobs that matter operationally: manifest, optional external manifest, train split, test split, and seed.
- The same cluster lane now also supports direct remote Sounio runtime launches through `POST /rewrite/jobs/sounio-runtime/launch-k8s`, and the worker image can optionally bundle a Linux-built `Sounio` toolchain under `/opt/sounio` for `sounio_runtime_remote` jobs.
- A reproducible dev smoke now exists in [CLUSTER_SMOKE.md](/Users/demetriosagourakis/Documents/New%20project/fsharp/CLUSTER_SMOKE.md), plus helper scripts in [/Users/demetriosagourakis/Documents/New project/scripts/prepare_cluster_sounio_bundle.sh](/Users/demetriosagourakis/Documents/New%20project/scripts/prepare_cluster_sounio_bundle.sh), [/Users/demetriosagourakis/Documents/New project/scripts/fsharp_cluster_smoke.py](/Users/demetriosagourakis/Documents/New%20project/scripts/fsharp_cluster_smoke.py), and the one-command wrapper [/Users/demetriosagourakis/Documents/New project/scripts/run_fsharp_cluster_smoke.sh](/Users/demetriosagourakis/Documents/New%20project/scripts/run_fsharp_cluster_smoke.sh).
- Repo-level entrypoints now exist in [Makefile](/Users/demetriosagourakis/Documents/New%20project/Makefile): `make smoke-cluster-sounio`, `make smoke-cluster-benchmark`, and the `*-fast` variants that skip bundle/image/import when those artifacts already exist.
- A manual self-hosted GitHub Actions workflow now exists in [.github/workflows/fsharp-cluster-smoke.yml](/Users/demetriosagourakis/Documents/New%20project/.github/workflows/fsharp-cluster-smoke.yml), so the same cluster smokes can be launched from the Actions UI on a runner that has Docker, .NET, Python, SSH access to `DEVdesktop`, and the local `Sounio` checkout.
- Python remains the source of truth until the F# lane reaches parity.

Build locally with:

```bash
export PATH="$HOME/.dotnet:$PATH"
dotnet build fsharp/DarwinResearchOs.sln
dotnet test fsharp/DarwinResearchOs.sln
```

Live Postgres smoke:

```bash
export PATH="$HOME/.dotnet:$PATH"
pg_ctl -D /opt/homebrew/var/postgresql@16 -l .tmp/postgres-rewrite.log -w start
createdb -h 127.0.0.1 -p 5432 stroke_lab_fsharp_smoke

DARWIN_RESEARCH_OS_DB_URL="Host=127.0.0.1;Port=5432;Username=$USER;Database=stroke_lab_fsharp_smoke" \
DARWIN_RESEARCH_OS_OBJECT_ROOT="$PWD/.rewrite-cache/postgres-smoke" \
dotnet fsi --lib:$HOME/.dotnet/shared/Microsoft.AspNetCore.App/8.0.24 \
  scripts/fsharp_postgres_smoke.fsx

DARWIN_RESEARCH_OS_DB_URL="Host=127.0.0.1;Port=5432;Username=$USER;Database=stroke_lab_fsharp_smoke" \
DARWIN_RESEARCH_OS_OBJECT_ROOT="$PWD/.rewrite-cache/postgres-api" \
dotnet run --project fsharp/src/Darwin.ResearchOs.Api/Darwin.ResearchOs.Api.fsproj --urls http://127.0.0.1:5101
```

Then verify:

```bash
curl -fsS http://127.0.0.1:5101/rewrite/storage
curl -fsS http://127.0.0.1:5101/rewrite/campaigns
curl -fsS http://127.0.0.1:5101/rewrite/programs
curl -fsS http://127.0.0.1:5101/rewrite/campaigns/<campaign-id>/k8s-plan
curl -fsS http://127.0.0.1:5101/rewrite/programs/<program-id>/k8s-plan
curl -fsS -X POST http://127.0.0.1:5101/rewrite/campaigns/<campaign-id>/k8s-plan/materialize
curl -fsS -X POST http://127.0.0.1:5101/rewrite/campaigns/<campaign-id>/k8s-plan/launch
curl -fsS -X POST http://127.0.0.1:5101/rewrite/programs/<program-id>/k8s-plan/launch
curl -fsS -X POST http://127.0.0.1:5101/rewrite/jobs/<job-id>/cancel
curl -fsS http://127.0.0.1:5101/rewrite/portfolio/programs
curl -fsS http://127.0.0.1:5101/rewrite/sounio-runtime
curl -fsS http://127.0.0.1:5101/rewrite/sounio-runtime/abi
curl -fsS -X POST http://127.0.0.1:5101/rewrite/sounio-runtime/run \
  -H 'Content-Type: application/json' \
  -d '{"label":"Mac Native Probe Run","persistArtifacts":true,"requireSnio":false}'
curl -fsS -X POST http://127.0.0.1:5101/rewrite/jobs/sounio-runtime \
  -H 'Content-Type: application/json' \
  -d '{"label":"Queued SNIO Probe","persistArtifacts":true,"requireSnio":true}'
curl -fsS -X POST http://127.0.0.1:5101/rewrite/jobs/sounio-runtime/launch-k8s \
  -H 'Content-Type: application/json' \
  -d '{"label":"Cluster SNIO Probe","kernelPath":"/app/sounio/kernels/runtime_probe.sio","persistArtifacts":true,"requireSnio":false}'
curl -fsS http://127.0.0.1:5101/rewrite/artifacts/sounio_kernel_run/<execution-id>
```

Relevant rewrite RFCs:

- [Native kernel execution ABI gap](/Users/demetriosagourakis/Documents/New%20project/docs/rewrite/sounio_native_kernel_abi_rfc.md)
- [Scientific native backend export gap](/Users/demetriosagourakis/Documents/New%20project/docs/rewrite/sounio_scientific_ffi_exports_rfc.md)

Queued benchmark smoke with separate API + worker:

```bash
DARWIN_RESEARCH_OS_DB_URL="Host=127.0.0.1;Port=5432;Username=$USER;Database=stroke_lab_fsharp_queue_smoke" \
DARWIN_RESEARCH_OS_OBJECT_ROOT="$PWD/.rewrite-cache/queue-smoke" \
dotnet run --project fsharp/src/Darwin.ResearchOs.Api/Darwin.ResearchOs.Api.fsproj --urls http://127.0.0.1:5104

DARWIN_RESEARCH_OS_DB_URL="Host=127.0.0.1;Port=5432;Username=$USER;Database=stroke_lab_fsharp_queue_smoke" \
DARWIN_RESEARCH_OS_OBJECT_ROOT="$PWD/.rewrite-cache/queue-smoke" \
dotnet run --project fsharp/src/Darwin.ResearchOs.Worker/Darwin.ResearchOs.Worker.fsproj

curl -fsS -X POST http://127.0.0.1:5104/rewrite/jobs/benchmark \
  -H 'Content-Type: application/json' \
  -d '{"datasetManifestPath":"/tmp/fsharp-queue-manifest.json","trainSplit":"train","testSplit":"test","seed":18}'
```
