CC = gcc
CFLAGS = -g -Wall -Wextra -std=c11 -D_DEFAULT_SOURCE -DCHECK
VPATH = src
OBJS= test.o allocator.o
TARGET= myallocator.out
LIB= liballoc.so

BENCH_TARGET = bench/bench_driver
BENCH_CHECK_TARGET = bench/bench_driver_check
BENCH_RESULTS_DIR = bench/results
BENCH_RUNS ?= 5
BENCH_ITERS ?= 10
BENCH_WARMUP ?= 2
TREE_SITTER_DIR ?= vendor/tree-sitter
TREE_SITTER_C_DIR ?= vendor/tree-sitter-c
BENCH_INCLUDES = \
	-I$(TREE_SITTER_DIR)/lib/include \
	-I$(TREE_SITTER_DIR)/lib/src \
	-I$(TREE_SITTER_C_DIR)/src \
	-Wno-unused-parameter \
	-Wno-unused-but-set-variable
BENCH_CFLAGS = -O3 -DNDEBUG -Wall -Wextra -std=c11 -D_DEFAULT_SOURCE $(BENCH_INCLUDES)
# 검증 빌드: assert가 살아 있어야 하므로 NDEBUG 금지, 측정용이 아니므로 -O0
BENCH_CHECK_CFLAGS = -O0 -g -DCHECK -Wall -Wextra -std=c11 -D_DEFAULT_SOURCE $(BENCH_INCLUDES)
BENCH_SRCS = bench/driver.c src/allocator.c \
	$(TREE_SITTER_DIR)/lib/src/lib.c \
	$(TREE_SITTER_C_DIR)/src/parser.c

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $(OBJS)

# LD_PRELOAD용 공유 라이브러리.
# PIC/비PIC 오브젝트 혼용을 피하려고 소스에서 바로 -fPIC로 빌드한다.
$(LIB): src/allocator.c src/preload.c src/allocator.h
	$(CC) $(CFLAGS) -fPIC -shared -o $@ src/allocator.c src/preload.c

clean:
	rm -f *.o
	rm -f $(TARGET) $(LIB)
	rm -f $(BENCH_TARGET) $(BENCH_CHECK_TARGET)

bench-driver: $(BENCH_TARGET)

$(BENCH_TARGET): $(BENCH_SRCS) src/allocator.h
	$(CC) $(BENCH_CFLAGS) -o $@ $(BENCH_SRCS)

$(BENCH_CHECK_TARGET): $(BENCH_SRCS) src/allocator.h
	$(CC) $(BENCH_CHECK_CFLAGS) -o $@ $(BENCH_SRCS)

# Stage 1: 정확성 검증. 실패하면 bench/bench-sweep이 진행되지 않는다.
bench-verify: $(BENCH_CHECK_TARGET)
	bash bench/verify.sh $(BENCH_CHECK_TARGET)

# Stage 2: 측정. bench-verify 의존으로 게이트를 강제한다.
bench: bench-verify $(BENCH_TARGET)
	BENCH_RUNS=$(BENCH_RUNS) BENCH_ITERS=$(BENCH_ITERS) BENCH_WARMUP=$(BENCH_WARMUP) \
		bash bench/run_bench.sh

bench-sweep: bench-verify $(BENCH_TARGET)
	BENCH_ITERS=$(BENCH_ITERS) BENCH_WARMUP=$(BENCH_WARMUP) \
		bash bench/run_sweep.sh

# Stage 3: 집계/시각화
bench-plot:
	python3 bench/plot_results.py $(BENCH_RESULTS_DIR)

bench-plot-sweep:
	python3 bench/plot_results.py $(BENCH_RESULTS_DIR)/sweep -o $(BENCH_RESULTS_DIR)/sweep/plots

bench-clean:
	rm -f $(BENCH_TARGET) $(BENCH_CHECK_TARGET)
	rm -rf $(BENCH_RESULTS_DIR)

test.o: test.c allocator.h
allocator.o: allocator.c allocator.h

.PHONY: clean bench-driver bench-verify bench bench-sweep bench-plot bench-plot-sweep bench-clean
