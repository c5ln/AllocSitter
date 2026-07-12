#include "segregated_allocator.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <sys/mman.h>

#define NUM_BINS 11
static void *free_head[NUM_BINS] = {NULL, }; // 각 bin의 free list 머리

// 성능 분석 용
static AllocStat alloc_stat;


// invariant 체크용
static void *heap_lo = NULL;
static void *heap_hi = NULL;

// mmap 기반 arena 
// sbrk는 프로세스에 하나뿐인 program break를 밀기 때문에 glibc 등 다른 malloc과 충돌한다.
// mmap으로 영역을 예약하고, 그 안에서만 break를 민다. MAP_NORESERVE + 지연 커밋이라 큰 예약도 공짜다.
#define ARENA_SIZE ((size_t)4 << 30)  // 4 GiB 가상 예약 
static char *arena_base = NULL;   // mmap 영역 시작
static char *arena_cur  = NULL;   // 현재 break (다음 할당 위치)
static char *arena_end  = NULL;   // 영역 상한

// sbrk 대체. n==0이면 현재 break 반환(=sbrk(0)). 실패 시 (void*)-1.
static void *arena_extend(size_t n){
    if(arena_base == NULL){       // 최초 1회: 영역 예약
        void *m = mmap(NULL, ARENA_SIZE, PROT_READ|PROT_WRITE,
                       MAP_PRIVATE|MAP_ANONYMOUS|MAP_NORESERVE, -1, 0);
        if(m == MAP_FAILED) return (void*)-1;
        arena_base = arena_cur = (char*)m;
        arena_end  = arena_base + ARENA_SIZE;
    }
    void *old = arena_cur;
    if(n == 0) return old;
    if(arena_cur + n > arena_end) return (void*)-1; // 영역 소진
    arena_cur += n;
    return old;
}


// 블록 헤더/footer에 동일한 tag(size|flag) 기록. footer 위치는 tag의 size로 계산
static void set_tags(uint32_t *header, uint32_t tag){
    *header = tag;
    *(uint32_t*)((char*)header + (tag & ~15u) - 4) = tag;
}

// free list 맨 앞에 블록 push (payload 첫 워드에 next 포인터 저장)
static void freelist_push(uint32_t *header, uint32_t bin){
    *(void**)((char*)header + 4) = free_head[bin];
    free_head[bin] = (void*)header;

    // 성능 측정용
    alloc_stat.freelist_len++;
}

static int32_t size_to_bin(size_t size)
{
    if(size <= 16) return 0;
    if(size <= 32) return 1;
    if(size <= 64) return 2;
    if(size <= 80) return 3;
    if(size <= 96) return 4;
    if(size <= 112) return 5;
    if(size <= 128) return 6;
    if(size <= 256) return 7;
    if(size <= 512) return 8;
    if(size <= 1024) return 9;
    else return 10;
}

void *my_malloc(size_t size){
    // if(size==0) return NULL;

    size_t need = (size + 4 + 4 + 15) & ~(size_t)15;  // 16의 배수로 올림 연산. 이진수의 관점으로 보면 된다.

    uint32_t bin = size_to_bin(need);
    void **link = &free_head[bin];

    while(*link)
    {
        // 성능 측정용
        alloc_stat.scan_steps++;

        uint32_t* h = (uint32_t*)(*link);
        size_t chunk_size = *h & ~15u;
        if(chunk_size >= need + 16 ) {

            // 성능 측정용
            alloc_stat.real_bytes += need;
    
            set_tags(h, need | 1); // 사용 블록 태그

            void *payload = (char*)h + 4; // payload 위치

            uint32_t *split_header = (uint32_t*)((char*)h + need);
            set_tags(split_header, (uint32_t)(chunk_size - need)); // 남은 free 블록

            *link = *(void**)payload; // 원래 블록 unlink

            // 성능 측정용
            alloc_stat.freelist_len--;
            
            freelist_push(split_header,size_to_bin(*split_header)); // 남은 블록 push
            return payload;
        }
        if(chunk_size >= need){
            set_tags(h, (uint32_t)chunk_size | 1); // 통째로 사용 표기

            // 성능 측정용
            alloc_stat.real_bytes += chunk_size;

            void *payload = (char*)h + 4;
            *link = *(void**)payload;

            // 성능 측정용
            alloc_stat.freelist_len--;

            return payload;
        }
        link = (void**)((char*)(*link) + 4);
    }
    uintptr_t cur = (uintptr_t)arena_extend(0);
    uintptr_t pad = ((uintptr_t)12 - cur) & 15; // 하위 4비트만 가져오기

     // flag 추가. LSB가 1이면 사용중
    void *p = arena_extend(need+pad);

    // heap 천장 체크
    heap_hi = arena_extend(0);

    if(p==(void*)(-1)){
        return NULL;
    }

    // 성능 계측용
    alloc_stat.real_bytes += need+pad;

    char *header = (char*)p + pad;
    char *payload = header+4;
    set_tags((uint32_t*)header, (uint32_t)need | 1);

    
    //heap 시작점 체크
    if(heap_lo == NULL) heap_lo = (void*)header;

    return (void*)payload;
}

void my_free(void *ptr){
    if(ptr == NULL) return;
    uint32_t *header = (uint32_t*)ptr - 1; 
    if(!(*header&1)) return; // double free 방지
    
    *header = *header & (~1u);

    uint32_t bin = size_to_bin(*header);
    freelist_push(header,bin);
}


void *my_calloc(size_t nmemb, size_t size){
    if(nmemb == 0 || size == 0) return NULL;
    if(nmemb > SIZE_MAX / size) return NULL; // 곱셈 오버플로 차단

    size_t total = nmemb * size;
    void *p = my_malloc(total);
    if(p) memset(p, 0, total);
    return p;
}
void *my_realloc(void *ptr, size_t size)
{
    if(ptr == NULL) return my_malloc(size);
    if(size==0 && ptr != NULL) {
        my_free(ptr);
        return NULL;
    }
    void *new_ptr = my_malloc(size);
    if(new_ptr == NULL) return NULL; // 실패 시 옛 블록은 그대로 둔다

    size_t old_payload = (*((uint32_t*)ptr - 1) & ~15u) - 8; // chunk - header4 - footer4
    size_t copy = old_payload < size ? old_payload : size; // 옛 블록 경계 밖을 읽지 않도록
    memcpy(new_ptr, ptr, copy);
    my_free(ptr);
    return new_ptr;
}

void check_invariant()
{
    #define MAX_NODES 500000
    static void *linear_free_list[MAX_NODES];
    int linear_count = 0;

    // 블록 단위 검사 + chunk 수집
    for(char* p = heap_lo; p < (char*)heap_hi;){

        size_t chunk = *(uint32_t*)p & ~15u;

        assert(chunk%16==0); // 메모리 정렬 확인
        assert(chunk >= 16); // 메모리 최소 크기 확인
        assert(p+chunk <= (char*)heap_hi); // chunk가 heap 안 넘는지
        assert((*(uint32_t*)(p + chunk - 4) & ~15u) == chunk); // header 크기 == footer 크기
     
        if ((*(uint32_t*)p & 1) == 0) {
            assert(linear_count < MAX_NODES); // 수집 배열 overflow 방지
            linear_free_list[linear_count++] = p ;
        }

        p += chunk;  
    }
      

      
    // free list 탐색 + 교차 검증 (bin 별로)
    int freelist_count = 0;
    for (int bin = 0; bin < 11; bin++) {
        void *p = free_head[bin];
        while (p) {
            assert((*(uint32_t*)p & 1) == 0);        // free 영역인지 체크
            assert(p >= heap_lo && p <= heap_hi);    // 힙 범위 체크
            assert(freelist_count < MAX_NODES);      // 사이클/폭주 감지

            // segregated : 블록 크기가 자기가 들어있는 bin과 일치하는지
            assert(size_to_bin(*(uint32_t*)p & ~15u) == bin);

            int found = 0;
            for (int i = 0; i < linear_count; i++) {
                if (linear_free_list[i] == p) { found = 1; break; }
            }
            assert(found);                           // 교차 검증

            freelist_count++;
            p = *(void**)((char*)p + 4);
        }
    }

    // 개수 일치 = 두 집합이 같음
    assert(linear_count == freelist_count);

    // 측정 검증: freelist_len ±1 누락이 있으면 실제 리스트 길이와 어긋난다
    assert((uint64_t)freelist_count == alloc_stat.freelist_len);
}

// 통계 스냅샷. 복사본을 반환하므로 driver가 읽는 동안 원본이 변해도 안전하다.
AllocStat alloc_stat_get(void){
    return alloc_stat;
}

// 누적 카운터만 0으로. freelist_len은 현재 상태값이라 보존해야한다.
void alloc_stat_reset(void){
    uint64_t len = alloc_stat.freelist_len;
    memset(&alloc_stat, 0, sizeof alloc_stat);
    alloc_stat.freelist_len = len;
}
