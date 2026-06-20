#!/usr/bin/env bash
# Build iteris-family-operator-guide.pdf
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/docs"
xelatex -interaction=nonstopmode iteris-family-operator-guide.tex >/dev/null
xelatex -interaction=nonstopmode iteris-family-operator-guide.tex >/dev/null
echo "Built: $ROOT/docs/iteris-family-operator-guide.pdf"
