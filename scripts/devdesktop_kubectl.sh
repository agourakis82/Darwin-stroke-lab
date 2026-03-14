#!/usr/bin/env bash
set -euo pipefail

host="${DARWIN_RESEARCH_OS_SSH_KUBECTL_HOST:-demetrios@DEVdesktop}"

exec ssh -o BatchMode=yes "$host" kubectl "$@"
