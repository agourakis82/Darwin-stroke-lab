#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

pass() {
  printf 'OK: %s\n' "$*"
}

require_file() {
  local file="$1"
  [[ -s "$file" ]] || fail "missing or empty file: $file"
  pass "file present: $file"
}

require_no_placeholders() {
  local file="$1"
  if rg -n 'REPLACE_WITH_' "$file" >/dev/null; then
    fail "placeholder still present in $file"
  fi
  pass "no placeholders in $file"
}

extract_secret_field() {
  local file="$1"
  local secret="$2"
  local field="$3"
  awk -v secret="$secret" -v field="$field" '
    $0 ~ /^---/ { in_secret=0 }
    $0 ~ "^[[:space:]]*name:[[:space:]]*" secret "$" { in_secret=1; next }
    in_secret && $0 ~ "^[[:space:]]*" field ":[[:space:]]*" {
      sub("^[[:space:]]*" field ":[[:space:]]*", "", $0)
      print $0
      exit
    }
  ' "$file"
}

extract_url_password() {
  local url="$1"
  printf '%s\n' "$url" | sed -n 's#.*://[^:]*:\([^@]*\)@.*#\1#p'
}

require_equal() {
  local left="$1"
  local right="$2"
  local label="$3"
  [[ "$left" == "$right" ]] || fail "$label mismatch"
  pass "$label match"
}

require_non_empty() {
  local value="$1"
  local label="$2"
  [[ -n "$value" ]] || fail "$label is empty"
  pass "$label is non-empty"
}

ensure_slice_01_success() {
  command -v kubectl >/dev/null 2>&1 || fail "kubectl is required to verify slice 01 success"
  kubectl get sc proxmox-cephfs proxmox-ceph-rbd >/dev/null 2>&1 || fail "slice 01 storage classes are not present"
  kubectl get csidriver cephfs.csi.ceph.com rbd.csi.ceph.com >/dev/null 2>&1 || fail "slice 01 CSI drivers are not present"
  local pod_count
  pod_count="$(kubectl get pods -n storage-system --no-headers 2>/dev/null | wc -l | tr -d ' ')"
  [[ "${pod_count:-0}" -gt 0 ]] || fail "storage-system has no pods; slice 01 does not look complete"
  local unhealthy
  unhealthy="$(kubectl get pods -n storage-system --no-headers 2>/dev/null | awk '$2 !~ /^[0-9]+\/[0-9]+$/ || $2 !~ ("^" substr($2, index($2, "/")+1) "$") || ($3 != "Running" && $3 != "Completed")')"
  [[ -z "$unhealthy" ]] || fail "storage-system contains non-ready pods; slice 01 is not healthy"
  pass "slice 01 success verified by read-only cluster checks"
}

OPERATOR="$ROOT/postgres/operator.yaml"
CLUSTER="$ROOT/postgres/cluster.yaml"
SECRETS="$ROOT/postgres/bootstrap-secrets.template.yaml"
SQL="$ROOT/postgres/databases.sql"
CODER_HELM="$ROOT/coder/helmchart.yaml"
TEMPORAL_VALUES="$ROOT/temporal/helm-values.yaml"
WORKERS="$ROOT/temporal/workers.yaml"

ensure_slice_01_success

require_file "$OPERATOR"
require_file "$CLUSTER"
require_file "$SECRETS"
require_file "$SQL"
require_file "$CODER_HELM"
require_file "$TEMPORAL_VALUES"
require_file "$WORKERS"

require_no_placeholders "$SECRETS"

grep -q 'name: lab-postgres-app' "$SECRETS" || fail "lab-postgres-app secret missing"
grep -q 'name: coder-db-url' "$SECRETS" || fail "coder-db-url secret missing"
grep -q 'name: temporal-db-url' "$SECRETS" || fail "temporal-db-url secret missing"
grep -q 'name: lab-runs-db-url' "$SECRETS" || fail "lab-runs-db-url secret missing"
pass "expected PostgreSQL secrets exist"

grep -q 'name: lab-postgres-app' "$CLUSTER" || fail "cluster bootstrap secret reference mismatch"
grep -q 'storageClass: proxmox-ceph-rbd' "$CLUSTER" || fail "cluster storageClass must be proxmox-ceph-rbd"
grep -q 'name: coder' "$CLUSTER" || fail "coder role missing from cluster manifest"
grep -q 'name: temporal' "$CLUSTER" || fail "temporal role missing from cluster manifest"
grep -q 'name: lab_runs' "$CLUSTER" || fail "lab_runs role missing from cluster manifest"
pass "cluster manifest matches frozen PostgreSQL wiring"

grep -q 'CODER_PG_CONNECTION_URL' "$CODER_HELM" || fail "Coder no longer references coder-db-url"
grep -q 'name: coder-db-url' "$CODER_HELM" || fail "Coder secret reference mismatch"
grep -q 'existingSecret: temporal-db-url' "$TEMPORAL_VALUES" || fail "Temporal secret reference mismatch"
grep -q 'name: lab-runs-db-url' "$WORKERS" || fail "lab-runs secret reference mismatch"
pass "downstream manifest references are internally consistent"

grep -q '^CREATE DATABASE coder OWNER coder;' "$SQL" || fail "coder database creation missing"
grep -q '^CREATE DATABASE temporal OWNER temporal;' "$SQL" || fail "temporal database creation missing"
grep -q '^CREATE DATABASE temporal_visibility OWNER temporal;' "$SQL" || fail "temporal_visibility database creation missing"
grep -q '^CREATE DATABASE lab_runs OWNER lab_runs;' "$SQL" || fail "lab_runs database creation missing"
pass "database bootstrap SQL contains expected databases"

owner_password="$(extract_secret_field "$SECRETS" "lab-postgres-app" "password")"
coder_password="$(extract_secret_field "$SECRETS" "coder-db-url" "password")"
coder_url="$(extract_secret_field "$SECRETS" "coder-db-url" "url")"
temporal_password="$(extract_secret_field "$SECRETS" "temporal-db-url" "password")"
temporal_url="$(extract_secret_field "$SECRETS" "temporal-db-url" "url")"
temporal_visibility_url="$(extract_secret_field "$SECRETS" "temporal-db-url" "visibility_url")"
lab_runs_password="$(extract_secret_field "$SECRETS" "lab-runs-db-url" "password")"
lab_runs_url="$(extract_secret_field "$SECRETS" "lab-runs-db-url" "url")"

require_non_empty "$owner_password" "bootstrap owner password"
require_non_empty "$coder_password" "coder password"
require_non_empty "$temporal_password" "temporal password"
require_non_empty "$lab_runs_password" "lab_runs password"

require_equal "$(extract_url_password "$coder_url")" "$coder_password" "coder URL/password"
require_equal "$(extract_url_password "$temporal_url")" "$temporal_password" "temporal URL/password"
require_equal "$(extract_url_password "$temporal_visibility_url")" "$temporal_password" "temporal visibility URL/password"
require_equal "$(extract_url_password "$lab_runs_url")" "$lab_runs_password" "lab_runs URL/password"

printf 'PRE-FLIGHT SLICE 02: PASS\n'
