#include "stream_stats_window.h"
#include <cassert>
#include <cmath>

int main() {
  stream_stats_ws_state *state = nullptr;
  assert(stream_stats_ws_create(3, &state) == STREAM_STATS_WS_OK);
  const double values[] = {1.0, 2.0, 3.0, 4.0};
  uint8_t flags[4]{}; double evicted[4]{};
  stream_stats_ws_snapshot snapshot{};
  snapshot.abi_version = STREAM_STATS_WS_ABI_VERSION;
  snapshot.struct_size = sizeof(snapshot);
  assert(stream_stats_ws_add_many(state, values, 4, flags, evicted, 4, &snapshot) == STREAM_STATS_WS_OK);
  assert(!flags[0] && flags[3] && evicted[3] == 1.0);
  assert(snapshot.count == 3 && snapshot.sum == 9.0);
  assert(std::abs(snapshot.variance - 2.0 / 3.0) < 1e-12);
  assert(stream_stats_ws_validate(state) == STREAM_STATS_WS_OK);
  stream_stats_ws_destroy(state);
}
