#!/usr/bin/env bash
set -euo pipefail

host_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=host/host_run.sh
source "$host_root/host_run.sh"

host_run_supervise_contract "$@"
