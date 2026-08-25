#include "stream_stats_window.h"

#include <float.h>
#include <limits.h>
#include <math.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define WS_NULL ((int32_t)-1)

struct stream_stats_ws_state {
    uint64_t capacity;
    double *keys;
    uint64_t *multiplicities;
    int32_t *left;
    int32_t *right;
    uint32_t *heights;
    uint64_t *subtree_counts;
    double *subtree_sums;
    double *subtree_means;
    double *subtree_m2;
    double *subtree_mins;
    double *subtree_maxes;
    int32_t *free_indexes;
    double *ring;
    uint64_t free_length;
    uint64_t distinct_count;
    int32_t root;
    uint64_t head;
    uint64_t length;
};

typedef struct {
    uint64_t count;
    double mean;
    double m2;
} ws_moments;

static _Thread_local char ws_error[256];

static stream_stats_ws_status ws_fail(stream_stats_ws_status status, const char *message) {
    snprintf(ws_error, sizeof(ws_error), "%s", message);
    return status;
}

static void *ws_calloc(uint64_t count, size_t size) {
    if (count == 0 || count > SIZE_MAX / size) {
        return NULL;
    }
    return calloc((size_t)count, size);
}

static uint32_t ws_height(const stream_stats_ws_state *s, int32_t i) {
    return i == WS_NULL ? 0U : s->heights[i];
}

static uint64_t ws_count(const stream_stats_ws_state *s, int32_t i) {
    return i == WS_NULL ? 0U : s->subtree_counts[i];
}

static ws_moments ws_aggregate(const stream_stats_ws_state *s, int32_t i) {
    ws_moments result = {0U, 0.0, 0.0};
    if (i != WS_NULL) {
        result.count = s->subtree_counts[i];
        result.mean = s->subtree_means[i];
        result.m2 = s->subtree_m2[i];
    }
    return result;
}

static ws_moments ws_merge(ws_moments a, ws_moments b) {
    if (a.count == 0U) return b;
    if (b.count == 0U) return a;
    ws_moments result;
    result.count = a.count + b.count;
    double total = (double)result.count;
    double delta = b.mean - a.mean;
    result.mean = a.mean * ((double)a.count / total) +
                  b.mean * ((double)b.count / total);
    result.m2 = a.m2 + b.m2 +
                delta * delta * (double)a.count * ((double)b.count / total);
    return result;
}

static void ws_pull(stream_stats_ws_state *s, int32_t i) {
    int32_t left = s->left[i];
    int32_t right = s->right[i];
    uint64_t multiplicity = s->multiplicities[i];
    double key = s->keys[i];
    ws_moments own = {multiplicity, key, 0.0};
    ws_moments moments = ws_merge(ws_merge(ws_aggregate(s, left), own),
                                  ws_aggregate(s, right));
    s->subtree_counts[i] = moments.count;
    s->subtree_means[i] = moments.mean;
    s->subtree_m2[i] = moments.m2;
    s->subtree_sums[i] = (left == WS_NULL ? 0.0 : s->subtree_sums[left]) +
                         key * (double)multiplicity +
                         (right == WS_NULL ? 0.0 : s->subtree_sums[right]);
    s->subtree_mins[i] = left == WS_NULL ? key : s->subtree_mins[left];
    s->subtree_maxes[i] = right == WS_NULL ? key : s->subtree_maxes[right];
    uint32_t lh = ws_height(s, left);
    uint32_t rh = ws_height(s, right);
    s->heights[i] = 1U + (lh > rh ? lh : rh);
}

static int32_t ws_acquire(stream_stats_ws_state *s, double key) {
    if (s->free_length == 0U) return WS_NULL;
    int32_t i = s->free_indexes[--s->free_length];
    s->keys[i] = key;
    s->multiplicities[i] = 1U;
    s->left[i] = WS_NULL;
    s->right[i] = WS_NULL;
    s->heights[i] = 1U;
    s->subtree_counts[i] = 1U;
    s->subtree_sums[i] = key;
    s->subtree_means[i] = key;
    s->subtree_m2[i] = 0.0;
    s->subtree_mins[i] = key;
    s->subtree_maxes[i] = key;
    s->distinct_count++;
    return i;
}

static void ws_release(stream_stats_ws_state *s, int32_t i) {
    s->keys[i] = 0.0;
    s->multiplicities[i] = 0U;
    s->left[i] = WS_NULL;
    s->right[i] = WS_NULL;
    s->heights[i] = 0U;
    s->subtree_counts[i] = 0U;
    s->subtree_sums[i] = 0.0;
    s->subtree_means[i] = 0.0;
    s->subtree_m2[i] = 0.0;
    s->subtree_mins[i] = 0.0;
    s->subtree_maxes[i] = 0.0;
    s->free_indexes[s->free_length++] = i;
    s->distinct_count--;
}

static int ws_balance(const stream_stats_ws_state *s, int32_t i) {
    return (int)ws_height(s, s->left[i]) - (int)ws_height(s, s->right[i]);
}

static int32_t ws_rotate_left(stream_stats_ws_state *s, int32_t i) {
    int32_t promoted = s->right[i];
    int32_t middle = s->left[promoted];
    s->left[promoted] = i;
    s->right[i] = middle;
    ws_pull(s, i);
    ws_pull(s, promoted);
    return promoted;
}

static int32_t ws_rotate_right(stream_stats_ws_state *s, int32_t i) {
    int32_t promoted = s->left[i];
    int32_t middle = s->right[promoted];
    s->right[promoted] = i;
    s->left[i] = middle;
    ws_pull(s, i);
    ws_pull(s, promoted);
    return promoted;
}

static int32_t ws_rebalance(stream_stats_ws_state *s, int32_t i) {
    ws_pull(s, i);
    int balance = ws_balance(s, i);
    if (balance > 1) {
        int32_t left = s->left[i];
        if (ws_balance(s, left) < 0) s->left[i] = ws_rotate_left(s, left);
        return ws_rotate_right(s, i);
    }
    if (balance < -1) {
        int32_t right = s->right[i];
        if (ws_balance(s, right) > 0) s->right[i] = ws_rotate_right(s, right);
        return ws_rotate_left(s, i);
    }
    return i;
}

static int32_t ws_insert(stream_stats_ws_state *s, int32_t i, double key,
                         stream_stats_ws_status *status) {
    if (i == WS_NULL) {
        int32_t acquired = ws_acquire(s, key);
        if (acquired == WS_NULL) *status = STREAM_STATS_WS_INTERNAL_ERROR;
        return acquired;
    }
    if (key < s->keys[i]) {
        s->left[i] = ws_insert(s, s->left[i], key, status);
    } else if (key > s->keys[i]) {
        s->right[i] = ws_insert(s, s->right[i], key, status);
    } else {
        s->multiplicities[i]++;
        ws_pull(s, i);
        return i;
    }
    return *status == STREAM_STATS_WS_OK ? ws_rebalance(s, i) : i;
}

static int32_t ws_minimum_index(const stream_stats_ws_state *s, int32_t i) {
    while (s->left[i] != WS_NULL) i = s->left[i];
    return i;
}

static int32_t ws_delete(stream_stats_ws_state *s, int32_t i, double key,
                         int all_occurrences, stream_stats_ws_status *status) {
    if (i == WS_NULL) {
        *status = STREAM_STATS_WS_VALUE_NOT_FOUND;
        return i;
    }
    if (key < s->keys[i]) {
        s->left[i] = ws_delete(s, s->left[i], key, all_occurrences, status);
    } else if (key > s->keys[i]) {
        s->right[i] = ws_delete(s, s->right[i], key, all_occurrences, status);
    } else if (s->multiplicities[i] > 1U && !all_occurrences) {
        s->multiplicities[i]--;
        ws_pull(s, i);
        return i;
    } else {
        int32_t left = s->left[i];
        int32_t right = s->right[i];
        if (left == WS_NULL || right == WS_NULL) {
            int32_t replacement = left == WS_NULL ? right : left;
            ws_release(s, i);
            return replacement;
        }
        int32_t successor = ws_minimum_index(s, right);
        double successor_key = s->keys[successor];
        s->keys[i] = successor_key;
        s->multiplicities[i] = s->multiplicities[successor];
        s->right[i] = ws_delete(s, right, successor_key, 1, status);
    }
    return *status == STREAM_STATS_WS_OK ? ws_rebalance(s, i) : i;
}

static double ws_select(const stream_stats_ws_state *s, uint64_t rank) {
    int32_t i = s->root;
    while (i != WS_NULL) {
        uint64_t left_count = ws_count(s, s->left[i]);
        if (rank < left_count) {
            i = s->left[i];
        } else if (rank < left_count + s->multiplicities[i]) {
            return s->keys[i];
        } else {
            rank -= left_count + s->multiplicities[i];
            i = s->right[i];
        }
    }
    return NAN;
}

static void ws_free_state(stream_stats_ws_state *s) {
    if (!s) return;
    free(s->keys); free(s->multiplicities); free(s->left); free(s->right);
    free(s->heights); free(s->subtree_counts); free(s->subtree_sums);
    free(s->subtree_means); free(s->subtree_m2); free(s->subtree_mins);
    free(s->subtree_maxes); free(s->free_indexes); free(s->ring); free(s);
}

uint32_t stream_stats_ws_abi_version(void) { return STREAM_STATS_WS_ABI_VERSION; }
const char *stream_stats_ws_last_error(void) { return ws_error; }

stream_stats_ws_status stream_stats_ws_create(uint64_t capacity,
                                               stream_stats_ws_state **out) {
    if (!out || capacity == 0U || capacity > INT32_MAX) {
        return ws_fail(STREAM_STATS_WS_INVALID_ARGUMENT, "invalid window size");
    }
    *out = NULL;
    stream_stats_ws_state *s = calloc(1, sizeof(*s));
    if (!s) return ws_fail(STREAM_STATS_WS_OUT_OF_MEMORY, "state allocation failed");
    s->capacity = capacity;
#define WS_ALLOC(field, type) s->field = ws_calloc(capacity, sizeof(type))
    WS_ALLOC(keys, double); WS_ALLOC(multiplicities, uint64_t);
    WS_ALLOC(left, int32_t); WS_ALLOC(right, int32_t); WS_ALLOC(heights, uint32_t);
    WS_ALLOC(subtree_counts, uint64_t); WS_ALLOC(subtree_sums, double);
    WS_ALLOC(subtree_means, double); WS_ALLOC(subtree_m2, double);
    WS_ALLOC(subtree_mins, double); WS_ALLOC(subtree_maxes, double);
    WS_ALLOC(free_indexes, int32_t); WS_ALLOC(ring, double);
#undef WS_ALLOC
    if (!s->keys || !s->multiplicities || !s->left || !s->right || !s->heights ||
        !s->subtree_counts || !s->subtree_sums || !s->subtree_means ||
        !s->subtree_m2 || !s->subtree_mins || !s->subtree_maxes ||
        !s->free_indexes || !s->ring) {
        ws_free_state(s);
        return ws_fail(STREAM_STATS_WS_OUT_OF_MEMORY, "array allocation failed");
    }
    for (uint64_t i = 0; i < capacity; i++) {
        s->left[i] = WS_NULL;
        s->right[i] = WS_NULL;
        s->free_indexes[i] = (int32_t)(capacity - 1U - i);
    }
    s->free_length = capacity;
    s->root = WS_NULL;
    *out = s;
    return STREAM_STATS_WS_OK;
}

void stream_stats_ws_destroy(stream_stats_ws_state *s) { ws_free_state(s); }

stream_stats_ws_status stream_stats_ws_clear(stream_stats_ws_state *s) {
    if (!s) return ws_fail(STREAM_STATS_WS_INVALID_ARGUMENT, "null state");
    for (uint64_t i = 0; i < s->capacity; i++) {
        s->keys[i] = 0.0; s->multiplicities[i] = 0U;
        s->left[i] = WS_NULL; s->right[i] = WS_NULL; s->heights[i] = 0U;
        s->subtree_counts[i] = 0U; s->subtree_sums[i] = 0.0;
        s->subtree_means[i] = 0.0; s->subtree_m2[i] = 0.0;
        s->subtree_mins[i] = 0.0; s->subtree_maxes[i] = 0.0;
        s->free_indexes[i] = (int32_t)(s->capacity - 1U - i);
    }
    s->free_length = s->capacity; s->distinct_count = 0U; s->root = WS_NULL;
    s->head = 0U; s->length = 0U;
    return STREAM_STATS_WS_OK;
}

stream_stats_ws_status stream_stats_ws_add(stream_stats_ws_state *s, double value,
                                            uint8_t *did_evict, double *evicted) {
    if (!s || !did_evict || !evicted || !isfinite(value))
        return ws_fail(STREAM_STATS_WS_INVALID_ARGUMENT, "invalid add argument");
    if (value == 0.0) value = 0.0;
    *did_evict = 0U; *evicted = 0.0;
    if (s->length < s->capacity) {
        stream_stats_ws_status status = STREAM_STATS_WS_OK;
        s->root = ws_insert(s, s->root, value, &status);
        if (status != STREAM_STATS_WS_OK) return status;
        uint64_t position = (s->head + s->length) % s->capacity;
        s->ring[position] = value; s->length++;
        return STREAM_STATS_WS_OK;
    }
    double outgoing = s->ring[s->head];
    stream_stats_ws_status status = STREAM_STATS_WS_OK;
    s->root = ws_delete(s, s->root, outgoing, 0, &status);
    if (status != STREAM_STATS_WS_OK) return ws_fail(status, "eviction removal failed");
    s->root = ws_insert(s, s->root, value, &status);
    if (status != STREAM_STATS_WS_OK) {
        stream_stats_ws_status rollback = STREAM_STATS_WS_OK;
        s->root = ws_insert(s, s->root, outgoing, &rollback);
        return ws_fail(status, "insertion failed");
    }
    s->ring[s->head] = value;
    s->head = (s->head + 1U) % s->capacity;
    *did_evict = 1U; *evicted = outgoing;
    return STREAM_STATS_WS_OK;
}

stream_stats_ws_status stream_stats_ws_remove_oldest(stream_stats_ws_state *s,
                                                      double *removed) {
    if (!s || !removed) return ws_fail(STREAM_STATS_WS_INVALID_ARGUMENT, "invalid remove argument");
    if (s->length == 0U) return ws_fail(STREAM_STATS_WS_EMPTY_WINDOW, "empty window");
    double outgoing = s->ring[s->head];
    stream_stats_ws_status status = STREAM_STATS_WS_OK;
    s->root = ws_delete(s, s->root, outgoing, 0, &status);
    if (status != STREAM_STATS_WS_OK) return ws_fail(status, "tree removal failed");
    s->head = (s->head + 1U) % s->capacity;
    s->length--;
    if (s->length == 0U) s->head = 0U;
    *removed = outgoing;
    return STREAM_STATS_WS_OK;
}

stream_stats_ws_status stream_stats_ws_remove_value(stream_stats_ws_state *s,
                                                     double value) {
    if (!s || !isfinite(value)) return ws_fail(STREAM_STATS_WS_INVALID_ARGUMENT, "invalid remove value");
    if (value == 0.0) value = 0.0;
    uint64_t match = UINT64_MAX;
    for (uint64_t logical = 0; logical < s->length; logical++) {
        uint64_t position = (s->head + logical) % s->capacity;
        if (s->ring[position] == value) { match = logical; break; }
    }
    if (match == UINT64_MAX) return ws_fail(STREAM_STATS_WS_VALUE_NOT_FOUND, "value not found");
    stream_stats_ws_status status = STREAM_STATS_WS_OK;
    s->root = ws_delete(s, s->root, value, 0, &status);
    if (status != STREAM_STATS_WS_OK) return ws_fail(status, "tree removal failed");
    for (uint64_t logical = match; logical + 1U < s->length; logical++) {
        uint64_t target = (s->head + logical) % s->capacity;
        uint64_t source = (s->head + logical + 1U) % s->capacity;
        s->ring[target] = s->ring[source];
    }
    s->length--;
    if (s->length == 0U) s->head = 0U;
    return STREAM_STATS_WS_OK;
}

stream_stats_ws_status stream_stats_ws_snapshot_get(const stream_stats_ws_state *s,
                                                      stream_stats_ws_snapshot *out) {
    if (!s || !out || out->abi_version != STREAM_STATS_WS_ABI_VERSION ||
        out->struct_size < sizeof(stream_stats_ws_snapshot))
        return ws_fail(STREAM_STATS_WS_INVALID_ARGUMENT, "invalid snapshot structure");
    uint32_t requested_size = out->struct_size;
    stream_stats_ws_snapshot result;
    memset(&result, 0, sizeof(result));
    result.abi_version = STREAM_STATS_WS_ABI_VERSION;
    result.struct_size = sizeof(result);
    result.count = s->length;
    result.sum = s->root == WS_NULL ? 0.0 : s->subtree_sums[s->root];
    if (s->length > 0U) {
        result.has_values = 1U;
        result.min = s->subtree_mins[s->root]; result.max = s->subtree_maxes[s->root];
        result.mean = s->subtree_means[s->root];
        result.variance = s->subtree_m2[s->root] / (double)s->length;
        if (result.variance < 0.0 && result.variance > -DBL_EPSILON) result.variance = 0.0;
        if (result.variance < 0.0) return ws_fail(STREAM_STATS_WS_INTERNAL_ERROR, "negative variance");
        result.std = sqrt(result.variance);
    }
    memcpy(out, &result, requested_size < sizeof(result) ? requested_size : sizeof(result));
    return STREAM_STATS_WS_OK;
}

stream_stats_ws_status stream_stats_ws_count(const stream_stats_ws_state *s,
                                              uint64_t *out) {
    if (!s || !out) return ws_fail(STREAM_STATS_WS_INVALID_ARGUMENT, "invalid count output");
    *out = s->length;
    return STREAM_STATS_WS_OK;
}

stream_stats_ws_status stream_stats_ws_sum(const stream_stats_ws_state *s,
                                            double *out) {
    if (!s || !out) return ws_fail(STREAM_STATS_WS_INVALID_ARGUMENT, "invalid sum output");
    *out = s->root == WS_NULL ? 0.0 : s->subtree_sums[s->root];
    return STREAM_STATS_WS_OK;
}

stream_stats_ws_status stream_stats_ws_min(const stream_stats_ws_state *s,
                                            double *out) {
    if (!s || !out) return ws_fail(STREAM_STATS_WS_INVALID_ARGUMENT, "invalid min output");
    if (s->length == 0U) return ws_fail(STREAM_STATS_WS_EMPTY_WINDOW, "empty window");
    *out = s->subtree_mins[s->root];
    return STREAM_STATS_WS_OK;
}

stream_stats_ws_status stream_stats_ws_max(const stream_stats_ws_state *s,
                                            double *out) {
    if (!s || !out) return ws_fail(STREAM_STATS_WS_INVALID_ARGUMENT, "invalid max output");
    if (s->length == 0U) return ws_fail(STREAM_STATS_WS_EMPTY_WINDOW, "empty window");
    *out = s->subtree_maxes[s->root];
    return STREAM_STATS_WS_OK;
}

stream_stats_ws_status stream_stats_ws_add_many(stream_stats_ws_state *s,
                                                 const double *values, uint64_t length,
                                                 uint8_t *did_evict, double *evicted,
                                                 uint64_t eviction_capacity,
                                                 stream_stats_ws_snapshot *final_snapshot) {
    if (!s || (length > 0U && !values)) return ws_fail(STREAM_STATS_WS_INVALID_ARGUMENT, "invalid batch");
    if ((did_evict == NULL) != (evicted == NULL) ||
        (did_evict && eviction_capacity < length))
        return ws_fail(STREAM_STATS_WS_INVALID_ARGUMENT, "invalid eviction buffers");
    for (uint64_t i = 0; i < length; i++) if (!isfinite(values[i]))
        return ws_fail(STREAM_STATS_WS_INVALID_ARGUMENT, "batch contains non-finite value");
    for (uint64_t i = 0; i < length; i++) {
        uint8_t flag; double outgoing;
        stream_stats_ws_status status = stream_stats_ws_add(s, values[i], &flag, &outgoing);
        if (status != STREAM_STATS_WS_OK) return status;
        if (did_evict) { did_evict[i] = flag; evicted[i] = outgoing; }
    }
    if (final_snapshot) return stream_stats_ws_snapshot_get(s, final_snapshot);
    return STREAM_STATS_WS_OK;
}

stream_stats_ws_status stream_stats_ws_percentile(const stream_stats_ws_state *s,
                                                   double percentile, double *out) {
    if (!s || !out || !isfinite(percentile) || percentile < 0.0 || percentile > 100.0)
        return ws_fail(STREAM_STATS_WS_INVALID_ARGUMENT, "invalid percentile");
    if (s->length == 0U) return ws_fail(STREAM_STATS_WS_EMPTY_WINDOW, "empty window");
    double rank = percentile / 100.0 * (double)(s->length - 1U);
    uint64_t lower_rank = (uint64_t)floor(rank);
    uint64_t upper_rank = (uint64_t)ceil(rank);
    double lower = ws_select(s, lower_rank);
    if (lower_rank == upper_rank) { *out = lower; return STREAM_STATS_WS_OK; }
    double upper = ws_select(s, upper_rank);
    double fraction = rank - (double)lower_rank;
    *out = lower * (1.0 - fraction) + upper * fraction;
    return STREAM_STATS_WS_OK;
}

stream_stats_ws_status stream_stats_ws_percentile_of(const stream_stats_ws_state *s,
                                                      double value, double *out) {
    if (!s || !out || !isfinite(value)) return ws_fail(STREAM_STATS_WS_INVALID_ARGUMENT, "invalid value");
    if (s->length == 0U) return ws_fail(STREAM_STATS_WS_EMPTY_WINDOW, "empty window");
    if (value == 0.0) value = 0.0;
    uint64_t less = 0U, equal = 0U;
    int32_t i = s->root;
    while (i != WS_NULL) {
        if (value < s->keys[i]) i = s->left[i];
        else if (value > s->keys[i]) {
            less += ws_count(s, s->left[i]) + s->multiplicities[i];
            i = s->right[i];
        } else {
            less += ws_count(s, s->left[i]); equal = s->multiplicities[i]; break;
        }
    }
    *out = 100.0 * ((double)less + 0.5 * (double)equal) / (double)s->length;
    return STREAM_STATS_WS_OK;
}

static int ws_validate_node(const stream_stats_ws_state *s, int32_t i,
                            double lower, double upper, uint64_t *count) {
    if (i == WS_NULL) { *count = 0U; return 1; }
    if (i < 0 || (uint64_t)i >= s->capacity || !(s->keys[i] > lower) || !(s->keys[i] < upper) ||
        s->multiplicities[i] == 0U) return 0;
    uint64_t lc, rc;
    if (!ws_validate_node(s, s->left[i], lower, s->keys[i], &lc) ||
        !ws_validate_node(s, s->right[i], s->keys[i], upper, &rc)) return 0;
    int balance = (int)ws_height(s, s->left[i]) - (int)ws_height(s, s->right[i]);
    if (balance < -1 || balance > 1) return 0;
    *count = lc + s->multiplicities[i] + rc;
    return *count == s->subtree_counts[i];
}

stream_stats_ws_status stream_stats_ws_validate(const stream_stats_ws_state *s) {
    if (!s) return ws_fail(STREAM_STATS_WS_INVALID_ARGUMENT, "null state");
    uint64_t count;
    if (!ws_validate_node(s, s->root, -INFINITY, INFINITY, &count) || count != s->length ||
        s->free_length + s->distinct_count != s->capacity)
        return ws_fail(STREAM_STATS_WS_INTERNAL_ERROR, "state invariant failed");
    return STREAM_STATS_WS_OK;
}
