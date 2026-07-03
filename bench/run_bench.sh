#!/usr/bin/env bash
# 성능 측정 실행기.
# - 프로세스 외부 반복(BENCH_RUNS): ASLR/페이지 캐시 등 프로세스 간 분산을 샘플링
# - allocator 교차 실행(A,B,A,B,...): 시간에 따른 시스템 상태 변화가 한쪽에 쏠리지 않게
# - taskset으로 CPU 고정: 코어 마이그레이션 노이즈 제거 (불가능하면 경고 후 진행)
# - CSV는 allocator별 파일에 append: plot_results.py가 다중 행을 집계
# 추가 인자는 driver에 그대로 전달된다 (예: --include-headers, 명시적 파일 목록).
set -u

DRIVER=${DRIVER:-bench/bench_driver}
RESULTS_DIR=${RESULTS_DIR:-bench/results}
RUNS=${BENCH_RUNS:-5}
ITERS=${BENCH_ITERS:-10}
WARMUP=${BENCH_WARMUP:-2}
CPU=${BENCH_CPU:-0}

if [ ! -x "$DRIVER" ]; then
    echo "run_bench: driver not found: $DRIVER (run 'make bench-driver')" >&2
    exit 1
fi
mkdir -p "$RESULTS_DIR"

runner=()
if command -v taskset >/dev/null 2>&1 && taskset -c "$CPU" true 2>/dev/null; then
    runner=(taskset -c "$CPU")
    echo "run_bench: pinned to CPU $CPU"
else
    echo "run_bench: WARNING: taskset unavailable, running unpinned (more noise)" >&2
fi

# 스키마(헤더)가 다른 기존 CSV에 append하면 컬럼이 어긋나므로 rotate한다.
append_csv() {
    local alloc=$1; shift
    local out_file="$RESULTS_DIR/$alloc.csv"
    local out header row
    out=$("${runner[@]}" "$DRIVER" --allocator="$alloc" --iters="$ITERS" --warmup="$WARMUP" --csv "$@") || return 1
    header=$(printf '%s\n' "$out" | head -n 1)
    row=$(printf '%s\n' "$out" | tail -n 1)
    if [ -f "$out_file" ] && [ "$(head -n 1 "$out_file")" != "$header" ]; then
        local old="$out_file.old-$(date +%s)"
        echo "run_bench: schema changed, rotating $out_file -> $old" >&2
        mv "$out_file" "$old"
    fi
    [ -f "$out_file" ] || printf '%s\n' "$header" > "$out_file"
    printf '%s\n' "$row" >> "$out_file"
}

for run in $(seq "$RUNS"); do
    for alloc in default mmap-arena; do
        echo "run_bench: run $run/$RUNS allocator=$alloc"
        append_csv "$alloc" "$@" || { echo "run_bench: FAILED (run $run, $alloc)" >&2; exit 1; }
    done
done

echo "run_bench: wrote $RUNS runs x {default,mmap-arena} to $RESULTS_DIR/"
