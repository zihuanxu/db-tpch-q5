/**
 * @file    vector_join.c
 * @brief   Minimal Vector Join (VJ) implementation.
 */

#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/time.h>
#include <unistd.h>

#include "vector_join.h"
#include "affinity.h"
#include "cpu_mapping.h"
#include "rdtsc.h"

typedef struct vector_index_t vector_index_t;
typedef struct vj_arg_t vj_arg_t;

struct vector_index_t {
    void * payloads;
    uint8_t * present;
    uint64_t nslots;
    uint32_t payload_width;
    uint64_t payload_bytes;
    uint64_t present_bytes;
    uint64_t total_bytes;
};

struct vj_arg_t {
    int32_t tid;
    const vector_index_t * index;
    relation_t relS;
    int64_t num_results;
};

static uint32_t vj_payload_width = 4;
static int vj_use_hugepage = 0;

static void
vj_die(const char * msg);

static uint64_t
checked_mul_bytes(uint64_t nitems, uint64_t item_size, const char * msg)
{
    if(item_size != 0 && nitems > SIZE_MAX / item_size) {
        vj_die(msg);
    }

    return nitems * item_size;
}

static uint64_t
checked_add_bytes(uint64_t lhs, uint64_t rhs, const char * msg)
{
    if(lhs > UINT64_MAX - rhs) {
        vj_die(msg);
    }

    return lhs + rhs;
}

static void *
vj_calloc(uint64_t nitems, uint64_t item_size, uint64_t * requested_bytes,
          const char * msg)
{
    void * ptr;
    uint64_t bytes = checked_mul_bytes(nitems, item_size, msg);

    if(vj_use_hugepage) {
        long page_size = sysconf(_SC_PAGESIZE);
        size_t alignment = page_size > 0 ? (size_t) page_size : 4096;

        if(bytes > SIZE_MAX) {
            vj_die(msg);
        }
        if(posix_memalign(&ptr, alignment, (size_t) bytes) != 0 || !ptr) {
            perror("[ERROR] VJ allocation failed");
            exit(EXIT_FAILURE);
        }
        memset(ptr, 0, (size_t) bytes);
        if(madvise(ptr, (size_t) bytes, MADV_HUGEPAGE) != 0) {
            fprintf(stderr, "[WARN ] VJ madvise(MADV_HUGEPAGE) failed: %s\n",
                    strerror(errno));
        }
    }
    else {
        ptr = calloc((size_t) nitems, (size_t) item_size);
        if(!ptr) {
            perror("[ERROR] VJ allocation failed");
            exit(EXIT_FAILURE);
        }
    }

    *requested_bytes = bytes;
    return ptr;
}

static void *
vj_malloc(uint64_t bytes, const char * msg)
{
    void * ptr;

    if(bytes > SIZE_MAX) {
        vj_die(msg);
    }

    ptr = malloc((size_t) bytes);
    if(!ptr) {
        perror("[ERROR] VJ allocation failed");
        exit(EXIT_FAILURE);
    }

    return ptr;
}

void
vector_join_set_payload_width(uint32_t payload_width)
{
    if(payload_width != 1 && payload_width != 2 && payload_width != 4) {
        fprintf(stderr,
                "[ERROR] VJ payload width must be one of 1, 2, or 4 bytes.\n");
        exit(EXIT_FAILURE);
    }

    vj_payload_width = payload_width;
}

uint32_t
vector_join_get_payload_width(void)
{
    return vj_payload_width;
}

void
vector_join_set_hugepage(int enabled)
{
    vj_use_hugepage = enabled ? 1 : 0;
}

int
vector_join_get_hugepage(void)
{
    return vj_use_hugepage;
}

static void
vj_die(const char * msg)
{
    fprintf(stderr, "[ERROR] VJ: %s\n", msg);
    exit(EXIT_FAILURE);
}

static void
vj_die_key(const char * msg, intkey_t key)
{
    fprintf(stderr, "[ERROR] VJ: %s key=%lld\n", msg, (long long) key);
    exit(EXIT_FAILURE);
}

static void
vj_die_payload(const char * msg, value_t payload, uint32_t payload_width)
{
    fprintf(stderr, "[ERROR] VJ: %s payload=%lld payload_width=%u\n",
            msg, (long long) payload, payload_width);
    exit(EXIT_FAILURE);
}

#ifndef NO_TIMING
static void
print_timing(uint64_t total, uint64_t build, uint64_t part,
             uint64_t numtuples, int64_t result,
             struct timeval * start, struct timeval * end)
{
    double diff_usec = (((*end).tv_sec*1000000L + (*end).tv_usec)
                        - ((*start).tv_sec*1000000L+(*start).tv_usec));
    double cyclestuple = total;
    cyclestuple /= numtuples;
    fprintf(stdout, "RUNTIME TOTAL, BUILD, PART (cycles): \n");
    fprintf(stderr, "%llu \t %llu \t %llu ",
            total, build, part);
    fprintf(stdout, "\n");
    fprintf(stdout, "TOTAL-TIME-USECS, TOTAL-TUPLES, CYCLES-PER-TUPLE: \n");
    fprintf(stdout, "%.4lf \t %llu \t ", diff_usec, result);
    fflush(stdout);
    fprintf(stderr, "%.4lf ", cyclestuple);
    fflush(stderr);
    fprintf(stdout, "\n");
}
#endif

static void
store_payload(vector_index_t * index, uint64_t slot, uint64_t payload)
{
    switch(index->payload_width) {
      case 1:
          ((uint8_t *) index->payloads)[slot] = (uint8_t) payload;
          break;
      case 2:
          ((uint16_t *) index->payloads)[slot] = (uint16_t) payload;
          break;
      case 4:
          ((uint32_t *) index->payloads)[slot] = (uint32_t) payload;
          break;
      default:
          vj_die("invalid payload width");
    }
}

static value_t
load_payload(const vector_index_t * index, uint64_t slot)
{
    switch(index->payload_width) {
      case 1:
          return (value_t) ((uint8_t *) index->payloads)[slot];
      case 2:
          return (value_t) ((uint16_t *) index->payloads)[slot];
      case 4:
          return (value_t) ((uint32_t *) index->payloads)[slot];
      default:
          vj_die("invalid payload width");
    }

    return 0;
}

static void
vector_index_init(vector_index_t * index, relation_t * relR)
{
    uint64_t i;
    intkey_t max_key = 0;
    uint32_t payload_width = vector_join_get_payload_width();

    memset(index, 0, sizeof(*index));
    index->payload_width = payload_width;

    for(i = 0; i < relR->num_tuples; i++) {
        intkey_t key = relR->tuples[i].key;
        if(key < 0) {
            vj_die_key("R key must be non-negative", key);
        }
        if(key > max_key) {
            max_key = key;
        }
    }

    index->nslots = (uint64_t) max_key + 1;
    if(index->nslots == 0) {
        vj_die("vector index size overflows addressable memory");
    }

    index->present = (uint8_t *) vj_calloc(index->nslots, sizeof(uint8_t),
                                           &index->present_bytes,
                                           "present vector size overflows addressable memory");
    index->payloads = vj_calloc(index->nslots, payload_width,
                                &index->payload_bytes,
                                "payload vector size overflows addressable memory");
    index->total_bytes = checked_add_bytes(index->payload_bytes,
                                           index->present_bytes,
                                           "vector index byte count overflows");

    for(i = 0; i < relR->num_tuples; i++) {
        intkey_t key = relR->tuples[i].key;
        value_t payload = relR->tuples[i].payload;
        uint64_t slot = (uint64_t) key;

        if(index->present[slot]) {
            vj_die_key("duplicate R key is not supported", key);
        }
        if(payload < 0) {
            vj_die_payload("R payload must be non-negative", payload,
                           payload_width);
        }

        /* Width 1/2/4 models compressed payload storage; the join only needs
         * the key lookup result, so payload truncation does not affect matches.
         */
        store_payload(index, slot, (uint64_t) payload);
        index->present[slot] = 1;
    }
}

static void
vector_index_destroy(vector_index_t * index)
{
    free(index->payloads);
    free(index->present);
    memset(index, 0, sizeof(*index));
}

static int64_t
probe_vector_index(const vector_index_t * index, relation_t * relS)
{
    uint64_t i;
    int64_t matches = 0;

    for(i = 0; i < relS->num_tuples; i++) {
        intkey_t key = relS->tuples[i].key;
        uint64_t slot;

        if(key < 0) {
            continue;
        }

        slot = (uint64_t) key;
        if(slot >= index->nslots) {
            continue;
        }

        if(index->present[slot]) {
            volatile value_t payload = load_payload(index, slot);
            (void) payload;
            matches++;
        }
    }

    return matches;
}

static void *
vj_thread(void * param)
{
    vj_arg_t * args = (vj_arg_t *) param;
    args->num_results = probe_vector_index(args->index, &args->relS);
    return 0;
}

result_t *
VJ(relation_t * relR, relation_t * relS, int nthreads)
{
    vector_index_t index;
    result_t * joinresult;
    vj_arg_t * args;
    pthread_t * tid;
    pthread_attr_t attr;
    cpu_set_t set;
    int64_t result = 0;
    int64_t numS;
    int64_t numSthr;
    int i;
    int rv;
    uint64_t result_bytes;
    uint64_t args_bytes;
    uint64_t tid_bytes;
    uint64_t helper_bytes;

#ifndef NO_TIMING
    struct timeval start, end;
    uint64_t timer1, timer2, timer3;
#endif

    if(nthreads <= 0) {
        vj_die("nthreads must be positive");
    }

    joinresult = (result_t *) vj_calloc(1, sizeof(result_t), &result_bytes,
                                        "result byte count overflows");
    args = (vj_arg_t *) vj_calloc((uint64_t) nthreads, sizeof(vj_arg_t),
                                  &args_bytes,
                                  "thread argument byte count overflows");
    tid_bytes = checked_mul_bytes((uint64_t) nthreads, sizeof(pthread_t),
                                  "thread id byte count overflows");
    tid = (pthread_t *) vj_malloc(tid_bytes,
                                  "thread id byte count overflows");

#ifndef NO_TIMING
    gettimeofday(&start, NULL);
    startTimer(&timer1);
    startTimer(&timer2);
    timer3 = 0;
#endif

    vector_index_init(&index, relR);

#ifndef NO_TIMING
    stopTimer(&timer2);
#endif

    numS = relS->num_tuples;
    numSthr = numS / nthreads;

    pthread_attr_init(&attr);
    for(i = 0; i < nthreads; i++) {
        int cpu_idx = get_cpu_id(i);

        CPU_ZERO(&set);
        CPU_SET(cpu_idx, &set);
        pthread_attr_setaffinity_np(&attr, sizeof(cpu_set_t), &set);

        args[i].tid = i;
        args[i].index = &index;
        args[i].relS.num_tuples = (i == (nthreads-1)) ? numS : numSthr;
        args[i].relS.tuples = relS->tuples + numSthr * i;
        numS -= numSthr;

        rv = pthread_create(&tid[i], &attr, vj_thread, (void *) &args[i]);
        if(rv) {
            fprintf(stderr,
                    "[ERROR] VJ pthread_create() return code is %d\n", rv);
            exit(EXIT_FAILURE);
        }
    }

    for(i = 0; i < nthreads; i++) {
        pthread_join(tid[i], NULL);
        result += args[i].num_results;
    }

#ifndef NO_TIMING
    stopTimer(&timer1);
    gettimeofday(&end, NULL);
    print_timing(timer1, timer2, timer3, relS->num_tuples, result,
                 &start, &end);
#endif

    joinresult->totalresults = result;
    joinresult->nthreads = nthreads;
    joinresult->has_vector_join_stats = 1;
    joinresult->payload_width_bytes = index.payload_width;
    joinresult->vector_index_bytes = index.payload_bytes;
    joinresult->vector_total_bytes = index.total_bytes;

    helper_bytes = checked_add_bytes(result_bytes, args_bytes,
                                     "VJ helper byte count overflows");
    helper_bytes = checked_add_bytes(helper_bytes, tid_bytes,
                                     "VJ helper byte count overflows");
    joinresult->total_extra_space_bytes =
        checked_add_bytes(index.total_bytes, helper_bytes,
                          "VJ total extra byte count overflows");

    vector_index_destroy(&index);
    free(args);
    free(tid);

    return joinresult;
}

static int
tuple_key_cmp(const void * lhs, const void * rhs)
{
    const tuple_t * a = (const tuple_t *) lhs;
    const tuple_t * b = (const tuple_t *) rhs;

    if(a->key < b->key) {
        return -1;
    }
    if(a->key > b->key) {
        return 1;
    }
    return 0;
}

result_t *
SORTMERGE(relation_t * relR, relation_t * relS, int nthreads)
{
    tuple_t * sortedR;
    tuple_t * sortedS;
    result_t * joinresult;
    uint64_t rbytes;
    uint64_t sbytes;
    uint64_t buffer_bytes;
    uint64_t i = 0;
    uint64_t j = 0;
    int64_t result = 0;

#ifndef NO_TIMING
    struct timeval start, end;
    uint64_t timer1, timer2, timer3;
#endif

    (void) nthreads;

    joinresult = (result_t *) vj_calloc(1, sizeof(result_t), &buffer_bytes,
                                        "sort-merge result byte count overflows");

    rbytes = checked_mul_bytes(relR->num_tuples, sizeof(tuple_t),
                               "sort-merge R copy byte count overflows");
    sbytes = checked_mul_bytes(relS->num_tuples, sizeof(tuple_t),
                               "sort-merge S copy byte count overflows");
    buffer_bytes = checked_add_bytes(rbytes, sbytes,
                                     "sort-merge buffer byte count overflows");

    sortedR = (tuple_t *) vj_malloc(rbytes, "sort-merge R copy byte count overflows");
    sortedS = (tuple_t *) vj_malloc(sbytes, "sort-merge S copy byte count overflows");

#ifndef NO_TIMING
    gettimeofday(&start, NULL);
    startTimer(&timer1);
    startTimer(&timer2);
    timer3 = 0;
#endif

    memcpy(sortedR, relR->tuples, (size_t) rbytes);
    memcpy(sortedS, relS->tuples, (size_t) sbytes);
    qsort(sortedR, (size_t) relR->num_tuples, sizeof(tuple_t), tuple_key_cmp);
    qsort(sortedS, (size_t) relS->num_tuples, sizeof(tuple_t), tuple_key_cmp);

#ifndef NO_TIMING
    stopTimer(&timer2);
#endif

    while(i < relR->num_tuples && j < relS->num_tuples) {
        if(sortedR[i].key < sortedS[j].key) {
            i++;
        }
        else if(sortedR[i].key > sortedS[j].key) {
            j++;
        }
        else {
            intkey_t key = sortedR[i].key;
            uint64_t rrun = 0;
            uint64_t srun = 0;

            while(i + rrun < relR->num_tuples && sortedR[i + rrun].key == key) {
                rrun++;
            }
            while(j + srun < relS->num_tuples && sortedS[j + srun].key == key) {
                srun++;
            }
            result += (int64_t) (rrun * srun);
            i += rrun;
            j += srun;
        }
    }

#ifndef NO_TIMING
    stopTimer(&timer1);
    gettimeofday(&end, NULL);
    print_timing(timer1, timer2, timer3, relS->num_tuples, result,
                 &start, &end);
#endif

    joinresult->totalresults = result;
    joinresult->nthreads = 1;
    joinresult->has_sortmerge_stats = 1;
    joinresult->sortmerge_buffer_bytes = buffer_bytes;
    joinresult->total_extra_space_bytes =
        checked_add_bytes(buffer_bytes, sizeof(result_t),
                          "sort-merge total extra byte count overflows");

    free(sortedR);
    free(sortedS);

    return joinresult;
}
