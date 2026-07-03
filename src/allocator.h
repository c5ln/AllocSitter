#ifndef ALLOCATOR_H
#define ALLOCATOR_H

#include <stddef.h>
#include <unistd.h>
#include <stdint.h>

typedef struct {
    uint32_t units;
} Header;

void *my_malloc(size_t size);
void my_free(void *ptr);
void *my_calloc(size_t nmemb, size_t size);
void *my_realloc(void *ptr, size_t size);

void check_invariant();

#endif