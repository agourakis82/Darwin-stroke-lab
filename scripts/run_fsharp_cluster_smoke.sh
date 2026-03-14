#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dotnet_root="${DOTNET_ROOT:-$HOME/.dotnet}"
if [[ -n "${DOTNET_BIN:-}" ]]; then
  dotnet_bin="$DOTNET_BIN"
elif command -v dotnet >/dev/null 2>&1; then
  dotnet_bin="$(command -v dotnet)"
else
  dotnet_bin="$dotnet_root/dotnet"
fi

mode="benchmark"
api_base="http://127.0.0.1:5124"
api_port="5124"
local_db_port="55433"
remote_db_host="127.0.0.1"
remote_db_port="55432"
ssh_host="${DARWIN_RESEARCH_OS_SSH_HOST:-demetrios@DEVdesktop}"
db_name="${DARWIN_RESEARCH_OS_DB_NAME:-darwin_fsharp_cluster_smoke}"
db_user="${DARWIN_RESEARCH_OS_DB_USER:-chiuratto_user}"
db_password="${DARWIN_RESEARCH_OS_DB_PASSWORD:-changeme}"
cluster_db_host="${DARWIN_RESEARCH_OS_K8S_DB_HOST:-192.168.3.225}"
cluster_db_port="${DARWIN_RESEARCH_OS_K8S_DB_PORT:-55432}"
dataset_pvc="${DARWIN_RESEARCH_OS_K8S_DATASET_PVC:-sounio-stroke-datasets}"
object_root="${DARWIN_RESEARCH_OS_OBJECT_ROOT:-$repo_root/.fsharp-cluster-smoke-objects}"
cluster_object_root="${DARWIN_RESEARCH_OS_K8S_OBJECT_ROOT:-/var/lib/darwin-research-os}"
worker_image="${DARWIN_RESEARCH_OS_WORKER_IMAGE:-darwin-research-os-worker:k3s-dev}"
kubectl_path="${DARWIN_RESEARCH_OS_KUBECTL_PATH:-$repo_root/scripts/devdesktop_kubectl.sh}"
manifest_path="/datasets/fixture-mini/small_benchmark_manifest.json"
seed="41"
kernel_path="/app/sounio/kernels/runtime_probe.sio"
require_snio="0"
timeout="120"
source_root="${SOUNIO_SOURCE_ROOT:-$HOME/sounio-lang-sounio}"
binary_root="${SOUNIO_BINARY_ROOT:-$source_root}"
skip_bundle="0"
skip_image="0"
skip_import="0"
keep_running="0"
api_pid=""
worker_pid=""
tunnel_pid=""
api_log_path=""
worker_log_path=""
tunnel_log_path=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

One-command local control-plane smoke for the F# rewrite lane.

Options:
  --mode benchmark|sounio   Smoke mode. Default: benchmark
  --manifest-path PATH      Benchmark manifest path inside the cluster. Default: $manifest_path
  --seed N                  Benchmark seed. Default: $seed
  --kernel-path PATH        Sounio kernel path inside the worker image. Default: $kernel_path
  --require-snio            Force SNIO-only execution for the sounio smoke
  --timeout SECONDS         Timeout for the smoke runner. Default: $timeout
  --api-port PORT           Local API port. Default: $api_port
  --db-name NAME            Smoke database name. Default: $db_name
  --source-root PATH        Sounio source root for bundle staging. Default: $source_root
  --binary-root PATH        Sounio binary root for bundle staging. Default: $binary_root
  --skip-bundle             Skip bundle staging
  --skip-image              Skip docker buildx image rebuild
  --skip-import             Skip remote k3s image import
  --keep-running            Keep tunnel/API/worker alive after the smoke
  -h, --help                Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      mode="$2"
      shift 2
      ;;
    --manifest-path)
      manifest_path="$2"
      shift 2
      ;;
    --seed)
      seed="$2"
      shift 2
      ;;
    --kernel-path)
      kernel_path="$2"
      shift 2
      ;;
    --require-snio)
      require_snio="1"
      shift
      ;;
    --timeout)
      timeout="$2"
      shift 2
      ;;
    --api-port)
      api_port="$2"
      api_base="http://127.0.0.1:${api_port}"
      shift 2
      ;;
    --db-name)
      db_name="$2"
      shift 2
      ;;
    --source-root)
      source_root="$2"
      shift 2
      ;;
    --binary-root)
      binary_root="$2"
      shift 2
      ;;
    --skip-bundle)
      skip_bundle="1"
      shift
      ;;
    --skip-image)
      skip_image="1"
      shift
      ;;
    --skip-import)
      skip_import="1"
      shift
      ;;
    --keep-running)
      keep_running="1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "$mode" != "benchmark" && "$mode" != "sounio" ]]; then
  echo "Unsupported mode: $mode" >&2
  exit 1
fi

if [[ ! -x "$dotnet_bin" ]]; then
  echo "Missing dotnet executable at $dotnet_bin" >&2
  exit 1
fi

cleanup() {
  if [[ "$keep_running" == "1" ]]; then
    return
  fi
  if [[ -n "$api_pid" ]]; then
    kill "$api_pid" 2>/dev/null || true
  fi
  if [[ -n "$worker_pid" ]]; then
    kill "$worker_pid" 2>/dev/null || true
  fi
  if [[ -n "$tunnel_pid" ]]; then
    kill "$tunnel_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT

tcp_ready() {
  python3 - "$1" "$2" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
sock = socket.socket()
sock.settimeout(1.0)
try:
    sock.connect((host, port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
}

dump_failure_context() {
  local header="$1"
  echo "$header" >&2
  if [[ -n "$tunnel_log_path" && -f "$tunnel_log_path" ]]; then
    echo "--- tunnel log ---" >&2
    tail -n 80 "$tunnel_log_path" >&2 || true
  fi
  if [[ -n "$api_log_path" && -f "$api_log_path" ]]; then
    echo "--- api log ---" >&2
    tail -n 120 "$api_log_path" >&2 || true
  fi
  if [[ -n "$worker_log_path" && -f "$worker_log_path" ]]; then
    echo "--- worker log ---" >&2
    tail -n 120 "$worker_log_path" >&2 || true
  fi
}

mkdir -p "$repo_root/.codex-run" "$object_root"
tunnel_log_path="$repo_root/.codex-run/fsharp-cluster-tunnel.log"
api_log_path="$repo_root/.codex-run/fsharp-cluster-api.log"
worker_log_path="$repo_root/.codex-run/fsharp-cluster-worker.log"

if [[ "$skip_bundle" != "1" ]]; then
  if [[ "$mode" == "sounio" ]]; then
    bash "$repo_root/scripts/prepare_cluster_sounio_bundle.sh" "$source_root" "$binary_root"
  else
    bash "$repo_root/scripts/prepare_cluster_sounio_bundle.sh" "$source_root" "$binary_root"
  fi
fi

if [[ "$skip_image" != "1" ]]; then
  docker buildx build --platform linux/amd64 --load \
    -f "$repo_root/fsharp/Dockerfile.worker" \
    -t "$worker_image" \
    "$repo_root"
fi

if [[ "$skip_import" != "1" ]]; then
  docker save "$worker_image" | ssh -o BatchMode=yes "$ssh_host" 'sudo k3s ctr images import -'
fi

export PATH="$(dirname "$dotnet_bin"):$dotnet_root:$PATH"
dotnet build "$repo_root/fsharp/src/Darwin.ResearchOs.Api/Darwin.ResearchOs.Api.fsproj" >/dev/null
dotnet build "$repo_root/fsharp/src/Darwin.ResearchOs.Worker/Darwin.ResearchOs.Worker.fsproj" >/dev/null

db_conn="Host=127.0.0.1;Port=${local_db_port};Username=${db_user};Password=${db_password};Database=${db_name}"
cluster_db_conn="Host=${cluster_db_host};Port=${cluster_db_port};Username=${db_user};Password=${db_password};Database=${db_name}"

ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -N \
  -L "${local_db_port}:${remote_db_host}:${remote_db_port}" \
  "$ssh_host" \
  >"$tunnel_log_path" 2>&1 &
tunnel_pid="$!"

for _ in $(seq 1 20); do
  if tcp_ready "127.0.0.1" "$local_db_port"; then
    break
  fi
  if ! kill -0 "$tunnel_pid" 2>/dev/null; then
    dump_failure_context "SSH tunnel exited before the local DB port became reachable."
    exit 1
  fi
  sleep 1
done
if ! tcp_ready "127.0.0.1" "$local_db_port"; then
  dump_failure_context "SSH tunnel did not make 127.0.0.1:${local_db_port} reachable."
  exit 1
fi

env \
  DOTNET_ROOT="$dotnet_root" \
  ASPNETCORE_URLS="$api_base" \
  DARWIN_RESEARCH_OS_DB_URL="$db_conn" \
  DARWIN_RESEARCH_OS_OBJECT_STORE="filesystem" \
  DARWIN_RESEARCH_OS_OBJECT_ROOT="$object_root" \
  DARWIN_RESEARCH_OS_WORKER_IMAGE="$worker_image" \
  DARWIN_RESEARCH_OS_K8S_DB_URL="$cluster_db_conn" \
  DARWIN_RESEARCH_OS_K8S_OBJECT_STORE="filesystem" \
  DARWIN_RESEARCH_OS_K8S_OBJECT_ROOT="$cluster_object_root" \
  DARWIN_RESEARCH_OS_K8S_DATASET_PVC="$dataset_pvc" \
  DARWIN_RESEARCH_OS_K8S_SOUNIO_ROOT="/opt/sounio" \
  DARWIN_RESEARCH_OS_K8S_SOUNIO_STDLIB_PATH="/opt/sounio/stdlib" \
  DARWIN_RESEARCH_OS_K8S_SOUNIO_RUNTIME_LIB_PATH="/opt/sounio/runtime/target/release/libsounio_runtime.so" \
  "$dotnet_bin" "$repo_root/fsharp/src/Darwin.ResearchOs.Api/bin/Debug/net8.0/Darwin.ResearchOs.Api.dll" \
  >"$api_log_path" 2>&1 &
api_pid="$!"

env \
  DOTNET_ROOT="$dotnet_root" \
  DARWIN_RESEARCH_OS_DB_URL="$db_conn" \
  DARWIN_RESEARCH_OS_OBJECT_STORE="filesystem" \
  DARWIN_RESEARCH_OS_OBJECT_ROOT="$object_root" \
  DARWIN_RESEARCH_OS_KUBECTL_PATH="$kubectl_path" \
  DARWIN_RESEARCH_OS_K8S_DB_URL="$cluster_db_conn" \
  DARWIN_RESEARCH_OS_K8S_OBJECT_STORE="filesystem" \
  DARWIN_RESEARCH_OS_K8S_OBJECT_ROOT="$cluster_object_root" \
  DARWIN_RESEARCH_OS_K8S_DATASET_PVC="$dataset_pvc" \
  DARWIN_RESEARCH_OS_K8S_SOUNIO_ROOT="/opt/sounio" \
  DARWIN_RESEARCH_OS_K8S_SOUNIO_STDLIB_PATH="/opt/sounio/stdlib" \
  DARWIN_RESEARCH_OS_K8S_SOUNIO_RUNTIME_LIB_PATH="/opt/sounio/runtime/target/release/libsounio_runtime.so" \
  "$dotnet_bin" "$repo_root/fsharp/src/Darwin.ResearchOs.Worker/bin/Debug/net8.0/darwin-research-os-worker.dll" \
  >"$worker_log_path" 2>&1 &
worker_pid="$!"

for _ in $(seq 1 30); do
  if curl -sf "$api_base/health" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$api_pid" 2>/dev/null; then
    dump_failure_context "Rewrite API exited before becoming healthy."
    exit 1
  fi
  sleep 1
done
if ! curl -sf "$api_base/health" >/dev/null 2>&1; then
  dump_failure_context "Rewrite API never became healthy."
  exit 1
fi

smoke_args=(python3 "$repo_root/scripts/fsharp_cluster_smoke.py" --api-base "$api_base" --mode "$mode" --timeout "$timeout")
if [[ "$mode" == "benchmark" ]]; then
  smoke_args+=(--manifest-path "$manifest_path" --seed "$seed")
else
  smoke_args+=(--kernel-path "$kernel_path")
  if [[ "$require_snio" == "1" ]]; then
    smoke_args+=(--require-snio)
  fi
fi

"${smoke_args[@]}"

if [[ "$keep_running" == "1" ]]; then
  cat <<EOF
Left running:
  tunnel_pid: $tunnel_pid
  api_pid: $api_pid
  worker_pid: $worker_pid
  api_base: $api_base
EOF
fi
