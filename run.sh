#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
export PYTHONDONTWRITEBYTECODE=1
if [ "$#" -eq 0 ]; then set -- gui; fi
exec /usr/bin/python3 -m sc830 "$@"
