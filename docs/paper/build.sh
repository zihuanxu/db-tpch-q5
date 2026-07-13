#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export TEXINPUTS="$(pwd)/template:${TEXINPUTS:-}"
pdflatex -interaction=nonstopmode -halt-on-error paper.tex
pdflatex -interaction=nonstopmode -halt-on-error paper.tex
python3 ../../scripts/check_paper.py --repo-root ../.. --write-provenance
