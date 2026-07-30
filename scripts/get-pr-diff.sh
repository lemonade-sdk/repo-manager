#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${script_dir}/../skills/pr-review/scripts/get-pr-diff.sh" "$@"
