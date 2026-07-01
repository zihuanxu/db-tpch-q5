/**
 * @file    starjoin.c
 * @brief   Minimal star-join implementations for npo/pro/vj modes.
 */

#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>

#include "starjoin.h"
#include "affinity.h"
#include "cpu_mapping.h"
#include "generator.h"
#include "rdtsc.h"

typedef struct starjoin_hash_entry_t starjoin_hash_entry_t;
typedef struct starjoin_hash_table_t starjoin_hash_table_t;
typedef struct starjoin_vector_t starjoin_vector_t;
typedef struct starjoin_thread_arg_t starjoin_thread_arg_t;

struct starjoin_hash_entry_t {
    intkey_t key;
    value_t payload;
    uint8_t present;
};

struct starjoin_hash_table_t {
    starjoin_hash_entry_t *entries;
    uint64_t nbuckets;
    uint64_t mask;
    uint64_t bytes;
};

struct starjoin_vector_t {
    uint8_t *present;
    void *payloads;
    uint64_t nslots;
    uint32_t payload_width;
    uint64_t present_bytes;
    uint64_t payload_bytes;
    uint64_t total_bytes;
};

struct starjoin_thread_arg_t {
    int tid;
    const relation_t *lineorder;
    uint64_t start_idx;
    uint64_t end_idx;
    const starjoin_hash_table_t *orders_hash;
    const starjoin_hash_table_t *partsupp_hash;
    const starjoin_vector_t *orders_vector;
    const starjoin_vector_t *partsupp_vector;
    uint64_t match_count;
    int64_t aggregate_sum;
    relation_t *temp_relation;
    uint64_t temp_offset;
    uint64_t temp_count;
    uint32_t prefetch_distance;
};

extern int numalocalize;
extern int nthreads;

static uint64_t
starjoin_next_pow2(uint64_t value)
{
    if(value <= 1) {
        return 1;
    }

    value--;
    value |= value >> 1;
    value |= value >> 2;
    value |= value >> 4;
    value |= value >> 8;
    value |= value >> 16;
    value |= value >> 32;
    value++;
    return value;
}

static uint64_t
starjoin_tuple_bytes(void)
{
    return sizeof(tuple_t);
}

static void *
starjoin_alloc_relation_bytes(uint64_t ntuples)
{
    size_t bytes = (size_t) (ntuples * sizeof(tuple_t));
    void *ptr = NULL;
    if(posix_memalign(&ptr, 64, bytes) != 0 || !ptr) {
        perror("[ERROR] starjoin relation allocation failed");
        exit(EXIT_FAILURE);
    }
    if(numalocalize && ntuples > 0) {
        numa_localize((tuple_t *) ptr, (int64_t) ntuples, (uint32_t) nthreads);
    }
    return ptr;
}

static void
starjoin_store_payload(void *payloads, uint32_t payload_width,
                       uint64_t slot, value_t payload)
{
    switch(payload_width) {
      case 1:
          ((uint8_t *) payloads)[slot] = (uint8_t) payload;
          break;
      case 2:
          ((uint16_t *) payloads)[slot] = (uint16_t) payload;
          break;
      case 4:
          ((uint32_t *) payloads)[slot] = (uint32_t) payload;
          break;
      default:
          fprintf(stderr, "[ERROR] Unsupported vector payload width %u\n",
                  payload_width);
          exit(EXIT_FAILURE);
    }
}

static value_t
starjoin_load_payload(const void *payloads, uint32_t payload_width, uint64_t slot)
{
    switch(payload_width) {
      case 1:
          return (value_t) ((const uint8_t *) payloads)[slot];
      case 2:
          return (value_t) ((const uint16_t *) payloads)[slot];
      case 4:
          return (value_t) ((const uint32_t *) payloads)[slot];
      default:
          fprintf(stderr, "[ERROR] Unsupported vector payload width %u\n",
                  payload_width);
          exit(EXIT_FAILURE);
    }
}

static uint64_t
starjoin_hash_key(intkey_t key, uint64_t mask)
{
    uint64_t x = (uint64_t) (uint32_t) key;
    x *= 11400714819323198485ull;
    return x & mask;
}

static void
starjoin_hash_init(starjoin_hash_table_t *table, const relation_t *rel)
{
    uint64_t i;
    uint64_t nbuckets = starjoin_next_pow2(rel->num_tuples * 2);

    table->entries = calloc((size_t) nbuckets, sizeof(starjoin_hash_entry_t));
    if(!table->entries) {
        perror("[ERROR] starjoin hash allocation failed");
        exit(EXIT_FAILURE);
    }
    table->nbuckets = nbuckets;
    table->mask = nbuckets - 1;
    table->bytes = nbuckets * sizeof(starjoin_hash_entry_t);

    for(i = 0; i < rel->num_tuples; i++) {
        intkey_t key = rel->tuples[i].key;
        uint64_t slot = starjoin_hash_key(key, table->mask);
        while(table->entries[slot].present) {
            if(table->entries[slot].key == key) {
                fprintf(stderr, "[ERROR] duplicate dimension key %d\n", key);
                exit(EXIT_FAILURE);
            }
            slot = (slot + 1) & table->mask;
        }
        table->entries[slot].present = 1;
        table->entries[slot].key = key;
        table->entries[slot].payload = rel->tuples[i].payload;
    }
}

static void
starjoin_hash_destroy(starjoin_hash_table_t *table)
{
    free(table->entries);
    memset(table, 0, sizeof(*table));
}

static int
starjoin_hash_probe(const starjoin_hash_table_t *table, intkey_t key, value_t *payload)
{
    uint64_t slot = starjoin_hash_key(key, table->mask);
    while(table->entries[slot].present) {
        if(table->entries[slot].key == key) {
            *payload = table->entries[slot].payload;
            return 1;
        }
        slot = (slot + 1) & table->mask;
    }
    return 0;
}

static void
starjoin_vector_init(starjoin_vector_t *vector, const relation_t *rel, uint32_t payload_width)
{
    uint64_t i;
    intkey_t max_key = 0;

    memset(vector, 0, sizeof(*vector));
    vector->payload_width = payload_width;

    for(i = 0; i < rel->num_tuples; i++) {
        if(rel->tuples[i].key > max_key) {
            max_key = rel->tuples[i].key;
        }
    }

    vector->nslots = (uint64_t) max_key + 1;
    vector->present = calloc((size_t) vector->nslots, sizeof(uint8_t));
    vector->payloads = calloc((size_t) vector->nslots, (size_t) payload_width);
    if(!vector->present || !vector->payloads) {
        perror("[ERROR] starjoin vector allocation failed");
        exit(EXIT_FAILURE);
    }
    vector->present_bytes = vector->nslots * sizeof(uint8_t);
    vector->payload_bytes = vector->nslots * payload_width;
    vector->total_bytes = vector->present_bytes + vector->payload_bytes;

    for(i = 0; i < rel->num_tuples; i++) {
        uint64_t slot = (uint64_t) rel->tuples[i].key;
        if(vector->present[slot]) {
            fprintf(stderr, "[ERROR] duplicate dimension key %d\n",
                    rel->tuples[i].key);
            exit(EXIT_FAILURE);
        }
        vector->present[slot] = 1;
        starjoin_store_payload(vector->payloads, payload_width, slot,
                               rel->tuples[i].payload);
    }
}

static void
starjoin_vector_destroy(starjoin_vector_t *vector)
{
    free(vector->present);
    free(vector->payloads);
    memset(vector, 0, sizeof(*vector));
}

static int
starjoin_vector_probe(const starjoin_vector_t *vector, intkey_t key, value_t *payload)
{
    uint64_t slot;
    if(key < 0) {
        return 0;
    }
    slot = (uint64_t) key;
    if(slot >= vector->nslots || !vector->present[slot]) {
        return 0;
    }
    *payload = starjoin_load_payload(vector->payloads, vector->payload_width, slot);
    return 1;
}

static void
starjoin_fill_dimension(relation_t *rel, uint64_t ntuples, value_t payload)
{
    uint64_t i;
    rel->num_tuples = ntuples;
    rel->tuples = starjoin_alloc_relation_bytes(ntuples);
    for(i = 0; i < ntuples; i++) {
        rel->tuples[i].key = (intkey_t) (i + 1);
        rel->tuples[i].payload = payload;
    }
}

static void
starjoin_shuffle_relation_keys(relation_t *rel)
{
    uint64_t i;
    if(rel->num_tuples < 2) {
        return;
    }

    for(i = rel->num_tuples - 1; i > 0; i--) {
        uint64_t j = (uint64_t) rand() % (i + 1);
        intkey_t tmp = rel->tuples[i].key;
        rel->tuples[i].key = rel->tuples[j].key;
        rel->tuples[j].key = tmp;
    }
}

static void
starjoin_shuffle_relation_payloads(relation_t *rel)
{
    uint64_t i;
    if(rel->num_tuples < 2) {
        return;
    }

    for(i = rel->num_tuples - 1; i > 0; i--) {
        uint64_t j = (uint64_t) rand() % (i + 1);
        value_t tmp = rel->tuples[i].payload;
        rel->tuples[i].payload = rel->tuples[j].payload;
        rel->tuples[j].payload = tmp;
    }
}

static void
starjoin_fill_lineorder(relation_t *rel, uint64_t ntuples,
                        uint64_t orders_size, uint64_t partsupp_size)
{
    uint64_t i;

    rel->num_tuples = ntuples;
    rel->tuples = starjoin_alloc_relation_bytes(ntuples);

    for(i = 0; i < ntuples; i++) {
        rel->tuples[i].key = (intkey_t) ((i % orders_size) + 1);
        rel->tuples[i].payload = (value_t) ((i % partsupp_size) + 1);
    }
    starjoin_shuffle_relation_keys(rel);
    starjoin_shuffle_relation_payloads(rel);
}

static void *
starjoin_pipeline_hash_thread(void *param)
{
    uint64_t i;
    starjoin_thread_arg_t *arg = (starjoin_thread_arg_t *) param;
    uint64_t match_count = 0;
    int64_t aggregate_sum = 0;
    uint32_t prefetch_distance = arg->prefetch_distance;

    for(i = arg->start_idx; i < arg->end_idx; i++) {
        tuple_t tuple = arg->lineorder->tuples[i];
        value_t order_payload;
        value_t partsupp_payload;
        if(prefetch_distance > 0 && i + prefetch_distance < arg->end_idx) {
            tuple_t next = arg->lineorder->tuples[i + prefetch_distance];
            uint64_t order_slot = starjoin_hash_key(next.key, arg->orders_hash->mask);
            uint64_t partsupp_slot = starjoin_hash_key(next.payload, arg->partsupp_hash->mask);
            __builtin_prefetch(&arg->orders_hash->entries[order_slot], 0, 1);
            __builtin_prefetch(&arg->partsupp_hash->entries[partsupp_slot], 0, 1);
        }
        if(!starjoin_hash_probe(arg->orders_hash, tuple.key, &order_payload)) {
            continue;
        }
        if(!starjoin_hash_probe(arg->partsupp_hash, tuple.payload, &partsupp_payload)) {
            continue;
        }
        aggregate_sum += (int64_t) order_payload
                         - ((int64_t) 1 - (int64_t) partsupp_payload);
        match_count++;
    }

    arg->match_count = match_count;
    arg->aggregate_sum = aggregate_sum;
    return NULL;
}

static void *
starjoin_pipeline_vector_thread(void *param)
{
    uint64_t i;
    starjoin_thread_arg_t *arg = (starjoin_thread_arg_t *) param;
    uint64_t match_count = 0;
    int64_t aggregate_sum = 0;
    uint32_t prefetch_distance = arg->prefetch_distance;

    for(i = arg->start_idx; i < arg->end_idx; i++) {
        tuple_t tuple = arg->lineorder->tuples[i];
        value_t order_payload;
        value_t partsupp_payload;
        if(prefetch_distance > 0 && i + prefetch_distance < arg->end_idx) {
            tuple_t next = arg->lineorder->tuples[i + prefetch_distance];
            if(next.key >= 0 && (uint64_t) next.key < arg->orders_vector->nslots) {
                uint64_t slot = (uint64_t) next.key;
                __builtin_prefetch(&arg->orders_vector->present[slot], 0, 1);
                __builtin_prefetch(((const char *) arg->orders_vector->payloads)
                                   + slot * arg->orders_vector->payload_width, 0, 1);
            }
            if(next.payload >= 0 && (uint64_t) next.payload < arg->partsupp_vector->nslots) {
                uint64_t slot = (uint64_t) next.payload;
                __builtin_prefetch(&arg->partsupp_vector->present[slot], 0, 1);
                __builtin_prefetch(((const char *) arg->partsupp_vector->payloads)
                                   + slot * arg->partsupp_vector->payload_width, 0, 1);
            }
        }
        if(!starjoin_vector_probe(arg->orders_vector, tuple.key, &order_payload)) {
            continue;
        }
        if(!starjoin_vector_probe(arg->partsupp_vector, tuple.payload, &partsupp_payload)) {
            continue;
        }
        aggregate_sum += (int64_t) order_payload
                         - ((int64_t) 1 - (int64_t) partsupp_payload);
        match_count++;
    }

    arg->match_count = match_count;
    arg->aggregate_sum = aggregate_sum;
    return NULL;
}

static void *
starjoin_count_temp_thread(void *param)
{
    uint64_t i;
    starjoin_thread_arg_t *arg = (starjoin_thread_arg_t *) param;
    uint64_t temp_count = 0;
    for(i = arg->start_idx; i < arg->end_idx; i++) {
        value_t partsupp_payload;
        if(starjoin_hash_probe(arg->partsupp_hash, arg->lineorder->tuples[i].payload,
                               &partsupp_payload)) {
            temp_count++;
        }
    }
    arg->temp_count = temp_count;
    return NULL;
}

static void *
starjoin_count_temp_orders_thread(void *param)
{
    uint64_t i;
    starjoin_thread_arg_t *arg = (starjoin_thread_arg_t *) param;
    uint64_t temp_count = 0;
    for(i = arg->start_idx; i < arg->end_idx; i++) {
        value_t order_payload;
        if(starjoin_hash_probe(arg->orders_hash, arg->lineorder->tuples[i].key,
                               &order_payload)) {
            temp_count++;
        }
    }
    arg->temp_count = temp_count;
    return NULL;
}

static void *
starjoin_materialize_temp_thread(void *param)
{
    uint64_t i;
    uint64_t offset;
    starjoin_thread_arg_t *arg = (starjoin_thread_arg_t *) param;
    relation_t *temp = arg->temp_relation;

    offset = arg->temp_offset;
    for(i = arg->start_idx; i < arg->end_idx; i++) {
        tuple_t tuple = arg->lineorder->tuples[i];
        value_t partsupp_payload;
        if(starjoin_hash_probe(arg->partsupp_hash, tuple.payload, &partsupp_payload)) {
            temp->tuples[offset].key = tuple.key;
            temp->tuples[offset].payload = (value_t) (1 - partsupp_payload);
            offset++;
        }
    }
    return NULL;
}

static void *
starjoin_materialize_temp_orders_thread(void *param)
{
    uint64_t i;
    uint64_t offset;
    starjoin_thread_arg_t *arg = (starjoin_thread_arg_t *) param;
    relation_t *temp = arg->temp_relation;

    offset = arg->temp_offset;
    for(i = arg->start_idx; i < arg->end_idx; i++) {
        tuple_t tuple = arg->lineorder->tuples[i];
        value_t order_payload;
        if(starjoin_hash_probe(arg->orders_hash, tuple.key, &order_payload)) {
            temp->tuples[offset].key = tuple.payload;
            temp->tuples[offset].payload = (value_t) (order_payload - 1);
            offset++;
        }
    }
    return NULL;
}

static void *
starjoin_probe_temp_thread(void *param)
{
    uint64_t i;
    starjoin_thread_arg_t *arg = (starjoin_thread_arg_t *) param;
    uint64_t match_count = 0;
    int64_t aggregate_sum = 0;
    relation_t *temp = arg->temp_relation;

    for(i = arg->start_idx; i < arg->end_idx; i++) {
        tuple_t tuple = temp->tuples[i];
        value_t order_payload;
        if(starjoin_hash_probe(arg->orders_hash, tuple.key, &order_payload)) {
            aggregate_sum += (int64_t) order_payload - (int64_t) tuple.payload;
            match_count++;
        }
    }

    arg->match_count = match_count;
    arg->aggregate_sum = aggregate_sum;
    return NULL;
}

static void *
starjoin_probe_temp_partsupp_thread(void *param)
{
    uint64_t i;
    starjoin_thread_arg_t *arg = (starjoin_thread_arg_t *) param;
    uint64_t match_count = 0;
    int64_t aggregate_sum = 0;
    relation_t *temp = arg->temp_relation;

    for(i = arg->start_idx; i < arg->end_idx; i++) {
        tuple_t tuple = temp->tuples[i];
        value_t partsupp_payload;
        if(starjoin_hash_probe(arg->partsupp_hash, tuple.key, &partsupp_payload)) {
            aggregate_sum += (int64_t) tuple.payload + (int64_t) partsupp_payload;
            match_count++;
        }
    }

    arg->match_count = match_count;
    arg->aggregate_sum = aggregate_sum;
    return NULL;
}

static void
starjoin_run_threads(void *(*thread_fn)(void *), starjoin_thread_arg_t *args,
                     uint32_t nthreads)
{
    uint32_t i;
    pthread_t *threads = malloc(sizeof(pthread_t) * nthreads);
    pthread_attr_t attr;
    cpu_set_t set;

    if(!threads) {
        perror("[ERROR] starjoin thread allocation failed");
        exit(EXIT_FAILURE);
    }

    pthread_attr_init(&attr);
    for(i = 0; i < nthreads; i++) {
        int cpu_idx = get_cpu_id((int) i);
        CPU_ZERO(&set);
        CPU_SET(cpu_idx, &set);
        pthread_attr_setaffinity_np(&attr, sizeof(cpu_set_t), &set);
        if(pthread_create(&threads[i], &attr, thread_fn, &args[i]) != 0) {
            perror("[ERROR] starjoin pthread_create failed");
            exit(EXIT_FAILURE);
        }
    }

    for(i = 0; i < nthreads; i++) {
        pthread_join(threads[i], NULL);
    }

    pthread_attr_destroy(&attr);
    free(threads);
}

static void
starjoin_reduce(starjoin_result_t *result, starjoin_thread_arg_t *args, uint32_t nthreads)
{
    uint32_t i;
    result->match_count = 0;
    result->aggregate_sum = 0;
    for(i = 0; i < nthreads; i++) {
        result->match_count += args[i].match_count;
        result->aggregate_sum += args[i].aggregate_sum;
    }
}

static int
starjoin_run_npo(starjoin_result_t *result, const starjoin_input_t *input,
                 const starjoin_params_t *params)
{
    uint32_t i;
    uint64_t chunk;
    starjoin_hash_table_t orders_hash;
    starjoin_hash_table_t partsupp_hash;
    starjoin_thread_arg_t *args;

    starjoin_hash_init(&orders_hash, &input->orders);
    starjoin_hash_init(&partsupp_hash, &input->partsupp);

    args = calloc((size_t) params->nthreads, sizeof(starjoin_thread_arg_t));
    if(!args) {
        perror("[ERROR] starjoin npo args allocation failed");
        exit(EXIT_FAILURE);
    }

    chunk = input->lineorder.num_tuples / params->nthreads;
    for(i = 0; i < params->nthreads; i++) {
        uint64_t start = i * chunk;
        uint64_t end = (i == params->nthreads - 1)
                     ? input->lineorder.num_tuples
                     : start + chunk;
        args[i].tid = (int) i;
        args[i].lineorder = &input->lineorder;
        args[i].start_idx = start;
        args[i].end_idx = end;
        args[i].orders_hash = &orders_hash;
        args[i].partsupp_hash = &partsupp_hash;
        args[i].prefetch_distance = params->prefetch_distance;
    }

    starjoin_run_threads(starjoin_pipeline_hash_thread, args, params->nthreads);
    starjoin_reduce(result, args, params->nthreads);
    result->aux_bytes = orders_hash.bytes + partsupp_hash.bytes;
    result->total_bytes = result->input_bytes + result->aux_bytes;

    free(args);
    starjoin_hash_destroy(&orders_hash);
    starjoin_hash_destroy(&partsupp_hash);
    return 0;
}

static int
starjoin_run_vj(starjoin_result_t *result, const starjoin_input_t *input,
                const starjoin_params_t *params)
{
    uint32_t i;
    uint64_t chunk;
    starjoin_vector_t orders_vector;
    starjoin_vector_t partsupp_vector;
    starjoin_thread_arg_t *args;

    starjoin_vector_init(&orders_vector, &input->orders, params->payload_width);
    starjoin_vector_init(&partsupp_vector, &input->partsupp, params->payload_width);

    args = calloc((size_t) params->nthreads, sizeof(starjoin_thread_arg_t));
    if(!args) {
        perror("[ERROR] starjoin vj args allocation failed");
        exit(EXIT_FAILURE);
    }

    chunk = input->lineorder.num_tuples / params->nthreads;
    for(i = 0; i < params->nthreads; i++) {
        uint64_t start = i * chunk;
        uint64_t end = (i == params->nthreads - 1)
                     ? input->lineorder.num_tuples
                     : start + chunk;
        args[i].tid = (int) i;
        args[i].lineorder = &input->lineorder;
        args[i].start_idx = start;
        args[i].end_idx = end;
        args[i].orders_vector = &orders_vector;
        args[i].partsupp_vector = &partsupp_vector;
        args[i].prefetch_distance = params->prefetch_distance;
    }

    starjoin_run_threads(starjoin_pipeline_vector_thread, args, params->nthreads);
    starjoin_reduce(result, args, params->nthreads);
    result->aux_bytes = orders_vector.total_bytes + partsupp_vector.total_bytes;
    result->total_bytes = result->input_bytes + result->aux_bytes;

    free(args);
    starjoin_vector_destroy(&orders_vector);
    starjoin_vector_destroy(&partsupp_vector);
    return 0;
}

static int
starjoin_run_pro(starjoin_result_t *result, const starjoin_input_t *input,
                 const starjoin_params_t *params)
{
    uint32_t i;
    uint64_t temp_count = 0;
    uint64_t chunk;
    relation_t temp;
    starjoin_thread_arg_t *args;
    starjoin_hash_table_t partsupp_hash;
    starjoin_hash_table_t orders_hash;

    starjoin_hash_init(&partsupp_hash, &input->partsupp);
    starjoin_hash_init(&orders_hash, &input->orders);

    args = calloc((size_t) params->nthreads, sizeof(starjoin_thread_arg_t));
    if(!args) {
        perror("[ERROR] starjoin pro args allocation failed");
        exit(EXIT_FAILURE);
    }

    chunk = input->lineorder.num_tuples / params->nthreads;
    for(i = 0; i < params->nthreads; i++) {
        uint64_t start = i * chunk;
        uint64_t end = (i == params->nthreads - 1)
                     ? input->lineorder.num_tuples
                     : start + chunk;
        args[i].lineorder = &input->lineorder;
        args[i].start_idx = start;
        args[i].end_idx = end;
        args[i].partsupp_hash = &partsupp_hash;
        args[i].orders_hash = &orders_hash;
    }
    starjoin_run_threads(params->pro_order == 1
                         ? starjoin_count_temp_orders_thread
                         : starjoin_count_temp_thread,
                         args, params->nthreads);
    for(i = 0; i < params->nthreads; i++) {
        args[i].temp_offset = temp_count;
        temp_count += args[i].temp_count;
    }

    temp.num_tuples = temp_count;
    temp.tuples = starjoin_alloc_relation_bytes(temp_count);

    for(i = 0; i < params->nthreads; i++) {
        args[i].temp_relation = &temp;
    }
    starjoin_run_threads(params->pro_order == 1
                         ? starjoin_materialize_temp_orders_thread
                         : starjoin_materialize_temp_thread,
                         args, params->nthreads);

    chunk = temp.num_tuples / params->nthreads;
    for(i = 0; i < params->nthreads; i++) {
        uint64_t start = i * chunk;
        uint64_t end = (i == params->nthreads - 1) ? temp.num_tuples : start + chunk;
        args[i].start_idx = start;
        args[i].end_idx = end;
        args[i].orders_hash = &orders_hash;
        args[i].partsupp_hash = &partsupp_hash;
        args[i].match_count = 0;
        args[i].aggregate_sum = 0;
    }
    starjoin_run_threads(params->pro_order == 1
                         ? starjoin_probe_temp_partsupp_thread
                         : starjoin_probe_temp_thread,
                         args, params->nthreads);
    starjoin_reduce(result, args, params->nthreads);
    result->aux_bytes = temp.num_tuples * sizeof(tuple_t)
                      + partsupp_hash.bytes
                      + orders_hash.bytes;
    result->total_bytes = result->input_bytes + result->aux_bytes;

    free(args);
    starjoin_hash_destroy(&orders_hash);
    starjoin_hash_destroy(&partsupp_hash);
    free(temp.tuples);
    return 0;
}

int
starjoin_generate(starjoin_input_t *input, const starjoin_params_t *params)
{
    uint64_t sf = params->sf;
    uint64_t lineorder_size = sf * 6000000ull;
    uint64_t orders_size = sf * 1500000ull;
    uint64_t partsupp_size = sf * 800000ull;

    memset(input, 0, sizeof(*input));
    starjoin_fill_dimension(&input->orders, orders_size, 3);
    starjoin_fill_dimension(&input->partsupp, partsupp_size, 1);
    starjoin_fill_lineorder(&input->lineorder, lineorder_size, orders_size, partsupp_size);
    return 0;
}

void
starjoin_free(starjoin_input_t *input)
{
    if(!input) {
        return;
    }
    free(input->lineorder.tuples);
    free(input->orders.tuples);
    free(input->partsupp.tuples);
    memset(input, 0, sizeof(*input));
}

int
starjoin_run(starjoin_result_t *result, const starjoin_input_t *input,
             const starjoin_params_t *params)
{
#ifndef NO_TIMING
    struct timeval start, end;
    uint64_t timer;
    double diff_usec;
    double cycles_per_tuple = 0.0;
#endif
    result->input_bytes = (input->lineorder.num_tuples
                          + input->orders.num_tuples
                          + input->partsupp.num_tuples) * starjoin_tuple_bytes();
    result->aux_bytes = 0;
    result->total_bytes = result->input_bytes;

#ifndef NO_TIMING
    gettimeofday(&start, NULL);
    startTimer(&timer);
#endif

    switch(params->mode) {
      case STARJOIN_NPO:
          if(starjoin_run_npo(result, input, params) != 0) {
              return -1;
          }
          break;
      case STARJOIN_PRO:
          if(starjoin_run_pro(result, input, params) != 0) {
              return -1;
          }
          break;
      case STARJOIN_VJ:
          if(starjoin_run_vj(result, input, params) != 0) {
              return -1;
          }
          break;
      default:
          fprintf(stderr, "[ERROR] Unsupported starjoin mode\n");
          return -1;
    }

#ifndef NO_TIMING
    stopTimer(&timer);
    gettimeofday(&end, NULL);
    diff_usec = (((end.tv_sec * 1000000L) + end.tv_usec)
                 - ((start.tv_sec * 1000000L) + start.tv_usec));
    if(result->match_count > 0) {
        cycles_per_tuple = (double) timer / (double) result->match_count;
    }
    fprintf(stdout, "RUNTIME TOTAL, BUILD, PART (cycles): \n");
    fprintf(stderr, "%llu \t %llu \t %d ",
            (unsigned long long) timer,
            (unsigned long long) timer,
            0);
    fprintf(stdout, "\n");
    fprintf(stdout, "TOTAL-TIME-USECS, TOTAL-TUPLES, CYCLES-PER-TUPLE: \n");
    fprintf(stdout, "%.4lf \t %llu \t ", diff_usec,
            (unsigned long long) result->match_count);
    fflush(stdout);
    fprintf(stderr, "%.4lf ", cycles_per_tuple);
    fflush(stderr);
    fprintf(stdout, "\n");
#endif

    return 0;
}

const char *
starjoin_mode_name(starjoin_mode_t mode)
{
    switch(mode) {
      case STARJOIN_NPO:
          return "npo";
      case STARJOIN_PRO:
          return "pro";
      case STARJOIN_VJ:
          return "vj";
      default:
          return "none";
    }
}
