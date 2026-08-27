#!/usr/bin/env bash
# TS-side gate step: type-check every adapter whose dependencies are installed.
# Prints an explicit SKIP per adapter without node_modules so machines without
# Node never fail the gate; any installed-but-failing adapter fails the step.
set -u
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT/harnesses" || exit 1

failed=0
checked=0
skipped=0
for pkg in "$REPO_ROOT"/harnesses/*/; do
  name="$(basename "$pkg")"
  [ -f "$pkg/package.json" ] || continue
  if [ ! -d "$pkg/node_modules" ]; then
    echo "SKIP: $name (node_modules 未安装)"
    skipped=$((skipped + 1))
    continue
  fi
  if [ ! -f "$pkg/tsconfig.json" ]; then
    echo "SKIP: $name (无 tsconfig.json)"
    skipped=$((skipped + 1))
    continue
  fi
  echo "tsc --noEmit: $name"
  if (cd "$pkg" && ./node_modules/.bin/tsc --noEmit); then
    checked=$((checked + 1))
  else
    echo "FAIL: $name tsc --noEmit"
    failed=$((failed + 1))
  fi
done

echo "ts-gate: checked=$checked skipped=$skipped failed=$failed"
[ "$failed" -eq 0 ]
