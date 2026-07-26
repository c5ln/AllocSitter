#!/usr/bin/env python3
"""할당 trace 재생 → 이론적 peak RSS 하한과 정확한 요청 크기 분포.

입력: bench_driver --allocator=mmap-arena --trace=FILE 이 기록한 이벤트 로그.
  m <size> <ptr>            malloc
  c <size> <ptr>            calloc (size = nmemb*size)
  f <ptr>                   free
  r <old> <size> <new>      realloc

계산하는 것:
- peak live requested bytes: 어떤 allocator도 이보다 낮은 peak를 가질 수 없는
  이론 하한. realloc은 in-place 최적을 가정해 원자적 resize로 취급한다.
- peak live real (시뮬레이션): 현재 설계의 chunk = round16(size + 8) 기준으로
  같은 trace를 돌렸을 때의 peak. (peak_live_real - peak_live_req) = 헤더+올림
  오버헤드가 peak에 기여하는 양.
- 정확한 요청 크기 분포: log 버킷이 아니라 크기별 exact count. size class를
  어디에 놓아야 하는지(exact-fit 후보)를 결정하는 근거.
- 크기별 peak live: 각 크기가 peak 시점에 얼마나 메모리를 점유하는지.
  count가 많아도 수명이 짧으면 RSS에는 안 잡힌다 — class 설계는 이 값 기준.
"""
import argparse
import sys
from collections import defaultdict


def chunk_of(size):
    """현재 segregated 설계의 실제 chunk 크기: header4 + footer4 + 16B 올림."""
    return (size + 8 + 15) & ~15


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trace", help="bench_driver --trace=FILE 출력 파일")
    ap.add_argument("--top", type=int, default=20, help="크기 분포 상위 N개 (default 20)")
    args = ap.parse_args()

    live = {}                 # ptr -> requested size
    live_req = 0              # 살아있는 요청 bytes 합
    live_real = 0             # 살아있는 chunk bytes 합 (현재 설계 시뮬레이션)
    peak_req = 0
    peak_real = 0
    peak_req_event = 0        # peak_req가 발생한 이벤트 번호
    total_req = 0
    counts = defaultdict(int)         # size -> 요청 횟수
    live_by_size = defaultdict(int)   # size -> 살아있는 count
    peak_by_size = defaultdict(int)   # size -> 살아있는 count 최고점
    unknown_free = 0
    failed = 0

    def add(ptr, size):
        nonlocal live_req, live_real, total_req
        live[ptr] = size
        live_req += size
        live_real += chunk_of(size)
        total_req += size
        counts[size] += 1
        live_by_size[size] += 1
        if live_by_size[size] > peak_by_size[size]:
            peak_by_size[size] = live_by_size[size]

    def remove(ptr):
        nonlocal live_req, live_real, unknown_free
        size = live.pop(ptr, None)
        if size is None:
            unknown_free += 1
            return
        live_req -= size
        live_real -= chunk_of(size)
        live_by_size[size] -= 1

    events = 0
    with open(args.trace) as f:
        for line in f:
            parts = line.split()
            if not parts:
                continue
            events += 1
            op = parts[0]
            if op in ("m", "c"):
                size, ptr = int(parts[1]), int(parts[2], 16)
                if ptr == 0:
                    failed += 1
                    continue
                add(ptr, size)
            elif op == "f":
                remove(int(parts[1], 16))
            elif op == "r":
                # 원자적 resize로 취급: old 제거와 new 추가 사이에서는 peak을 안 본다
                old, size, new = int(parts[1], 16), int(parts[2]), int(parts[3], 16)
                if old:
                    remove(old)
                if new:
                    add(new, size)
                elif size:
                    failed += 1
            else:
                print(f"unknown record: {line.rstrip()}", file=sys.stderr)
                continue
            if live_req > peak_req:
                peak_req = live_req
                peak_req_event = events
            if live_real > peak_real:
                peak_real = live_real

    total_calls = sum(counts.values())
    print(f"events               : {events}")
    print(f"alloc calls          : {total_calls} (failed: {failed}, unknown free: {unknown_free})")
    print(f"total requested      : {total_req:,} bytes")
    print()
    print(f"peak live requested  : {peak_req:,} bytes  <-- 이론적 peak RSS 하한 (event #{peak_req_event})")
    print(f"peak live real (sim) : {peak_real:,} bytes  (현재 설계 x{peak_real / peak_req:.3f})"
          if peak_req else "peak live real (sim) : 0")
    print(f"end live requested   : {live_req:,} bytes ({len(live)} blocks leaked/알 수 없음)")
    print()

    print(f"-- top {args.top} request sizes by count --")
    print(f"{'size':>10} {'count':>12} {'share':>7} {'cum':>7} {'peak_live_cnt':>14} {'peak_live_bytes':>16} {'chunk':>7} {'waste':>6}")
    cum = 0
    for size, cnt in sorted(counts.items(), key=lambda kv: -kv[1])[:args.top]:
        cum += cnt
        ch = chunk_of(size)
        print(f"{size:>10} {cnt:>12} {cnt/total_calls:>6.1%} {cum/total_calls:>6.1%} "
              f"{peak_by_size[size]:>14} {peak_by_size[size]*size:>16,} {ch:>7} {ch-8-size:>6}")

    print()
    print(f"-- top {args.top} request sizes by peak live bytes --")
    print(f"{'size':>10} {'peak_live_cnt':>14} {'peak_live_bytes':>16} {'count':>12} {'chunk':>7} {'waste':>6}")
    by_peak = sorted(peak_by_size.items(), key=lambda kv: -(kv[1] * kv[0]))[:args.top]
    for size, pk in by_peak:
        ch = chunk_of(size)
        print(f"{size:>10} {pk:>14} {pk*size:>16,} {counts[size]:>12} {ch:>7} {ch-8-size:>6}")


if __name__ == "__main__":
    main()
