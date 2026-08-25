#include "stream_stats_window.h"

#include <assert.h>
#include <math.h>
#include <stddef.h>

int main(void) {
    stream_stats_ws_state *state = NULL;
    assert(stream_stats_ws_abi_version() == STREAM_STATS_WS_ABI_VERSION);
    assert(stream_stats_ws_create(3, &state) == STREAM_STATS_WS_OK);
    uint8_t did_evict;
    double evicted;
    assert(stream_stats_ws_add(state, 1.0, &did_evict, &evicted) == STREAM_STATS_WS_OK);
    assert(!did_evict);
    assert(stream_stats_ws_add(state, 2.0, &did_evict, &evicted) == STREAM_STATS_WS_OK);
    assert(stream_stats_ws_add(state, 3.0, &did_evict, &evicted) == STREAM_STATS_WS_OK);
    stream_stats_ws_snapshot snapshot = {
        .abi_version = STREAM_STATS_WS_ABI_VERSION,
        .struct_size = sizeof(stream_stats_ws_snapshot),
    };
    assert(stream_stats_ws_snapshot_get(state, &snapshot) == STREAM_STATS_WS_OK);
    assert(snapshot.count == 3 && snapshot.sum == 6.0 && snapshot.mean == 2.0);
    assert(fabs(snapshot.variance - 2.0 / 3.0) < 1e-12);
    assert(stream_stats_ws_add(state, 4.0, &did_evict, &evicted) == STREAM_STATS_WS_OK);
    assert(did_evict && evicted == 1.0);
    double percentile;
    assert(stream_stats_ws_percentile(state, 50.0, &percentile) == STREAM_STATS_WS_OK);
    assert(percentile == 3.0);
    assert(stream_stats_ws_validate(state) == STREAM_STATS_WS_OK);
    stream_stats_ws_destroy(state);
    return 0;
}
