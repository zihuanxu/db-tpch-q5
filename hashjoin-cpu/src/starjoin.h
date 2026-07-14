/**
 * @file    starjoin.h
 * @brief   Star-join interfaces for npo/pro/vj modes.
 */

#ifndef STARJOIN_H
#define STARJOIN_H

#include <stdint.h>

#include "types.h"

typedef enum starjoin_mode_t {
    STARJOIN_NONE = 0,
    STARJOIN_NPO,
    STARJOIN_PRO,
    STARJOIN_VJ
} starjoin_mode_t;

typedef struct starjoin_params_t starjoin_params_t;
typedef struct starjoin_input_t starjoin_input_t;
typedef struct starjoin_result_t starjoin_result_t;

struct starjoin_params_t {
    starjoin_mode_t mode;
    uint32_t nthreads;
    uint32_t sf;
    uint32_t payload_width;
    int basic_numa;
    uint32_t prefetch_distance;
    int pro_order;
};

struct starjoin_input_t {
    relation_t lineorder;
    relation_t orders;
    relation_t partsupp;
};

struct starjoin_result_t {
    uint64_t match_count;
    int64_t aggregate_sum;
    uint64_t input_bytes;
    uint64_t aux_bytes;
    uint64_t total_bytes;
};

int
starjoin_generate(starjoin_input_t *input, const starjoin_params_t *params);

void
starjoin_free(starjoin_input_t *input);

int
starjoin_run(starjoin_result_t *result, const starjoin_input_t *input,
             const starjoin_params_t *params);

const char *
starjoin_mode_name(starjoin_mode_t mode);

#endif /* STARJOIN_H */
