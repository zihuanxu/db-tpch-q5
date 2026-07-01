#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

S_SIZE="${S_SIZE:-$((1 << 30))}"
R_EXP_MIN="${R_EXP_MIN:-5}"
R_EXP_MAX="${R_EXP_MAX:-30}"
R_EXPS="${R_EXPS:-}"
THREADS="${THREADS:-64}"
REPEATS="${REPEATS:-3}"
ALGORITHMS="${ALGORITHMS:-NPO PRO sort-merge VJ_pw1 VJ_pw2 VJ_pw4 PRVJ_best}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-3600}"
BASE_RADIX_BITS="${BASE_RADIX_BITS:-18}"
NUM_PASSES="${NUM_PASSES:-2}"
PRVJ_RADIX_BITS="${PRVJ_RADIX_BITS:-18}"
PRVJ_PAYLOAD_WIDTH="${PRVJ_PAYLOAD_WIDTH:-4}"
PRVJ_TUNING_CSV="${PRVJ_TUNING_CSV:-}"
SORT_MERGE_CMD="${SORT_MERGE_CMD:-}"
BASIC_NUMA="${BASIC_NUMA:-1}"
KEY8B="${KEY8B:-0}"
RUN=0
STOP_VARIANT_ON_FAILURE=0

OUT_PREFIX="${OUT_PREFIX:-extended-algo-comparison-$(date -Iseconds)}"
SUMMARY_CSV="${SUMMARY_CSV:-${OUT_PREFIX}.summary.csv}"
RAW_CSV="${RAW_CSV:-${OUT_PREFIX}.raw.csv}"
LOG_DIR="${LOG_DIR:-${OUT_PREFIX}.logs}"
BUILD_LOG="${BUILD_LOG:-${OUT_PREFIX}.build.log}"

usage() {
  cat <<EOF
Usage: $0 [--run] [options]

Default mode is dry-run. Pass --run to build and execute the full sweep.

Experiment:
  Compare NPO, PRO, sort-merge, VJ(payload-width=1/2/4), and PRVJ(best config)
  at fixed |S|=2^30 while sweeping |R| from 2^5 to 2^30. Each point is run
  3 times by default and summarized with averages over successful repeats.

Options:
  --run                         Execute the experiment.
  --dry-run                     Print the plan only. This is the default.
  --threads N                   Thread count [${THREADS}].
  --repeats N                   Repeats per point [${REPEATS}].
  --s-size N                    Probe relation cardinality [${S_SIZE}].
  --r-exp-min N                 Minimum R exponent [${R_EXP_MIN}].
  --r-exp-max N                 Maximum R exponent [${R_EXP_MAX}].
  --r-exps "20 24 28"           Explicit R exponents. Overrides min/max sweep.
  --algorithms "NPO PRO ..."     Variants to run [${ALGORITHMS}].
                                Names: NPO PRO sort-merge VJ_pw1 VJ_pw2
                                VJ_pw4 PRVJ_best.
  --stop-variant-on-failure      Skip later R points for a variant after failure.
  --timeout-seconds N            Timeout per run, 0 disables timeout [${TIMEOUT_SECONDS}].
  --base-radix-bits N           Radix bits for PRO/base build [${BASE_RADIX_BITS}].
  --num-passes N                NUM_PASSES for radix builds [${NUM_PASSES}].
  --prvj-radix-bits N           PRVJ best-config radix bits [${PRVJ_RADIX_BITS}].
  --prvj-payload-width N        PRVJ best-config payload width [${PRVJ_PAYLOAD_WIDTH}].
  --prvj-tuning-csv PATH        Pick PRVJ best config from tuning CSV.
  --sort-merge-cmd CMD          External sort-merge command template.
                                If omitted, the built-in mchashjoins
                                sort-merge baseline is used.
  --out-prefix PREFIX           Prefix for summary/raw CSV and logs [${OUT_PREFIX}].
  --summary-csv PATH            Summary CSV path [${SUMMARY_CSV}].
  --raw-csv PATH                Raw per-repeat CSV path [${RAW_CSV}].
  --log-dir DIR                 Per-run log directory [${LOG_DIR}].
  --no-basic-numa               Do not pass --basic-numa.
  --enable-key8b                Build 16-byte tuples instead of default 8-byte tuples.
  -h, --help                    Show this help.

sort-merge command template:
  The template is evaluated by bash and may use these variables:
    R_SIZE S_SIZE THREADS REPEAT STDOUT_LOG STDERR_LOG
  It must write timing output parseable by this script, or at least finish
  successfully so the raw row records status OK with empty metrics.

CSV fields include:
  algo,payload_width,radix_bits,threads,throughput_mtps,time_ms,
  vector_total_bytes,extra_space_ratio
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
    --threads)
      THREADS="$2"
      shift 2
      ;;
    --repeats)
      REPEATS="$2"
      shift 2
      ;;
    --s-size)
      S_SIZE="$2"
      shift 2
      ;;
    --r-exp-min)
      R_EXP_MIN="$2"
      shift 2
      ;;
    --r-exp-max)
      R_EXP_MAX="$2"
      shift 2
      ;;
    --r-exps)
      R_EXPS="$2"
      shift 2
      ;;
    --algorithms)
      ALGORITHMS="$2"
      shift 2
      ;;
    --stop-variant-on-failure)
      STOP_VARIANT_ON_FAILURE=1
      shift
      ;;
    --timeout-seconds)
      TIMEOUT_SECONDS="$2"
      shift 2
      ;;
    --base-radix-bits)
      BASE_RADIX_BITS="$2"
      shift 2
      ;;
    --num-passes)
      NUM_PASSES="$2"
      shift 2
      ;;
    --prvj-radix-bits)
      PRVJ_RADIX_BITS="$2"
      shift 2
      ;;
    --prvj-payload-width)
      PRVJ_PAYLOAD_WIDTH="$2"
      shift 2
      ;;
    --prvj-tuning-csv)
      PRVJ_TUNING_CSV="$2"
      shift 2
      ;;
    --sort-merge-cmd)
      SORT_MERGE_CMD="$2"
      shift 2
      ;;
    --out-prefix)
      OUT_PREFIX="$2"
      SUMMARY_CSV="${OUT_PREFIX}.summary.csv"
      RAW_CSV="${OUT_PREFIX}.raw.csv"
      LOG_DIR="${OUT_PREFIX}.logs"
      BUILD_LOG="${OUT_PREFIX}.build.log"
      shift 2
      ;;
    --summary-csv)
      SUMMARY_CSV="$2"
      shift 2
      ;;
    --raw-csv)
      RAW_CSV="$2"
      shift 2
      ;;
    --log-dir)
      LOG_DIR="$2"
      shift 2
      ;;
    --no-basic-numa)
      BASIC_NUMA=0
      shift
      ;;
    --enable-key8b)
      KEY8B=1
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

tuple_bytes() {
  if [[ "$KEY8B" -eq 1 ]]; then
    echo 16
  else
    echo 8
  fi
}

payload_max_for_width() {
  case "$1" in
    "") echo "" ;;
    1) echo 255 ;;
    2) echo 65535 ;;
    4) echo 4294967295 ;;
    *)
      echo "[ERROR] payload width must be one of 1, 2, or 4: $1" >&2
      exit 2
      ;;
  esac
}

selected_r_exps() {
  if [[ -n "$R_EXPS" ]]; then
    printf '%s\n' $R_EXPS
  else
    seq "$R_EXP_MIN" "$R_EXP_MAX"
  fi
}

has_algorithm() {
  local needle="$1"
  local item
  for item in $ALGORITHMS; do
    if [[ "$item" == "$needle" ]]; then
      return 0
    fi
  done
  return 1
}

csv_escape() {
  local s="${1:-}"
  s="${s//\"/\"\"}"
  printf '"%s"' "$s"
}

pick_prvj_best_from_tuning_csv() {
  local csv="$1"
  if [[ ! -r "$csv" ]]; then
    echo "[ERROR] PRVJ tuning CSV is not readable: $csv" >&2
    exit 2
  fi

  local best
  best="$(awk -F',' '
    NR == 1 {
      for (i = 1; i <= NF; i++) {
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", $i)
        col[$i] = i
      }
      next
    }
    {
      status = (("status" in col) ? $(col["status"]) : "OK")
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", status)
      if (status != "OK") next
      t = $(col["throughput_mtps"]) + 0
      if (t > best_t) {
        best_t = t
        best_pw = $(col["payload_width"])
        best_rb = $(col["radix_bits"])
      }
    }
    END {
      if (best_t > 0) {
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", best_pw)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", best_rb)
        print best_pw "," best_rb
      }
    }
  ' "$csv")"

  if [[ -z "$best" ]]; then
    echo "[ERROR] Could not pick PRVJ best config from: $csv" >&2
    exit 2
  fi

  PRVJ_PAYLOAD_WIDTH="${best%,*}"
  PRVJ_RADIX_BITS="${best#*,}"
}

print_plan() {
  echo "Extended algorithm comparison plan"
  echo "  repo: ${ROOT_DIR}"
  echo "  mode: $([[ "$RUN" -eq 1 ]] && echo run || echo dry-run)"
  echo "  S_SIZE: ${S_SIZE}"
  if [[ -n "$R_EXPS" ]]; then
    echo "  R exponents: ${R_EXPS}"
  else
    echo "  R sweep: 2^${R_EXP_MIN} .. 2^${R_EXP_MAX}"
  fi
  echo "  THREADS: ${THREADS}"
  echo "  REPEATS: ${REPEATS}"
  echo "  ALGORITHMS: ${ALGORITHMS}"
  echo "  TIMEOUT_SECONDS: ${TIMEOUT_SECONDS}"
  echo "  BASE_RADIX_BITS: ${BASE_RADIX_BITS}"
  echo "  NUM_PASSES: ${NUM_PASSES}"
  echo "  PRVJ best config: payload_width=${PRVJ_PAYLOAD_WIDTH}, radix_bits=${PRVJ_RADIX_BITS}"
  echo "  BASIC_NUMA: ${BASIC_NUMA}"
  echo "  KEY8B: ${KEY8B}"
  echo "  SUMMARY_CSV: ${SUMMARY_CSV}"
  echo "  RAW_CSV: ${RAW_CSV}"
  echo "  LOG_DIR: ${LOG_DIR}"
  if [[ -n "$SORT_MERGE_CMD" ]]; then
    echo "  sort-merge: external command configured"
  else
    echo "  sort-merge: built-in mchashjoins baseline"
  fi
  echo "  stop variant on failure: ${STOP_VARIANT_ON_FAILURE}"
  echo
  echo "Algorithms:"
  if has_algorithm NPO; then echo "  NPO"; fi
  if has_algorithm PRO; then echo "  PRO"; fi
  if has_algorithm sort-merge; then echo "  sort-merge"; fi
  if has_algorithm VJ_pw1; then echo "  VJ payload-width=1"; fi
  if has_algorithm VJ_pw2; then echo "  VJ payload-width=2"; fi
  if has_algorithm VJ_pw4; then echo "  VJ payload-width=4"; fi
  if has_algorithm PRVJ_best; then echo "  PRVJ payload-width=${PRVJ_PAYLOAD_WIDTH}, radix_bits=${PRVJ_RADIX_BITS}"; fi
}

configure_for_radix_bits() {
  local radix_bits="$1"
  local configure_key_arg="--disable-key8B"

  if [[ "$KEY8B" -eq 1 ]]; then
    configure_key_arg="--enable-key8B"
  fi

  {
    echo "=== $(date -Iseconds) build radix_bits=${radix_bits} num_passes=${NUM_PASSES} ==="
    if [[ -f Makefile ]]; then
      make distclean
    fi
    ./configure "${configure_key_arg}" \
      CPPFLAGS="-DNUM_PASSES=${NUM_PASSES} -DNUM_RADIX_BITS=${radix_bits}"
    make
  } >>"${BUILD_LOG}" 2>&1
}

extract_metrics() {
  local stdout_log="$1"
  local algo="$2"
  local time_usec="" time_ms="" throughput_mtps="" vector_total_bytes=""

  if [[ "$algo" == "PRO" || "$algo" == "PRVJ" ]]; then
    local timing_line
    timing_line="$(awk -F',' '/^[[:space:]]*[0-9]+[[:space:]]*,[[:space:]]*[0-9]+[[:space:]]*,/ { line=$0 } END { print line }' "$stdout_log")"
    if [[ -n "$timing_line" ]]; then
      time_usec="$(awk -F',' '{ gsub(/[[:space:]]/, "", $6); print $6 }' <<<"$timing_line")"
    fi
  elif [[ "$algo" == "NPO" || "$algo" == "VJ" || "$algo" == "sort-merge" ]]; then
    time_usec="$(awk '
      /TOTAL-TIME-USECS/ { getline; print $1; exit }
    ' "$stdout_log")"
  fi

  if [[ "$algo" == "NPO" ]]; then
    vector_total_bytes="$(awk -F',' '/^NPO_STATS_CSV,/ { v=$3 } END { gsub(/[[:space:]]/, "", v); print v }' "$stdout_log")"
  elif [[ "$algo" == "VJ" ]]; then
    vector_total_bytes="$(awk -F',' '/^VJ_STATS_CSV,/ { v=$4 } END { gsub(/[[:space:]]/, "", v); print v }' "$stdout_log")"
  elif [[ "$algo" == "PRVJ" ]]; then
    vector_total_bytes="$(awk -F',' '/^PRVJ_STATS_CSV,/ { v=$3 } END { gsub(/[[:space:]]/, "", v); print v }' "$stdout_log")"
  elif [[ "$algo" == "sort-merge" ]]; then
    vector_total_bytes="$(awk -F',' '/^SORTMERGE_STATS_CSV,/ { v=$3 } END { gsub(/[[:space:]]/, "", v); print v }' "$stdout_log")"
  fi

  if [[ -n "$time_usec" ]]; then
    time_ms="$(awk -v usec="$time_usec" 'BEGIN { if (usec > 0) printf "%.3f", usec / 1000.0 }')"
    throughput_mtps="$(awk -v s="$S_SIZE" -v usec="$time_usec" 'BEGIN { if (usec > 0) printf "%.6f", s / usec }')"
  fi

  printf '%s,%s,%s,%s\n' "$throughput_mtps" "$time_ms" "$vector_total_bytes" "$time_usec"
}

append_raw_row() {
  local algo="$1" variant="$2" r_exp="$3" r_size="$4" payload_width="$5"
  local radix_bits="$6" repeat="$7" status="$8" throughput="$9" time_ms="${10}"
  local vector_total="${11}" extra_ratio="${12}" stdout_log="${13}" stderr_log="${14}" error="${15}"

  {
    printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,' \
      "$algo" "$variant" "$r_exp" "$r_size" "$S_SIZE" \
      "$payload_width" "$radix_bits" "$THREADS" "$repeat" "$status" \
      "$throughput" "$time_ms" "$vector_total"
    printf '%s,' "$extra_ratio"
    csv_escape "$stdout_log"
    printf ','
    csv_escape "$stderr_log"
    printf ','
    csv_escape "$error"
    printf '\n'
  } >>"$RAW_CSV"
}

append_summary_for_point() {
  local algo="$1" variant="$2" r_exp="$3" r_size="$4" payload_width="$5" radix_bits="$6"

  awk -F',' -v algo="$algo" -v variant="$variant" -v r_exp="$r_exp" \
    -v r_size="$r_size" -v s_size="$S_SIZE" -v payload_width="$payload_width" \
    -v radix_bits="$radix_bits" -v threads="$THREADS" -v repeats="$REPEATS" \
    -v raw_csv="$RAW_CSV" '
    NR == 1 { next }
    $1 == algo && $2 == variant && $3 == r_exp {
      total_runs++
      if ($10 == "OK") {
        ok_runs++
        if ($11 != "") { throughput_sum += $11; throughput_n++ }
        if ($12 != "") { time_sum += $12; time_n++ }
        if ($13 != "") { vector_sum += $13; vector_n++ }
        if ($14 != "") { ratio_sum += $14; ratio_n++ }
      } else {
        failures++
      }
    }
    END {
      if (ok_runs == repeats) status = "OK"
      else if (ok_runs > 0) status = "PARTIAL"
      else if (total_runs > 0) status = "FAIL"
      else status = "MISSING"

      throughput = (throughput_n > 0) ? throughput_sum / throughput_n : ""
      time_ms = (time_n > 0) ? time_sum / time_n : ""
      vector_total = (vector_n > 0) ? vector_sum / vector_n : ""
      ratio = (ratio_n > 0) ? ratio_sum / ratio_n : ""

      printf "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,", algo, variant, r_exp, r_size, s_size, payload_width, radix_bits, threads, repeats, ok_runs, status
      if (throughput != "") printf "%.6f", throughput
      printf ","
      if (time_ms != "") printf "%.3f", time_ms
      printf ","
      if (vector_total != "") printf "%.0f", vector_total
      printf ","
      if (ratio != "") printf "%.8f", ratio
      printf ",%s\n", raw_csv
    }
  ' "$RAW_CSV" >>"$SUMMARY_CSV"
}

declare -A DISABLED_VARIANTS=()

variant_is_disabled() {
  local variant="$1"
  [[ "${DISABLED_VARIANTS[$variant]:-0}" == "1" ]]
}

disable_variant() {
  local variant="$1"
  if [[ "$STOP_VARIANT_ON_FAILURE" -eq 1 ]]; then
    DISABLED_VARIANTS["$variant"]=1
  fi
}

append_skipped_point() {
  local algo="$1" variant="$2" r_exp="$3" r_size="$4" payload_width="$5"
  local radix_bits="$6" reason="$7" repeat

  for repeat in $(seq 1 "$REPEATS"); do
    append_raw_row "$algo" "$variant" "$r_exp" "$r_size" "$payload_width" \
      "$radix_bits" "$repeat" "SKIPPED" "" "" "" "" "" "" "$reason"
  done
  append_summary_for_point "$algo" "$variant" "$r_exp" "$r_size" "$payload_width" "$radix_bits"
}

append_preflight_fail_point() {
  local algo="$1" variant="$2" r_exp="$3" r_size="$4" payload_width="$5"
  local radix_bits="$6" reason="$7" repeat

  for repeat in $(seq 1 "$REPEATS"); do
    append_raw_row "$algo" "$variant" "$r_exp" "$r_size" "$payload_width" \
      "$radix_bits" "$repeat" "FAIL" "" "" "" "" "" "" "$reason"
  done
  append_summary_for_point "$algo" "$variant" "$r_exp" "$r_size" "$payload_width" "$radix_bits"
  disable_variant "$variant"
}

generated_payload_overflow_reason() {
  local r_size="$1" payload_width="$2"
  local max_payload
  local max_generated_payload=$((r_size - 1))

  if [[ -z "$payload_width" ]]; then
    return 1
  fi

  max_payload="$(payload_max_for_width "$payload_width")"
  if (( max_generated_payload > max_payload )); then
    printf 'Generated R payload max %s exceeds payload-width=%s max %s' \
      "$max_generated_payload" "$payload_width" "$max_payload"
    return 0
  fi

  return 1
}

run_binary_point() {
  local algo="$1" variant="$2" r_exp="$3" r_size="$4" payload_width="$5" radix_bits="$6"
  local repeat stdout_log stderr_log status metrics throughput time_ms vector_total extra_ratio error
  local numa_args=()
  local payload_args=()
  local tuple_size raw_bytes

  if variant_is_disabled "$variant"; then
    append_skipped_point "$algo" "$variant" "$r_exp" "$r_size" "$payload_width" \
      "$radix_bits" "Variant disabled after earlier failure"
    return
  fi

  if [[ "$BASIC_NUMA" -eq 1 ]]; then
    numa_args+=(--basic-numa)
  fi
  if [[ -n "$payload_width" ]]; then
    payload_args+=(--payload-width="$payload_width")
  fi

  tuple_size="$(tuple_bytes)"
  raw_bytes=$(( (r_size + S_SIZE) * tuple_size ))

  for repeat in $(seq 1 "$REPEATS"); do
    stdout_log="$LOG_DIR/${variant}_R2e${r_exp}_rep${repeat}.stdout"
    stderr_log="$LOG_DIR/${variant}_R2e${r_exp}_rep${repeat}.stderr"
    status="OK"
    error=""

    local cmd=("$ROOT_DIR/src/mchashjoins" -a "$algo" -n "$THREADS" \
        -r "$r_size" -s "$S_SIZE" "${numa_args[@]}" "${payload_args[@]}" \
        )

    if [[ "$TIMEOUT_SECONDS" -gt 0 ]]; then
      cmd=(timeout --preserve-status "$TIMEOUT_SECONDS" "${cmd[@]}")
    fi

    if ! "${cmd[@]}" >"$stdout_log" 2>"$stderr_log"; then
      status="FAIL"
      error="$(tail -n 3 "$stderr_log" | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
      if [[ -z "$error" ]]; then
        error="Command failed or timed out after ${TIMEOUT_SECONDS}s"
      fi
    fi

    metrics="$(extract_metrics "$stdout_log" "$algo")"
    IFS=',' read -r throughput time_ms vector_total _time_usec <<<"$metrics"
    extra_ratio=""
    if [[ -n "$vector_total" ]]; then
      extra_ratio="$(awk -v extra="$vector_total" -v raw="$raw_bytes" 'BEGIN { if (raw > 0) printf "%.8f", extra / raw }')"
    fi

    append_raw_row "$algo" "$variant" "$r_exp" "$r_size" "$payload_width" \
      "$radix_bits" "$repeat" "$status" "$throughput" "$time_ms" \
      "$vector_total" "$extra_ratio" "$stdout_log" "$stderr_log" "$error"

    if [[ "$status" != "OK" ]]; then
      disable_variant "$variant"
    fi
  done

  append_summary_for_point "$algo" "$variant" "$r_exp" "$r_size" "$payload_width" "$radix_bits"
}

run_sort_merge_point() {
  local r_exp="$1" r_size="$2" repeat stdout_log stderr_log status error
  local throughput="" time_ms="" vector_total="" extra_ratio="" radix_bits="" payload_width=""

  if variant_is_disabled "sort-merge"; then
    append_skipped_point "sort-merge" "sort-merge" "$r_exp" "$r_size" \
      "$payload_width" "$radix_bits" "Variant disabled after earlier failure"
    return
  fi

  for repeat in $(seq 1 "$REPEATS"); do
    stdout_log="$LOG_DIR/sort_merge_R2e${r_exp}_rep${repeat}.stdout"
    stderr_log="$LOG_DIR/sort_merge_R2e${r_exp}_rep${repeat}.stderr"
    status="OK"
    error=""

    if [[ -z "$SORT_MERGE_CMD" ]]; then
      status="UNSUPPORTED"
      error="No sort-merge algorithm is registered in mchashjoins; pass --sort-merge-cmd to use an external implementation."
      : >"$stdout_log"
      printf '%s\n' "$error" >"$stderr_log"
    else
      R_SIZE="$r_size" S_SIZE="$S_SIZE" THREADS="$THREADS" REPEAT="$repeat" \
      STDOUT_LOG="$stdout_log" STDERR_LOG="$stderr_log" \
      bash -lc "$SORT_MERGE_CMD" >"$stdout_log" 2>"$stderr_log" || {
        status="FAIL"
        error="$(tail -n 3 "$stderr_log" | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
      }
      # External command parsing is intentionally conservative.
      time_ms="$(awk -F',' '/time_ms/ { print $2; exit }' "$stdout_log" || true)"
      throughput="$(awk -F',' '/throughput_mtps/ { print $2; exit }' "$stdout_log" || true)"
    fi

    append_raw_row "sort-merge" "sort-merge" "$r_exp" "$r_size" "$payload_width" \
      "$radix_bits" "$repeat" "$status" "$throughput" "$time_ms" \
      "$vector_total" "$extra_ratio" "$stdout_log" "$stderr_log" "$error"

    if [[ "$status" != "OK" ]]; then
      disable_variant "sort-merge"
    fi
  done

  append_summary_for_point "sort-merge" "sort-merge" "$r_exp" "$r_size" "$payload_width" "$radix_bits"
}

run_sweep_for_base_build() {
  local r_exp r_size
  for r_exp in $(selected_r_exps); do
    r_size=$((1 << r_exp))
    echo "[INFO] base build sweep R=2^${r_exp}" >&2
    if has_algorithm NPO; then run_binary_point "NPO" "NPO" "$r_exp" "$r_size" "" ""; fi
    if has_algorithm PRO; then run_binary_point "PRO" "PRO" "$r_exp" "$r_size" "" "$BASE_RADIX_BITS"; fi
    if has_algorithm sort-merge; then
      if [[ -n "$SORT_MERGE_CMD" ]]; then
        run_sort_merge_point "$r_exp" "$r_size"
      else
        run_binary_point "sort-merge" "sort-merge" "$r_exp" "$r_size" "" ""
      fi
    fi
    if has_algorithm VJ_pw1; then run_binary_point "VJ" "VJ_pw1" "$r_exp" "$r_size" "1" ""; fi
    if has_algorithm VJ_pw2; then run_binary_point "VJ" "VJ_pw2" "$r_exp" "$r_size" "2" ""; fi
    if has_algorithm VJ_pw4; then run_binary_point "VJ" "VJ_pw4" "$r_exp" "$r_size" "4" ""; fi
  done
}

run_sweep_for_prvj_build() {
  local r_exp r_size
  for r_exp in $(selected_r_exps); do
    r_size=$((1 << r_exp))
    echo "[INFO] PRVJ best sweep R=2^${r_exp}" >&2
    if has_algorithm PRVJ_best; then
      run_binary_point "PRVJ" "PRVJ_best" "$r_exp" "$r_size" "$PRVJ_PAYLOAD_WIDTH" "$PRVJ_RADIX_BITS"
    fi
  done
}

if [[ -n "$PRVJ_TUNING_CSV" ]]; then
  pick_prvj_best_from_tuning_csv "$PRVJ_TUNING_CSV"
fi

cd "$ROOT_DIR"
print_plan

if [[ "$RUN" -ne 1 ]]; then
  cat <<EOF

Dry-run only. No build or experiment was started.

To execute after choosing/confirming PRVJ best config:
  $0 --run --prvj-radix-bits ${PRVJ_RADIX_BITS} --prvj-payload-width ${PRVJ_PAYLOAD_WIDTH}

To pick PRVJ best config from tuning output:
  $0 --run --prvj-tuning-csv path/to/prvj-tuning.csv
EOF
  exit 0
fi

mkdir -p "$LOG_DIR"
echo "algo,variant,r_exp,r_size,s_size,payload_width,radix_bits,threads,repeat,status,throughput_mtps,time_ms,vector_total_bytes,extra_space_ratio,stdout_log,stderr_log,error" >"$RAW_CSV"
echo "algo,variant,r_exp,r_size,s_size,payload_width,radix_bits,threads,repeats,ok_runs,status,throughput_mtps,time_ms,vector_total_bytes,extra_space_ratio,raw_csv" >"$SUMMARY_CSV"

echo "[INFO] Building base algorithms with NUM_RADIX_BITS=${BASE_RADIX_BITS}" >&2
configure_for_radix_bits "$BASE_RADIX_BITS"
run_sweep_for_base_build

if [[ "$PRVJ_RADIX_BITS" != "$BASE_RADIX_BITS" ]]; then
  echo "[INFO] Building PRVJ best with NUM_RADIX_BITS=${PRVJ_RADIX_BITS}" >&2
  configure_for_radix_bits "$PRVJ_RADIX_BITS"
else
  echo "[INFO] Reusing base build for PRVJ best" >&2
fi
run_sweep_for_prvj_build

echo "[INFO] Summary CSV: $SUMMARY_CSV" >&2
echo "[INFO] Raw CSV: $RAW_CSV" >&2
echo "[INFO] Build log: $BUILD_LOG" >&2
echo "[INFO] Logs: $LOG_DIR" >&2
