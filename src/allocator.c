#include "allocator.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <sys/mman.h>

static void *free_head = NULL; // free list 머리


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
static void freelist_push(uint32_t *header){
    *(void**)((char*)header + 4) = free_head;
    free_head = (void*)header;
}

// free list에서 해당 블록을 제거 (없으면 무시)
static void freelist_remove(uint32_t *header){
    void **link = &free_head;
    while(*link){
        if(*link == (void*)header){
            *link = *(void**)((char*)header + 4);
            return;
        }
        link = (void**)((char*)(*link) + 4);
    }
}

void *my_malloc(size_t size){
    // if(size==0) return NULL;

    size_t need = (size + 4 + 4 + 15) & ~(size_t)15;  // 16의 배수로 올림 연산. 이진수의 관점으로 보면 된다.
    void **link = &free_head;
    while(*link)
    {
        uint32_t* h = (uint32_t*)(*link);
        size_t chunk_size = *h & ~15u;
        if(chunk_size >= need + 16 ) {
            set_tags(h, need | 1); // 사용 블록 태그

            void *payload = (char*)h + 4; // payload 위치

            uint32_t *split_header = (uint32_t*)((char*)h + need);
            set_tags(split_header, (uint32_t)(chunk_size - need)); // 남은 free 블록

            *link = *(void**)payload; // 원래 블록 unlink
            freelist_push(split_header); // 남은 블록 push
            return payload;
        }
        if(chunk_size >= need){
            set_tags(h, (uint32_t)chunk_size | 1); // 통째로 사용 표기

            void *payload = (char*)h + 4;
            *link = *(void**)payload;
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
    
    // coalescing logic
    // 오른쪽 블록 찾기
    size_t size = *header & ~15u;
    uint32_t* right_header = (uint32_t*)((char*)header + size);
    // 오른쪽 블럭도 free 영역인 경우
    if ((char*)right_header < (char*)heap_hi && (*right_header & 1) == 0)
    {
        freelist_remove(right_header); // 오른쪽 블록 unlink
        set_tags(header, (uint32_t)(size + (*right_header & ~15u))); // 병합 크기로 태그
    }
    // 왼쪽 블럭 찾기
    uint32_t* left_footer = (uint32_t*)((char*)header - 4);
    size_t left_size = *left_footer & ~15u;
    // 왼쪽 블럭도 free인 경우
    if ((char*)left_footer > (char*)heap_lo && (*left_footer & 1) == 0)
    {
        uint32_t* left_header = (uint32_t*)((char*)left_footer-left_size+4);
        freelist_remove(left_header); // 왼쪽 블록 unlink
        set_tags(left_header, (uint32_t)((*header&~15u) + (*left_header & ~15u))); // 병합 크기로 태그
        freelist_push(left_header);
        return;
    }

    freelist_push(header);
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
    // 기존의 block size
    size_t old_size = *((uint32_t*)ptr-1) & ~15u;
    // 새로 필요한 size
    size_t new_size = (size + 8 + 15) & ~15u; 

    // 새로운 size > old size
    if(new_size > old_size){
        // 잔여 공간 여부 파악. 어떻게?
        uint32_t *right_header = (uint32_t*)((char*)ptr - 4 + old_size);
        // 1. 이웃이 존재하고 free 상태이며 공간 충분하면 위치 그대로 확장
        if((char*)right_header < (char*)heap_hi && (*right_header & 1)==0 &&old_size + (size_t)(*right_header & ~15u) >= new_size)
        {   
            size_t combined = old_size + *right_header;
            freelist_remove(right_header); // 오른쪽 free 블록 unlink
            // 합치고 남은 공간이 최소 크기인 16byte 이상이면 spliting
            if(combined >= new_size + 16)
            {
                uint32_t *split_header = (uint32_t*)((char*)ptr + new_size - 4);
                set_tags(split_header, (uint32_t)(combined - new_size)); // 남은 free 블록
                freelist_push(split_header);
                set_tags((uint32_t*)ptr - 1, (uint32_t)new_size | 1); // 앞 블록
            }
            // 남는 공간이 16byte보다 작으면 통째로 흡수
            else {
                set_tags((uint32_t*)ptr - 1, (uint32_t)combined | 1);
            }
            return ptr;
        }
        // 2. block 위치 이동
        else {
        void *new_ptr = my_malloc(new_size);
        if(new_ptr == NULL) return NULL;
        memcpy(new_ptr, ptr, old_size-8);
        my_free(ptr);
        return new_ptr;
        }
    }
    // 새로운 size <= old size
    else if(new_size <= old_size)
    {
        size_t leftover = old_size - new_size;
        // 남는 공간이 최소 블록(16byte) 이상일 때만 spliting
        if(leftover >= 16)
        {
            set_tags((uint32_t*)ptr - 1, (uint32_t)new_size | 1); // 앞 블록을 new_size로

            uint32_t *free_header = (uint32_t*)((char*)ptr + new_size - 4);
            set_tags(free_header, (uint32_t)leftover); // 뒤쪽 남은 블록을 free 블록으로
            freelist_push(free_header);
        }
        // leftover < 16 이면 자르지 않고 old_size 그대로 둔다
        return ptr;
    }
    return NULL;
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
      

      // free list 탐색 + 교차 검증
    int freelist_count = 0;
    void *p = free_head;
    while (p) {
        assert((*(uint32_t*)p & 1) == 0); // free 영역인지 체크
        assert(p >= heap_lo && p <= heap_hi); // 힙 범위 체크
        assert(freelist_count < MAX_NODES);              // 사이클/폭주 감지

        int found = 0;
        for (int i = 0; i < linear_count; i++) {
            if (linear_free_list[i] == p) { found = 1; break; }
        }
        assert(found);                                   // 교차 검증

        freelist_count++;
        p = *(void**)((char*)p + 4); // free list의 다음 블록 체크
    }

    // 개수 일치 = 두 집합이 같음
    assert(linear_count == freelist_count);              // link 갱신 누락 검출
}
