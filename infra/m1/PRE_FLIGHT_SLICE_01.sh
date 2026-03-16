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

extract_yaml_scalar() {
  local file="$1"
  local key="$2"
  awk -v key="$key" '
    $0 ~ "^[[:space:]]*" key ":[[:space:]]*" {
      sub("^[[:space:]]*" key ":[[:space:]]*", "", $0)
      print $0
      exit
    }
  ' "$file"
}

require_no_placeholders() {
  local file="$1"
  if rg -n 'REPLACE_WITH_' "$file" >/dev/null; then
    fail "placeholder still present in $file"
  fi
  pass "no placeholders in $file"
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

CFG="$ROOT/ceph-csi/ceph-csi-configmap.yaml"
SEC="$ROOT/ceph-csi/secret-template.yaml"
CEPHFS_VALUES="$ROOT/ceph-csi/cephfs-values.yaml"
RBD_VALUES="$ROOT/ceph-csi/rbd-values.yaml"
SC="$ROOT/ceph-csi/storageclasses.yaml"
NS="$ROOT/bootstrap/namespaces.yaml"

require_file "$NS"
require_file "$CFG"
require_file "$SEC"
require_file "$CEPHFS_VALUES"
require_file "$RBD_VALUES"
require_file "$SC"

require_no_placeholders "$SEC"

grep -q 'name: storage-system' "$NS" || fail "storage-system namespace missing from namespaces.yaml"
pass "storage-system namespace declared"

grep -q 'name: ceph-csi-config' "$CFG" || fail "ceph-csi-config ConfigMap name mismatch"
pass "ceph-csi-config ConfigMap name is fixed"

grep -q 'externallyManagedConfigmap: true' "$CEPHFS_VALUES" || fail "cephfs-values must use externallyManagedConfigmap"
grep -q 'configMapName: ceph-csi-config' "$CEPHFS_VALUES" || fail "cephfs-values configMapName mismatch"
pass "cephfs values reference ceph-csi-config"

grep -q 'externallyManagedConfigmap: true' "$RBD_VALUES" || fail "rbd-values must use externallyManagedConfigmap"
grep -q 'configMapName: ceph-csi-config' "$RBD_VALUES" || fail "rbd-values configMapName mismatch"
pass "rbd values reference ceph-csi-config"

grep -q 'name: ceph-csi-cephfs-secret' "$SEC" || fail "CephFS secret metadata name missing"
grep -q 'name: ceph-csi-rbd-secret' "$SEC" || fail "RBD secret metadata name missing"
pass "secret metadata names exist"

grep -q 'csi.storage.k8s.io/provisioner-secret-name: ceph-csi-cephfs-secret' "$SC" || fail "CephFS storage class secret reference mismatch"
grep -q 'csi.storage.k8s.io/provisioner-secret-name: ceph-csi-rbd-secret' "$SC" || fail "RBD storage class secret reference mismatch"
pass "storage classes reference expected secret names"

grep -q 'clusterID: f591bae9-eec5-4ae0-abfe-466ed7528c9e' "$SC" || fail "storage classes clusterID mismatch"
grep -q '"clusterID": "f591bae9-eec5-4ae0-abfe-466ed7528c9e"' "$CFG" || fail "configmap clusterID mismatch"
pass "clusterID is internally consistent"

grep -q 'fsName: cephfs' "$SC" || fail "CephFS fsName mismatch"
grep -q 'pool: cephfs_data' "$SC" || fail "CephFS pool mismatch"
grep -q 'subvolumeGroup: csi' "$SC" || fail "CephFS subvolumeGroup mismatch"
grep -q 'pool: rbd_ssd' "$SC" || fail "RBD pool mismatch"
pass "storage classes match frozen Ceph values"

cephfs_admin_id="$(extract_yaml_scalar "$SEC" "adminID")"
cephfs_admin_key="$(extract_yaml_scalar "$SEC" "adminKey")"
cephfs_user_id="$(extract_yaml_scalar "$SEC" "userID")"
cephfs_user_key="$(extract_yaml_scalar "$SEC" "userKey")"
rbd_user_id="$(awk '
  $0 ~ /^---/ { doc++ }
  doc >= 1 && $0 ~ /^[[:space:]]*userID:[[:space:]]*/ {
    sub("^[[:space:]]*userID:[[:space:]]*", "", $0)
    print $0
    exit
  }
' "$SEC")"
rbd_user_key="$(awk '
  $0 ~ /^---/ { doc++ }
  doc >= 1 && $0 ~ /^[[:space:]]*userKey:[[:space:]]*/ {
    sub("^[[:space:]]*userKey:[[:space:]]*", "", $0)
    print $0
    exit
  }
' "$SEC")"

require_equal "$cephfs_admin_id" "csi-cephfs" "CephFS adminID"
require_equal "$cephfs_user_id" "csi-cephfs" "CephFS userID"
require_non_empty "$cephfs_admin_key" "CephFS adminKey"
require_non_empty "$cephfs_user_key" "CephFS userKey"
require_equal "$cephfs_admin_key" "$cephfs_user_key" "CephFS admin/user key"

require_equal "$rbd_user_id" "csi-rbd" "RBD userID"
require_non_empty "$rbd_user_key" "RBD userKey"

printf 'PRE-FLIGHT SLICE 01: PASS\n'
