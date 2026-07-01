/**
 * @file    vector_join.h
 * @brief   Interface for Vector Join (VJ).
 */

#ifndef VECTOR_JOIN_H
#define VECTOR_JOIN_H

#include <stdint.h>

#include "types.h"

void
vector_join_set_payload_width(uint32_t payload_width);

uint32_t
vector_join_get_payload_width(void);

void
vector_join_set_hugepage(int enabled);

int
vector_join_get_hugepage(void);

result_t *
VJ(relation_t *relR, relation_t *relS, int nthreads);

result_t *
SORTMERGE(relation_t *relR, relation_t *relS, int nthreads);

#endif /* VECTOR_JOIN_H */
