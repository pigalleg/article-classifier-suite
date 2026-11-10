#!/usr/bin/env bash
# compile_figures.sh — compile every standalone TikZ/pgfplots figure in
# data/results/figures/ to PDF with pdflatex, and report any failures.
#
# Usage:
#   bash scripts/compile_figures.sh               # compile in place
#   bash scripts/compile_figures.sh --clean       # also remove .aux/.log files
#
# Requires a TeX distribution with pdflatex, standalone, pgfplots
# (e.g. TeX Live or MacTeX). Honors SO_DATA_DIR like the Python scripts.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIG_DIR="${SO_DATA_DIR:-$ROOT/data}/results/figures"
CLEAN=0
[ "${1:-}" = "--clean" ] && CLEAN=1

if ! command -v pdflatex >/dev/null 2>&1; then
  echo "[error] pdflatex not found — install TeX Live / MacTeX." >&2
  exit 1
fi

cd "$FIG_DIR" || { echo "[error] $FIG_DIR not found" >&2; exit 1; }

ok=0; fail=0; failed=()
for tex in *.tex; do
  [ -e "$tex" ] || continue
  if pdflatex -interaction=nonstopmode -halt-on-error "$tex" >/dev/null 2>&1; then
    ok=$((ok + 1))
  else
    fail=$((fail + 1)); failed+=("$tex")
  fi
  if [ "$CLEAN" -eq 1 ]; then
    base="${tex%.tex}"
    rm -f -- "$base.aux" "$base.log" "$base.synctex.gz"
  fi
done

echo "Compiled: $ok   Failed: $fail   (in $FIG_DIR)"
for f in "${failed[@]+"${failed[@]}"}"; do
  echo "  FAILED: $f  (see ${f%.tex}.log)"
done
[ "$fail" -eq 0 ]
