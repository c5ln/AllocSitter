# AllocSitter

C로 직접 구현한 메모리 할당기(`my_malloc` / `my_free` / `my_calloc` / `my_realloc`)와,
실제 워크로드(tree-sitter로 Redis 소스 파싱)에서 glibc malloc과 성능을 비교하는 벤치마크 하네스입니다.

## 할당기 구조

- **mmap 기반 arena**: `sbrk`는 프로세스에 하나뿐인 program break를 밀어 glibc malloc과 충돌하므로, `mmap(MAP_NORESERVE)`으로 4 GiB 가상 영역을 예약해 두고 그 안에서만 break를 밉니다. 지연 커밋이라 큰 예약도 실제 메모리를 쓰지 않습니다.
- **First-fit 명시적 free list**: 단일 연결 리스트를 앞에서부터 탐색해 맞는 블록을 찾고, 여유가 있으면 분할(split)합니다.
- **Boundary tag (header + footer)**: 각 블록의 앞뒤에 `size | used` 태그를 기록해, `free` 시 좌우 이웃 블록과 즉시 병합(coalescing)합니다.
- **16바이트 정렬**: 모든 payload는 16바이트 정렬, 최소 블록 크기는 16바이트입니다.
- **realloc 최적화**: 오른쪽 이웃이 free이고 공간이 충분하면 복사 없이 제자리 확장하고, 축소 시 남는 공간은 free 블록으로 잘라 반환합니다.

### 검증 (`check_invariant`)

`-DCHECK` 빌드에서는 연산마다 힙 전체를 선형 순회하며 불변식을 검사합니다.

- 블록 정렬 / 최소 크기 / 힙 경계, header == footer
- 선형 순회로 수집한 free 블록 집합과 free list를 교차 검증 (링크 갱신 누락, 사이클 감지)
- 계측 카운터(`freelist_len`)와 실제 리스트 길이 일치 여부

### 계측

병목 분석을 위해 두 층위의 통계를 수집합니다.

- **`AllocStat`** (allocator 내부): free list 탐색 횟수(`scan_steps`, `remove_steps`), 현재 free list 길이, 실제 할당 바이트 합
- **`DataStat`** (driver, 논리적 요청): malloc/calloc/realloc/free 호출 횟수, 요청 바이트 합, 요청 크기의 log2 버킷 히스토그램

## 디렉터리 구성

```
src/
  allocator.c, allocator.h   할당기 본체 + 통계 API
  test.c                     단위 테스트 (정렬, 병합, realloc, 통계 등)
bench/
  driver.c                   tree-sitter로 Redis C 소스를 파싱하는 벤치마크 드라이버
  verify.sh                  정확성 게이트: CHECK 빌드 + 두 allocator 결과 diff
  run_bench.sh               측정 실행기 (CPU 고정, 교차 실행, CSV append)
  run_sweep.sh               입력 크기 스윕 (30/60/90개 → .c 전체 → .c+.h 전체)
  plot_results.py            CSV 집계 및 그래프 생성
  redis-src/                 벤치마크 입력 (Redis 소스 스냅샷, REDIS_VERSION.txt 참고)
  results/                   측정 결과 CSV
vendor/
  tree-sitter, tree-sitter-c git submodule
```

## 빌드 및 실행

```bash
git clone --recurse-submodules <repo-url>   # tree-sitter submodule 필요

make            # 단위 테스트 빌드 (myallocator.out, -DCHECK 포함)
./myallocator.out
```

### 벤치마크

3단계 파이프라인으로 구성되어 있고, 검증을 통과해야 측정이 진행됩니다.

```bash
make bench-verify   # Stage 1: CHECK 빌드로 invariant 검사 + default/mmap-arena 파싱 결과 diff
make bench          # Stage 2: 측정 (bench-verify 통과가 선행 조건)
make bench-sweep    # Stage 2': 입력 크기별 스케일링 곡선 측정
make bench-plot     # Stage 3: results/ 집계·시각화
make bench-plot-sweep
```

측정 파라미터는 환경 변수로 조절합니다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `BENCH_RUNS` | 5 | 프로세스 외부 반복 (ASLR 등 프로세스 간 분산 샘플링) |
| `BENCH_ITERS` | 10 | 프로세스 내부 파싱 반복 |
| `BENCH_WARMUP` | 2 | 측정 제외 워밍업 반복 |
| `BENCH_CPU` | 0 | `taskset`으로 고정할 CPU 코어 |

벤치마크는 Redis 소스(.c 133개 기준 약 7 MB)를 tree-sitter C 파서로 파싱하며, 파일 로드는 측정 전에 기본 allocator로 끝내 두고 **파싱 중의 할당만** 커스텀 allocator(`--allocator=mmap-arena`) 또는 glibc(`--allocator=default`)로 수행합니다. 결과 CSV에는 시간 통계(mean/median/min/max, MB/s), peak RSS와 위의 계측 통계가 함께 기록됩니다.

## 노이즈 대책

- `taskset`으로 CPU 코어 고정 (코어 마이그레이션 제거)
- 두 allocator를 A,B,A,B 순으로 교차 실행 (시스템 상태 변화가 한쪽에 쏠리지 않게)
- 프로세스 자체를 여러 번 재실행 (`BENCH_RUNS`)하여 프로세스 간 분산 샘플링
- CSV 스키마가 바뀌면 기존 파일을 rotate 후 새로 기록
