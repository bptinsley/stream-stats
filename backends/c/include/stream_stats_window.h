#ifndef STREAM_STATS_WINDOW_H
#define STREAM_STATS_WINDOW_H

#include <stdint.h>

#if defined(_WIN32)
#define STREAM_STATS_WS_API __declspec(dllexport)
#else
#define STREAM_STATS_WS_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define STREAM_STATS_WS_ABI_VERSION ((uint32_t)1)

typedef struct stream_stats_ws_state stream_stats_ws_state;
typedef int32_t stream_stats_ws_status;

#define STREAM_STATS_WS_OK ((stream_stats_ws_status)0)
#define STREAM_STATS_WS_INVALID_ARGUMENT ((stream_stats_ws_status)1)
#define STREAM_STATS_WS_EMPTY_WINDOW ((stream_stats_ws_status)2)
#define STREAM_STATS_WS_VALUE_NOT_FOUND ((stream_stats_ws_status)3)
#define STREAM_STATS_WS_OUT_OF_MEMORY ((stream_stats_ws_status)4)
#define STREAM_STATS_WS_INTERNAL_ERROR ((stream_stats_ws_status)5)
#define STREAM_STATS_WS_CLOSED ((stream_stats_ws_status)6)

typedef struct {
    uint32_t abi_version;
    uint32_t struct_size;
    uint64_t count;
    double sum;
    double min;
    double max;
    double mean;
    double variance;
    double std;
    uint8_t has_values;
    uint8_t reserved[7];
} stream_stats_ws_snapshot;

STREAM_STATS_WS_API uint32_t stream_stats_ws_abi_version(void);
STREAM_STATS_WS_API const char *stream_stats_ws_last_error(void);
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_create(
    uint64_t window_size,
    stream_stats_ws_state **out
);
STREAM_STATS_WS_API void stream_stats_ws_destroy(stream_stats_ws_state *state);
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_clear(
    stream_stats_ws_state *state
);
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_add(
    stream_stats_ws_state *state,
    double value,
    uint8_t *did_evict,
    double *evicted
);
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_add_many(
    stream_stats_ws_state *state,
    const double *values,
    uint64_t length,
    uint8_t *did_evict,
    double *evicted,
    uint64_t eviction_capacity,
    stream_stats_ws_snapshot *final_snapshot
);
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_remove_oldest(
    stream_stats_ws_state *state,
    double *removed
);
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_remove_value(
    stream_stats_ws_state *state,
    double value
);
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_snapshot_get(
    const stream_stats_ws_state *state,
    stream_stats_ws_snapshot *out
);
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_count(
    const stream_stats_ws_state *state,
    uint64_t *out
);
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_sum(
    const stream_stats_ws_state *state,
    double *out
);
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_min(
    const stream_stats_ws_state *state,
    double *out
);
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_max(
    const stream_stats_ws_state *state,
    double *out
);
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_percentile(
    const stream_stats_ws_state *state,
    double percentile,
    double *out
);
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_percentile_of(
    const stream_stats_ws_state *state,
    double value,
    double *out
);
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_validate(
    const stream_stats_ws_state *state
);

#ifdef __cplusplus
}
#endif

#endif
