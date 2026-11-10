#!/usr/bin/env bash
# Fail if tracked source disables TLS verification or binds to all interfaces.
# Tests and docs may mention these patterns (to test that they are rejected); nothing else may.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

pattern='ssl[[:space:]]*=[[:space:]]*False|verify[[:space:]]*=[[:space:]]*False|CERT_NONE|check_hostname[[:space:]]*=[[:space:]]*False|0\.0\.0\.0'
allow=(':!tests/' ':!docs/' ':!*.md' ':!scripts/check_insecure_patterns.sh' ':!uv.lock')

if matches=$(git grep --untracked -nIE "$pattern" -- . "${allow[@]}"); then
  echo "Insecure pattern found (TLS verification must stay on; HTTP binds to loopback):"
  echo "$matches"
  exit 1
fi
echo "No insecure patterns found."
