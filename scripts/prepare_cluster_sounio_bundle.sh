#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
default_source_root="${SOUNIO_SOURCE_ROOT:-$HOME/sounio-lang-sounio}"
source_root="${1:-$default_source_root}"
default_binary_root="${SOUNIO_BINARY_ROOT:-$source_root}"
binary_root="${2:-$default_binary_root}"
skip_prebuilt_bundle="${SOUNIO_SKIP_PREBUILT_BUNDLE:-0}"
target_root="$repo_root/fsharp/cluster-sounio/source"
bundle_root="$repo_root/fsharp/cluster-sounio/bundle"

if [[ ! -f "$source_root/compiler/Cargo.toml" ]]; then
  echo "Missing compiler/Cargo.toml under $source_root" >&2
  exit 1
fi

if [[ ! -d "$source_root/stdlib" ]]; then
  echo "Missing stdlib directory under $source_root" >&2
  exit 1
fi

if [[ "$skip_prebuilt_bundle" != "1" ]]; then
  if [[ ! -x "$binary_root/compiler/target/release/souc" ]]; then
    echo "Missing compiler/target/release/souc under $binary_root" >&2
    exit 1
  fi

  if [[ ! -d "$binary_root/stdlib" ]]; then
    echo "Missing stdlib directory under $binary_root" >&2
    exit 1
  fi
fi

rm -rf "$target_root"
rm -rf "$bundle_root"
mkdir -p "$target_root"

rsync -a \
  --delete \
  --exclude '.git' \
  --exclude 'target' \
  --exclude '.DS_Store' \
  "$source_root/" \
  "$target_root/"

if [[ "$skip_prebuilt_bundle" != "1" ]]; then
  mkdir -p "$bundle_root/compiler/target/release"
  cp "$binary_root/compiler/target/release/souc" "$bundle_root/compiler/target/release/souc"

  rsync -a \
    --delete \
    --exclude '.git' \
    --exclude 'target' \
    --exclude '.DS_Store' \
    "$binary_root/stdlib/" \
    "$bundle_root/stdlib/"

  if [[ -d "$source_root/self-hosted" ]]; then
    rsync -a \
      --delete \
      --exclude '.git' \
      --exclude 'target' \
      --exclude '.DS_Store' \
      "$source_root/self-hosted/" \
      "$bundle_root/self-hosted/"
  elif [[ -d "$binary_root/self-hosted" ]]; then
    rsync -a \
      --delete \
      --exclude '.git' \
      --exclude 'target' \
      --exclude '.DS_Store' \
      "$binary_root/self-hosted/" \
      "$bundle_root/self-hosted/"
  fi
fi

cat <<EOF
Prepared cluster Sounio bundle.
  source_root: $source_root
  binary_root: $binary_root
  target_root: $target_root
  bundle_root: $bundle_root
  skip_prebuilt_bundle: $skip_prebuilt_bundle

Next step:
  docker buildx build --platform linux/amd64 -t darwin-research-os-worker:k3s-dev -f "$repo_root/fsharp/Dockerfile.worker" --load "$repo_root"
EOF
