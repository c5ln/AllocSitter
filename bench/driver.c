// Redis C source parsing benchmark for tree-sitter.
//
// The source files are loaded with the process default allocator before timing.
// Timed iterations only cover tree-sitter parser/tree allocation, parsing,
// cursor traversal, and cleanup.
#include <dirent.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <time.h>
#include <tree_sitter/api.h>

#include "../src/allocator.h"

extern const TSLanguage *tree_sitter_c(void);

typedef enum {
    ALLOC_DEFAULT,
    ALLOC_MMAP_ARENA,
} AllocatorMode;

typedef struct {
    char *path;
    char *data;
    size_t len;
} SourceFile;

typedef struct {
    SourceFile *items;
    size_t len;
    size_t cap;
} SourceVec;

typedef struct {
    long files;
    long errors;
    long nodes;
    size_t bytes;
} ParseStats;

static const char *allocator_name(AllocatorMode mode) {
    return mode == ALLOC_MMAP_ARENA ? "mmap-arena" : "default";
}

static void *w_malloc(size_t n) {
    return my_malloc(n);
}

static void *w_calloc(size_t c, size_t n) {
    return my_calloc(c, n);
}

static void *w_realloc(void *p, size_t n) {
    return my_realloc(p, n);
}

static void w_free(void *p) {
    my_free(p);
}

static double now_sec(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

// Linux의 ru_maxrss는 KiB 단위. 프로세스 시작 이후의 peak이므로
// 두 allocator 실행을 같은 조건(같은 입력 사전 로드)에서 비교하는 용도.
static long peak_rss_kb(void) {
    struct rusage ru;
    if (getrusage(RUSAGE_SELF, &ru) != 0) return -1;
    return ru.ru_maxrss;
}

// CHECK 빌드에서만 힙 불변식 검사. 커스텀 allocator를 실제로 쓴 경우에만 의미가 있다.
static void maybe_check_heap(AllocatorMode allocator) {
#ifdef CHECK
    if (allocator == ALLOC_MMAP_ARENA) check_invariant();
#else
    (void)allocator;
#endif
}

static char *xstrdup(const char *s) {
    size_t n = strlen(s) + 1;
    char *copy = malloc(n);
    if (!copy) return NULL;
    memcpy(copy, s, n);
    return copy;
}

static int vec_push_path(SourceVec *vec, const char *path) {
    if (vec->len == vec->cap) {
        size_t next = vec->cap ? vec->cap * 2 : 128;
        SourceFile *items = realloc(vec->items, next * sizeof(*items));
        if (!items) return -1;
        vec->items = items;
        vec->cap = next;
    }
    vec->items[vec->len].path = xstrdup(path);
    vec->items[vec->len].data = NULL;
    vec->items[vec->len].len = 0;
    if (!vec->items[vec->len].path) return -1;
    vec->len++;
    return 0;
}

static int has_suffix(const char *s, const char *suffix) {
    size_t slen = strlen(s);
    size_t tlen = strlen(suffix);
    return slen >= tlen && strcmp(s + slen - tlen, suffix) == 0;
}

static int is_source_path(const char *path, int include_headers) {
    return has_suffix(path, ".c") || (include_headers && has_suffix(path, ".h"));
}

static int collect_sources(SourceVec *vec, const char *root, int include_headers) {
    DIR *dir = opendir(root);
    if (!dir) {
        fprintf(stderr, "cannot open directory %s: %s\n", root, strerror(errno));
        return -1;
    }

    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL) {
        if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0) {
            continue;
        }

        size_t n = strlen(root) + 1 + strlen(entry->d_name) + 1;
        char *path = malloc(n);
        if (!path) {
            closedir(dir);
            return -1;
        }
        snprintf(path, n, "%s/%s", root, entry->d_name);

        struct stat st;
        if (stat(path, &st) == 0) {
            if (S_ISDIR(st.st_mode)) {
                if (collect_sources(vec, path, include_headers) != 0) {
                    free(path);
                    closedir(dir);
                    return -1;
                }
            } else if (S_ISREG(st.st_mode) && is_source_path(path, include_headers)) {
                if (vec_push_path(vec, path) != 0) {
                    free(path);
                    closedir(dir);
                    return -1;
                }
            }
        }
        free(path);
    }

    closedir(dir);
    return 0;
}

static int cmp_source_path(const void *a, const void *b) {
    const SourceFile *sa = a;
    const SourceFile *sb = b;
    return strcmp(sa->path, sb->path);
}

static int read_file(SourceFile *src) {
    FILE *f = fopen(src->path, "rb");
    if (!f) {
        fprintf(stderr, "cannot read %s: %s\n", src->path, strerror(errno));
        return -1;
    }
    if (fseek(f, 0, SEEK_END) != 0) {
        fclose(f);
        return -1;
    }
    long sz = ftell(f);
    if (sz < 0) {
        fclose(f);
        return -1;
    }
    rewind(f);

    src->data = malloc((size_t)sz + 1);
    if (!src->data) {
        fclose(f);
        return -1;
    }
    src->len = fread(src->data, 1, (size_t)sz, f);
    src->data[src->len] = '\0';
    fclose(f);
    return 0;
}

static int load_sources(SourceVec *vec) {
    for (size_t i = 0; i < vec->len; i++) {
        if (read_file(&vec->items[i]) != 0) return -1;
    }
    return 0;
}

static long count_nodes_and_errors(TSTree *tree, int *has_error) {
    TSNode root = ts_tree_root_node(tree);
    *has_error = ts_node_has_error(root) ? 1 : 0;

    long nodes = 0;
    TSTreeCursor cur = ts_tree_cursor_new(root);
    int descending = 1;
    while (1) {
        nodes++;
        if (descending && ts_tree_cursor_goto_first_child(&cur)) continue;
        if (ts_tree_cursor_goto_next_sibling(&cur)) {
            descending = 1;
            continue;
        }
        descending = 0;
        if (!ts_tree_cursor_goto_parent(&cur)) break;
    }
    ts_tree_cursor_delete(&cur);
    return nodes;
}

static int parse_once(const SourceVec *sources, ParseStats *stats) {
    memset(stats, 0, sizeof(*stats));

    TSParser *parser = ts_parser_new();
    if (!parser) {
        fprintf(stderr, "ts_parser_new failed\n");
        return -1;
    }
    if (!ts_parser_set_language(parser, tree_sitter_c())) {
        fprintf(stderr, "ts_parser_set_language(tree_sitter_c) failed\n");
        ts_parser_delete(parser);
        return -1;
    }

    for (size_t i = 0; i < sources->len; i++) {
        const SourceFile *src = &sources->items[i];
        TSTree *tree = ts_parser_parse_string(parser, NULL, src->data, (uint32_t)src->len);
        if (!tree) {
            fprintf(stderr, "parse failed: %s\n", src->path);
            ts_parser_delete(parser);
            return -1;
        }

        int has_error = 0;
        stats->nodes += count_nodes_and_errors(tree, &has_error);
        stats->errors += has_error;
        stats->bytes += src->len;
        stats->files++;
        ts_tree_delete(tree);
    }

    ts_parser_delete(parser);
    return 0;
}

static int cmp_double(const void *a, const void *b) {
    double da = *(const double *)a;
    double db = *(const double *)b;
    return (da > db) - (da < db);
}

static double median(double *values, int n) {
    qsort(values, (size_t)n, sizeof(*values), cmp_double);
    if (n % 2) return values[n / 2];
    return (values[n / 2 - 1] + values[n / 2]) / 2.0;
}

static void free_sources(SourceVec *vec) {
    for (size_t i = 0; i < vec->len; i++) {
        free(vec->items[i].path);
        free(vec->items[i].data);
    }
    free(vec->items);
}

static void usage(const char *argv0) {
    fprintf(stderr,
            "usage: %s [options] [file.c ...]\n"
            "options:\n"
            "  --allocator=default|mmap-arena\n"
            "  --root=DIR              collect Redis sources from DIR (default: bench/redis-src)\n"
            "  --iters=N               measured iterations (default: 5)\n"
            "  --warmup=N              warmup iterations (default: 1)\n"
            "  --include-headers       include .h files as parser inputs\n"
            "  --csv                   print one CSV row instead of text\n",
            argv0);
}

int main(int argc, char **argv) {
    AllocatorMode allocator = ALLOC_DEFAULT;
    const char *root = "bench/redis-src";
    int iters = 5;
    int warmup = 1;
    int include_headers = 0;
    int csv = 0;
    SourceVec sources = {0};
    int explicit_files = 0;

    for (int i = 1; i < argc; i++) {
        if (strncmp(argv[i], "--allocator=", 12) == 0) {
            const char *name = argv[i] + 12;
            if (strcmp(name, "default") == 0) {
                allocator = ALLOC_DEFAULT;
            } else if (strcmp(name, "mmap-arena") == 0) {
                allocator = ALLOC_MMAP_ARENA;
            } else {
                usage(argv[0]);
                return 2;
            }
        } else if (strncmp(argv[i], "--root=", 7) == 0) {
            root = argv[i] + 7;
        } else if (strncmp(argv[i], "--iters=", 8) == 0) {
            iters = atoi(argv[i] + 8);
        } else if (strncmp(argv[i], "--warmup=", 9) == 0) {
            warmup = atoi(argv[i] + 9);
        } else if (strcmp(argv[i], "--include-headers") == 0) {
            include_headers = 1;
        } else if (strcmp(argv[i], "--csv") == 0) {
            csv = 1;
        } else if (strcmp(argv[i], "--help") == 0) {
            usage(argv[0]);
            return 0;
        } else if (argv[i][0] == '-') {
            usage(argv[0]);
            return 2;
        } else {
            explicit_files = 1;
            if (vec_push_path(&sources, argv[i]) != 0) {
                fprintf(stderr, "out of memory\n");
                return 1;
            }
        }
    }

    if (iters <= 0 || warmup < 0) {
        usage(argv[0]);
        return 2;
    }

    if (!explicit_files && collect_sources(&sources, root, include_headers) != 0) {
        free_sources(&sources);
        return 1;
    }
    qsort(sources.items, sources.len, sizeof(*sources.items), cmp_source_path);

    if (sources.len == 0) {
        fprintf(stderr, "no source files found\n");
        free_sources(&sources);
        return 1;
    }
    if (load_sources(&sources) != 0) {
        free_sources(&sources);
        return 1;
    }

    if (allocator == ALLOC_MMAP_ARENA) {
        ts_set_allocator(w_malloc, w_calloc, w_realloc, w_free);
    }

    ParseStats last = {0};
    for (int i = 0; i < warmup; i++) {
        if (parse_once(&sources, &last) != 0) {
            free_sources(&sources);
            return 1;
        }
        maybe_check_heap(allocator);
    }

    double *times = calloc((size_t)iters, sizeof(*times));
    double *sorted = calloc((size_t)iters, sizeof(*sorted));
    if (!times || !sorted) {
        free(times);
        free(sorted);
        free_sources(&sources);
        return 1;
    }

    double total = 0.0;
    double min = 0.0;
    double max = 0.0;
    for (int i = 0; i < iters; i++) {
        double t0 = now_sec();
        if (parse_once(&sources, &last) != 0) {
            free(times);
            free(sorted);
            free_sources(&sources);
            return 1;
        }
        double t1 = now_sec();
        maybe_check_heap(allocator);
        times[i] = t1 - t0;
        sorted[i] = times[i];
        total += times[i];
        if (i == 0 || times[i] < min) min = times[i];
        if (i == 0 || times[i] > max) max = times[i];
    }

    double mean = total / iters;
    double med = median(sorted, iters);
    double mbps = mean > 0.0 ? ((double)last.bytes / 1e6) / mean : 0.0;
    long rss_kb = peak_rss_kb();

    if (csv) {
        printf("allocator,iters,warmup,files,bytes,nodes,errors,mean_sec,median_sec,min_sec,max_sec,mbps,peak_rss_kb\n");
        printf("%s,%d,%d,%ld,%zu,%ld,%ld,%.9f,%.9f,%.9f,%.9f,%.2f,%ld\n",
               allocator_name(allocator), iters, warmup, last.files, last.bytes,
               last.nodes, last.errors, mean, med, min, max, mbps, rss_kb);
    } else {
        printf("allocator    : %s\n", allocator_name(allocator));
        printf("iterations   : %d measured, %d warmup\n", iters, warmup);
        printf("files parsed : %ld\n", last.files);
        printf("total bytes  : %zu\n", last.bytes);
        printf("total nodes  : %ld\n", last.nodes);
        printf("files w/error: %ld\n", last.errors);
        printf("mean time    : %.6f s\n", mean);
        printf("median time  : %.6f s\n", med);
        printf("min/max time : %.6f / %.6f s\n", min, max);
        printf("throughput   : %.2f MB/s\n", mbps);
        printf("peak RSS     : %ld KiB\n", rss_kb);
    }

    free(times);
    free(sorted);
    free_sources(&sources);
    return 0;
}
