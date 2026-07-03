#!/usr/bin/env bash
# 입력 크기 스윕: .c 30/60/90개 → .c 전체 → .c+.h 전체.
# first-fit free list는 live 블록 수에 따라 비선형으로 느려질 수 있으므로
# 단일 크기가 아니라 스케일링 곡선으로 비교한다.
# 결과는 bench/results/sweep/ 에 append — 행마다 bytes가 다르므로
# plot_results.py가 (allocator, bytes) 단위로 묶어 라인 차트를 그린다.
set -u

ROOT=${BENCH_ROOT:-bench/redis-src}
SWEEP_DIR=${SWEEP_DIR:-bench/results/sweep}
export RESULTS_DIR="$SWEEP_DIR"
export BENCH_RUNS=${BENCH_RUNS:-3}

here=$(dirname "$0")

echo "sweep: workload=30_files (first 30 .c files)"
files_30=$(find "$ROOT" -name '*.c' | sort | head -n 30)
# shellcheck disable=SC2086
bash "$here/run_bench.sh" $files_30 || exit 1

echo "sweep: workload=60_files (first 60 .c files)"
files_60=$(find "$ROOT" -name '*.c' | sort | head -n 60)
# shellcheck disable=SC2086
bash "$here/run_bench.sh" $files_60 || exit 1

echo "sweep: workload=90_files (first 90 .c files)"
files_90=$(find "$ROOT" -name '*.c' | sort | head -n 90)
# shellcheck disable=SC2086
bash "$here/run_bench.sh" $files_90 || exit 1

echo "sweep: workload=medium (all .c files)"
bash "$here/run_bench.sh" || exit 1

echo "sweep: workload=large (all .c + .h files)"
bash "$here/run_bench.sh" --include-headers || exit 1

echo "sweep: done, results in $SWEEP_DIR/"
