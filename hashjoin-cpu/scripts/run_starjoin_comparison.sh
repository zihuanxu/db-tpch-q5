#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SF="${SF:-1}"
THREADS_LIST="${THREADS_LIST:-1 2 4 8 16}"
MODES="${MODES:-npo pro vj}"
PREFETCH_DISTANCES="${PREFETCH_DISTANCES:-0}"
PAYLOAD_WIDTH="${PAYLOAD_WIDTH:-1}"
OUT="${OUT:-starjoin-comparison.csv}"
LOG_DIR="${LOG_DIR:-starjoin-comparison.logs}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-600}"
BASIC_NUMA="${BASIC_NUMA:-0}"
RUN=0

usage() {
  cat <<EOF
Usage: $0 [--run] [options]

Default mode is dry-run. Pass --run to execute starjoin experiments.

Options:
  --run                         Execute the experiment.
  --dry-run                     Print the plan only.
  --sf N                        Starjoin scale factor [${SF}].
  --threads-list "1 2 4"        Thread counts [${THREADS_LIST}].
  --modes "npo pro vj"          Modes to run [${MODES}].
  --prefetch-distances "0 16"   Prefetch distances for npo/vj [${PREFETCH_DISTANCES}].
  --payload-width N             VJ payload width [${PAYLOAD_WIDTH}].
  --out PATH                    Output CSV [${OUT}].
  --log-dir DIR                 Log directory [${LOG_DIR}].
  --timeout-seconds N           Timeout per run, 0 disables timeout [${TIMEOUT_SECONDS}].
  --basic-numa                  Pass --basic-numa to mchashjoins.
  -h, --help                    Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run)
      RUN=1
      shift
      ;;
    --dry-run)
      RUN=0
      shift
      ;;
    --sf)
      SF="$2"
      shift 2
      ;;
    --threads-list)
      THREADS_LIST="$2"
      shift 2
      ;;
    --modes)
      MODES="$2"
      shift 2
      ;;
    --prefetch-distances)
      PREFETCH_DISTANCES="$2"
      shift 2
      ;;
    --payload-width)
      PAYLOAD_WIDTH="$2"
      shift 2
      ;;
    --out)
      OUT="$2"
      shift 2
      ;;
    --log-dir)
      LOG_DIR="$2"
      shift 2
      ;;
    --timeout-seconds)
      TIMEOUT_SECONDS="$2"
      shift 2
      ;;
    --basic-numa)
      BASIC_NUMA=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

print_plan() {
  echo "Starjoin comparison plan"
  echo "  repo: ${ROOT_DIR}"
  echo "  mode: $([[ "$RUN" -eq 1 ]] && echo run || echo dry-run)"
  echo "  SF: ${SF}"
  echo "  THREADS_LIST: ${THREADS_LIST}"
  echo "  MODES: ${MODES}"
  echo "  PREFETCH_DISTANCES: ${PREFETCH_DISTANCES}"
  echo "  PAYLOAD_WIDTH: ${PAYLOAD_WIDTH}"
  echo "  BASIC_NUMA: ${BASIC_NUMA}"
  echo "  OUT: ${OUT}"
  echo "  LOG_DIR: ${LOG_DIR}"
}

extract_time_ms() {
  awk '
    /TOTAL-TIME-USECS/ {
      getline
      if ($1 != "") {
        printf "%.3f", $1 / 1000.0
      }
      exit
    }
  ' "$1"
}

append_row() {
  local mode="$1" threads="$2" prefetch="$3" status="$4" stdout_log="$5" stderr_log="$6" error="$7"
  local time_ms matches aggregate input_bytes aux_bytes total_bytes

  time_ms="$(extract_time_ms "$stdout_log")"
  IFS=',' read -r _tag _mode _sf _threads matches aggregate input_bytes aux_bytes total_bytes < <(
    awk -F',' '/^STARJOIN_STATS_CSV,/ {print; exit}' "$stdout_log"
  )

  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"%s","%s","%s"\n' \
    "$mode" "$SF" "$threads" "$PAYLOAD_WIDTH" "$prefetch" "$BASIC_NUMA" \
    "$status" "${time_ms:-}" "${matches:-}" "${aggregate:-}" \
    "${input_bytes:-}" "${aux_bytes:-}" "${total_bytes:-}" \
    "$stdout_log" "$stderr_log" "$error" >>"$OUT"
}

run_one() {
  local mode="$1" threads="$2" prefetch="$3"
  local stdout_log="$LOG_DIR/${mode}_sf${SF}_t${threads}_pf${prefetch}.stdout"
  local stderr_log="$LOG_DIR/${mode}_sf${SF}_t${threads}_pf${prefetch}.stderr"
  local status="OK"
  local error=""
  local cmd=("$ROOT_DIR/src/mchashjoins" --starjoin="$mode" --sf="$SF" -n "$threads")

  if [[ "$mode" == "vj" ]]; then
    cmd+=(--payload-width="$PAYLOAD_WIDTH")
  fi
  if [[ "$mode" == "npo" || "$mode" == "vj" ]]; then
    cmd+=(--starjoin-prefetch-distance="$prefetch")
  fi
  if [[ "$BASIC_NUMA" -eq 1 ]]; then
    cmd+=(--basic-numa)
  fi
  if [[ "$TIMEOUT_SECONDS" -gt 0 ]]; then
    cmd=(timeout --preserve-status "$TIMEOUT_SECONDS" "${cmd[@]}")
  fi

  if ! "${cmd[@]}" >"$stdout_log" 2>"$stderr_log"; then
    status="FAIL"
    error="$(tail -n 3 "$stderr_log" | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
    if [[ -z "$error" ]]; then
      error="Command failed or timed out"
    fi
  fi

  append_row "$mode" "$threads" "$prefetch" "$status" "$stdout_log" "$stderr_log" "$error"
}

cd "$ROOT_DIR"
print_plan

if [[ "$RUN" -ne 1 ]]; then
  echo
  echo "Dry-run only. Pass --run to execute."
  exit 0
fi

mkdir -p "$LOG_DIR"
echo 'mode,sf,nthreads,payload_width,prefetch_distance,basic_numa,status,time_ms,matches,aggregate_sum,input_bytes,aux_bytes,total_bytes,stdout_log,stderr_log,error' >"$OUT"

for threads in $THREADS_LIST; do
  for mode in $MODES; do
    if [[ "$mode" == "pro" ]]; then
      run_one "$mode" "$threads" "0"
    else
      for prefetch in $PREFETCH_DISTANCES; do
        run_one "$mode" "$threads" "$prefetch"
      done
    fi
  done
done

echo "[INFO] Wrote ${OUT}" >&2
echo "[INFO] Logs: ${LOG_DIR}" >&2
