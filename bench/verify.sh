#!/usr/bin/env bash
# 정확성 검증
# 1) CHECK 빌드(assert 활성)로 mmap-arena 실행 → parse 반복마다 check_invariant()가 돈다.
# 2) 두 allocator의 files/bytes/nodes/errors가 완전히 일치하는지 diff.
# 하나라도 실패하면 비정상 종료 → make bench가 여기서 멈춘다.
set -u

DRIVER_CHECK=${1:-bench/bench_driver_check}
ROOT=${BENCH_ROOT:-bench/redis-src}

if [ ! -x "$DRIVER_CHECK" ]; then
    echo "verify: check build not found: $DRIVER_CHECK" >&2
    exit 1
fi

# files,bytes,nodes,errors 는 CSV의 4~7번째 컬럼
result_fields() {
    local alloc=$1
    "$DRIVER_CHECK" --allocator="$alloc" --root="$ROOT" --iters=1 --warmup=0 --csv \
        | tail -n 1 | cut -d, -f4-7
}

echo "verify: running invariant-checked parse (mmap-arena)..."
mmap_fields=$(result_fields mmap-arena) || { echo "verify: mmap-arena run FAILED (invariant violation or crash)" >&2; exit 1; }

echo "verify: running reference parse (default)..."
default_fields=$(result_fields default) || { echo "verify: default run FAILED" >&2; exit 1; }

echo "verify: default    files,bytes,nodes,errors = $default_fields"
echo "verify: mmap-arena files,bytes,nodes,errors = $mmap_fields"

if [ "$default_fields" != "$mmap_fields" ]; then
    echo "verify: MISMATCH — parse results differ between allocators; performance numbers are not trustworthy" >&2
    exit 1
fi

echo "verify: OK (results identical, invariants held)"
