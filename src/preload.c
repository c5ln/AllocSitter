// LD_PRELOAD용 표준 심볼 wrapper.
#include "allocator.h"
#include <stddef.h>

void *malloc(size_t size) {
    return my_malloc(size);
}

void free(void *ptr) {
    my_free(ptr);
}

void *calloc(size_t nmemb, size_t size) {
    return my_calloc(nmemb, size);
}

void *realloc(void *ptr, size_t size) {
    return my_realloc(ptr, size);
}
