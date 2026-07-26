#ifndef ALLOCATOR_H
#define ALLOCATOR_H

#include <stddef.h>
#include <unistd.h>
#include <stdint.h>

typedef struct {
    uint32_t units;
} Header;

// allocator 내부 동작 통계
typedef struct {
    uint64_t scan_steps; // allocation을 위해 while 내에서 탐색 횟수
    uint64_t remove_steps; // remove 를 위해 while 내에서의 탐색 횟수
    uint64_t freelist_len; // 현재 free list length
    uint64_t real_bytes; // 실제 할당 bytes 합
    uint64_t live_real_bytes;      // 지금 살아있는 블록들의 chunk bytes 합 (header/footer/16B 올림 포함)
    uint64_t peak_live_real_bytes; // live_real_bytes 최고점 (reset 시 현재 live 값으로 재시작)
    uint64_t arena_used_bytes;     // arena에서 잘라 쓴 총량 = touch된 페이지 상한 (단조 증가)
} AllocStat;

// size가 속하는 log2 버킷. 버킷 i는 [2^i, 2^(i+1)) 구간.
static inline unsigned size_bucket(size_t size){
    return 63u - (unsigned)__builtin_clzll((uint64_t)size | 1u);
}

void *my_malloc(size_t size);
void my_free(void *ptr);
void *my_calloc(size_t nmemb, size_t size);
void *my_realloc(void *ptr, size_t size);

AllocStat alloc_stat_get(void);  // 현재 통계의 스냅샷
void alloc_stat_reset(void);     // 누적 카운터 리셋 (freelist_len은 보존)

void check_invariant();

#endif