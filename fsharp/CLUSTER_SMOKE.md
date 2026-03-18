# F# Cluster Smoke

This runbook reproduces the cluster smoke that proves the F# lane can launch
cluster jobs without the Python worker in the pod hot path.

## Fast path

If you want the whole local control-plane smoke in one command, use:

```bash
bash /Users/demetriosagourakis/Documents/New\ project/scripts/run_fsharp_cluster_smoke.sh --mode sounio
```

Or for the benchmark path:

```bash
bash /Users/demetriosagourakis/Documents/New\ project/scripts/run_fsharp_cluster_smoke.sh --mode benchmark
```

The wrapper stages the bundle, rebuilds/imports the worker image, opens the
Postgres tunnel, starts the local API and worker, runs the smoke, and tears the
local processes down unless `--keep-running` is set.

Repo-level shortcuts are also available in [Makefile](/Users/demetriosagourakis/Documents/New%20project/Makefile):

```bash
make smoke-cluster-sounio
make smoke-cluster-benchmark
```

If the bundle, worker image, and remote import are already fresh, use the fast
variants:

```bash
make smoke-cluster-sounio-fast
make smoke-cluster-benchmark-fast
```

## GitHub Actions

A manual self-hosted workflow now exists at [.github/workflows/fsharp-cluster-smoke.yml](/Users/demetriosagourakis/Documents/New%20project/.github/workflows/fsharp-cluster-smoke.yml).

It assumes the runner:

- is a self-hosted macOS runner
- has Docker/buildx, Python 3, and .NET 8 available
- can SSH to `DEVdesktop`
- has the expected local `Sounio` checkout available, or repo variables overriding `SOUNIO_SOURCE_ROOT` / `SOUNIO_BINARY_ROOT`

The workflow exposes two inputs:

- `mode = sounio | benchmark`
- `path = fast | full`

`fast` reuses the current bundle/image/import state. `full` reruns bundle
staging, worker image build, and remote image import before the smoke itself.

## 1. Prepare the Sounio bundle for cluster images

If you want to smoke `sounio_runtime_remote`, stage a cluster-safe `Sounio`
bundle into the worker image build context first.

For the current Linux cluster smoke, the safest path is source-build mode from
the older checkout that still compiles cleanly in the container:

```bash
SOUNIO_SKIP_PREBUILT_BUNDLE=1 \
  bash /Users/demetriosagourakis/Documents/New\ project/scripts/prepare_cluster_sounio_bundle.sh \
  /Users/demetriosagourakis/sounio
```

By default this copies source and prebuilt artifacts from
`~/sounio-lang-sounio` into:

`/Users/demetriosagourakis/Documents/New project/fsharp/cluster-sounio/source`
`/Users/demetriosagourakis/Documents/New project/fsharp/cluster-sounio/bundle`

If you want to separate the source tree from the known-buildable binary tree,
pass the binary root as the second argument or export `SOUNIO_BINARY_ROOT`.

The worker Dockerfile will prefer the staged prebuilt bundle under
`fsharp/cluster-sounio/bundle` and only try to compile Linux `souc` plus the
Linux `libsounio_runtime.so` from `source/` as a fallback.

## 2. Build and import the F# worker image

```bash
docker buildx build --platform linux/amd64 \
  -t darwin-research-os-worker:k3s-dev \
  -f /Users/demetriosagourakis/Documents/New\ project/fsharp/Dockerfile.worker \
  --load /Users/demetriosagourakis/Documents/New\ project

docker save darwin-research-os-worker:k3s-dev | \
  ssh -o BatchMode=yes demetrios@DEVdesktop 'sudo k3s ctr images import -'
```

## 3. Tunnel the remote Postgres locally

```bash
ssh -o BatchMode=yes -N -L 55433:127.0.0.1:55432 demetrios@DEVdesktop
```

## 4. Start the rewrite API and worker locally

API:

```bash
export PATH="$HOME/.dotnet:$PATH"
export ASPNETCORE_URLS="http://127.0.0.1:5124"
export DARWIN_RESEARCH_OS_DB_URL="Host=127.0.0.1;Port=55433;Username=chiuratto_user;Password=changeme;Database=darwin_fsharp_cluster_smoke"
export DARWIN_RESEARCH_OS_OBJECT_STORE="filesystem"
export DARWIN_RESEARCH_OS_OBJECT_ROOT="/Users/demetriosagourakis/Documents/New project/.fsharp-cluster-smoke-objects"
export DARWIN_RESEARCH_OS_WORKER_IMAGE="darwin-research-os-worker:k3s-dev"
export DARWIN_RESEARCH_OS_K8S_DB_URL="Host=192.168.3.225;Port=55432;Username=chiuratto_user;Password=changeme;Database=darwin_fsharp_cluster_smoke"
export DARWIN_RESEARCH_OS_K8S_OBJECT_STORE="filesystem"
export DARWIN_RESEARCH_OS_K8S_OBJECT_ROOT="/var/lib/darwin-research-os"
export DARWIN_RESEARCH_OS_K8S_DATASET_PVC="sounio-stroke-datasets"
export DARWIN_RESEARCH_OS_K8S_SOUNIO_ROOT="/opt/sounio"
export DARWIN_RESEARCH_OS_K8S_SOUNIO_STDLIB_PATH="/opt/sounio/stdlib"
export DARWIN_RESEARCH_OS_K8S_SOUNIO_RUNTIME_LIB_PATH="/opt/sounio/runtime/target/release/libsounio_runtime.so"
dotnet run --no-launch-profile --project /Users/demetriosagourakis/Documents/New\ project/fsharp/src/Darwin.ResearchOs.Api/Darwin.ResearchOs.Api.fsproj
```

Worker:

```bash
export PATH="$HOME/.dotnet:$PATH"
export DARWIN_RESEARCH_OS_DB_URL="Host=127.0.0.1;Port=55433;Username=chiuratto_user;Password=changeme;Database=darwin_fsharp_cluster_smoke"
export DARWIN_RESEARCH_OS_OBJECT_STORE="filesystem"
export DARWIN_RESEARCH_OS_OBJECT_ROOT="/Users/demetriosagourakis/Documents/New project/.fsharp-cluster-smoke-objects"
export DARWIN_RESEARCH_OS_KUBECTL_PATH="/Users/demetriosagourakis/Documents/New project/scripts/devdesktop_kubectl.sh"
export DARWIN_RESEARCH_OS_K8S_DB_URL="Host=192.168.3.225;Port=55432;Username=chiuratto_user;Password=changeme;Database=darwin_fsharp_cluster_smoke"
export DARWIN_RESEARCH_OS_K8S_OBJECT_STORE="filesystem"
export DARWIN_RESEARCH_OS_K8S_OBJECT_ROOT="/var/lib/darwin-research-os"
export DARWIN_RESEARCH_OS_K8S_DATASET_PVC="sounio-stroke-datasets"
export DARWIN_RESEARCH_OS_K8S_SOUNIO_ROOT="/opt/sounio"
export DARWIN_RESEARCH_OS_K8S_SOUNIO_STDLIB_PATH="/opt/sounio/stdlib"
export DARWIN_RESEARCH_OS_K8S_SOUNIO_RUNTIME_LIB_PATH="/opt/sounio/runtime/target/release/libsounio_runtime.so"
dotnet run --no-launch-profile --project /Users/demetriosagourakis/Documents/New\ project/fsharp/src/Darwin.ResearchOs.Worker/Darwin.ResearchOs.Worker.fsproj
```

## 5. Run the benchmark smoke

```bash
python3 /Users/demetriosagourakis/Documents/New\ project/scripts/fsharp_cluster_smoke.py \
  --api-base http://127.0.0.1:5124 \
  --mode benchmark \
  --manifest-path /datasets/fixture-mini/small_benchmark_manifest.json \
  --seed 41
```

Expected signal:

- `dispatch_job.status = completed`
- `target_job.status = completed`
- the K8s job command is `/app/darwin-research-os-worker run-benchmark-job --job-id ...`

## 6. Run the Sounio runtime smoke

```bash
python3 /Users/demetriosagourakis/Documents/New\ project/scripts/fsharp_cluster_smoke.py \
  --api-base http://127.0.0.1:5124 \
  --mode sounio \
  --kernel-path /app/sounio/kernels/runtime_probe.sio
```

If you want to force the remote kernel execution to reject CLI fallback:

```bash
python3 /Users/demetriosagourakis/Documents/New\ project/scripts/fsharp_cluster_smoke.py \
  --api-base http://127.0.0.1:5124 \
  --mode sounio \
  --kernel-path /app/sounio/kernels/runtime_probe.sio \
  --require-snio
```

Expected signal:

- `dispatch_job.status = completed`
- `target_job.status = completed`
- the K8s job command is `/app/darwin-research-os-worker run-sounio-runtime-job --job-id ...`
