#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOBS="${JOBS:-4}"
THREADS="${THREADS:-2}"
SMALL_R="${SMALL_R:-1024}"
SMALL_S="${SMALL_S:-4096}"
STAR_SF="${STAR_SF:-1}"

fail() {
  echo "[FAIL] $*" >&2
  exit 1
}

run_capture() {
  echo "[CHECK] $*" >&2
  local output
  if ! output="$("$@" 2>&1)"; then
    printf '%s\n' "$output" >&2
    fail "command failed: $*"
  fi
  printf '%s' "$output"
}

require_contains() {
  local output="$1"
  local needle="$2"
  local label="$3"

  if [[ "$output" != *"$needle"* ]]; then
    printf '%s\n' "$output" >&2
    fail "missing ${label}: ${needle}"
  fi
}

check_binary_join() {
  local algo="$1"
  local stat_marker="$2"
  shift 2

  local output
  output="$(run_capture "$ROOT_DIR/src/mchashjoins" -a "$algo" -n "$THREADS" \
    -r "$SMALL_R" -s "$SMALL_S" "$@")"
  require_contains "$output" "Results = ${SMALL_S}" "$algo result count"
  if [[ -n "$stat_marker" ]]; then
    require_contains "$output" "$stat_marker" "$algo stats"
  fi
}

check_starjoin() {
  local mode="$1"
  shift

  local expected_matches=$((STAR_SF * 6000000))
  local expected_aggregate=$((expected_matches * 3))
  local output
  output="$(run_capture "$ROOT_DIR/src/mchashjoins" --starjoin="$mode" \
    --sf="$STAR_SF" -n "$THREADS" "$@")"
  require_contains "$output" \
    "STARJOIN_STATS_CSV,${mode},${STAR_SF},${THREADS},${expected_matches},${expected_aggregate}" \
    "starjoin ${mode} stats"
}

cd "$ROOT_DIR"

if [[ ! -f Makefile ]]; then
  echo "[CHECK] configure"
  ./configure --disable-key8B CPPFLAGS="-DNUM_PASSES=2 -DNUM_RADIX_BITS=18"
fi

echo "[CHECK] build"
make -j"$JOBS"

check_binary_join "NPO" "NPO_STATS_CSV,"
check_binary_join "PRO" ""
check_binary_join "sort-merge" "SORTMERGE_STATS_CSV,"
check_binary_join "VJ" "VJ_STATS_CSV," --payload-width=1
check_binary_join "VJ" "VJ_STATS_CSV," --payload-width=1 --vj-hugepage
check_binary_join "VJ" "VJ_STATS_CSV," --payload-width=2
check_binary_join "VJ" "VJ_STATS_CSV," --payload-width=4
check_binary_join "PRVJ" "PRVJ_STATS_CSV," --payload-width=1

check_starjoin "npo"
check_starjoin "pro"
check_starjoin "vj" --payload-width=1

dry_run_output="$(run_capture "$ROOT_DIR/scripts/run_extended_algo_comparison.sh" \
  --dry-run --r-exps "5" --algorithms "NPO PRO sort-merge VJ_pw1 PRVJ_best" \
  --threads "$THREADS" --s-size "$SMALL_S")"
require_contains "$dry_run_output" "sort-merge: built-in mchashjoins baseline" \
  "extended comparison dry-run plan"

echo "[PASS] assignment self-check passed"
