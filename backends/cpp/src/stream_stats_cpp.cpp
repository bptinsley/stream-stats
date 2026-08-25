#include "stream_stats_window.h"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <new>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
constexpr std::int32_t nil = -1;
thread_local std::string last_error = "no error";

struct window_error : std::runtime_error {
  stream_stats_ws_status status;
  window_error(stream_stats_ws_status value, const char *message)
      : std::runtime_error(message), status(value) {}
};

struct moments { std::uint64_t count; double mean; double m2; };

class WindowStatistics final {
 public:
  explicit WindowStatistics(std::size_t capacity)
      : capacity_(capacity), keys_(capacity), multiplicity_(capacity),
        left_(capacity, nil), right_(capacity, nil), heights_(capacity),
        counts_(capacity), sums_(capacity), means_(capacity), m2_(capacity),
        mins_(capacity), maxes_(capacity), ring_(capacity) {
    if (capacity == 0 || capacity > static_cast<std::size_t>(std::numeric_limits<std::int32_t>::max()))
      throw window_error(STREAM_STATS_WS_INVALID_ARGUMENT, "window size must be positive and representable");
    free_.reserve(capacity);
    for (std::size_t i = capacity; i-- > 0;) free_.push_back(static_cast<std::int32_t>(i));
  }
  WindowStatistics(const WindowStatistics &) = delete;
  WindowStatistics &operator=(const WindowStatistics &) = delete;
  WindowStatistics(WindowStatistics &&) noexcept = default;
  WindowStatistics &operator=(WindowStatistics &&) noexcept = default;

  std::optional<double> add(double value) {
    value = normalized(value);
    if (length_ < capacity_) {
      insert(value);
      ring_[(head_ + length_) % capacity_] = value;
      ++length_;
      return std::nullopt;
    }
    const double old = ring_[head_];
    erase(old);
    try { insert(value); }
    catch (...) { insert(old); throw; }
    ring_[head_] = value;
    head_ = (head_ + 1) % capacity_;
    return old;
  }
  double remove_oldest() {
    require_values();
    const double old = ring_[head_];
    erase(old);
    head_ = (head_ + 1) % capacity_;
    if (--length_ == 0) head_ = 0;
    return old;
  }
  void remove(double value) {
    value = normalized(value);
    std::size_t position = length_;
    for (std::size_t i = 0; i < length_; ++i)
      if (ring_[(head_ + i) % capacity_] == value) { position = i; break; }
    if (position == length_)
      throw window_error(STREAM_STATS_WS_VALUE_NOT_FOUND, "value is not present in window");
    erase(value);
    for (std::size_t i = position; i + 1 < length_; ++i)
      ring_[(head_ + i) % capacity_] = ring_[(head_ + i + 1) % capacity_];
    if (--length_ == 0) head_ = 0;
  }
  void clear() noexcept {
    std::fill(multiplicity_.begin(), multiplicity_.end(), 0);
    std::fill(left_.begin(), left_.end(), nil);
    std::fill(right_.begin(), right_.end(), nil);
    std::fill(heights_.begin(), heights_.end(), 0);
    std::fill(counts_.begin(), counts_.end(), 0);
    free_.clear();
    for (std::size_t i = capacity_; i-- > 0;) free_.push_back(static_cast<std::int32_t>(i));
    root_ = nil; head_ = 0; length_ = 0;
  }
  std::size_t count() const noexcept { return length_; }
  double sum() const noexcept { return root_ == nil ? 0.0 : sums_[root_]; }
  double min() const { require_values(); return mins_[root_]; }
  double max() const { require_values(); return maxes_[root_]; }
  double mean() const { require_values(); return means_[root_]; }
  double variance() const {
    require_values();
    double result = m2_[root_] / static_cast<double>(length_);
    if (result < 0.0 && result > -1e-15) result = 0.0;
    if (result < 0.0) throw window_error(STREAM_STATS_WS_INTERNAL_ERROR, "negative variance invariant failure");
    return result;
  }
  double percentile(double p) const {
    require_values();
    if (!std::isfinite(p) || p < 0.0 || p > 100.0)
      throw window_error(STREAM_STATS_WS_INVALID_ARGUMENT, "percentile must be in [0, 100]");
    const double rank = p / 100.0 * static_cast<double>(length_ - 1);
    const auto lo = static_cast<std::uint64_t>(std::floor(rank));
    const auto hi = static_cast<std::uint64_t>(std::ceil(rank));
    const double a = select(lo);
    if (lo == hi) return a;
    const double fraction = rank - static_cast<double>(lo);
    return a * (1.0 - fraction) + select(hi) * fraction;
  }
  double percentile_of(double value) const {
    require_values(); value = normalized(value);
    std::uint64_t less = 0, equal = 0; auto node = root_;
    while (node != nil) {
      if (value < keys_[node]) node = left_[node];
      else if (value > keys_[node]) { less += node_count(left_[node]) + multiplicity_[node]; node = right_[node]; }
      else { less += node_count(left_[node]); equal = multiplicity_[node]; break; }
    }
    return 100.0 * (static_cast<double>(less) + 0.5 * static_cast<double>(equal)) / static_cast<double>(length_);
  }
  void validate() const {
    std::vector<double> tree;
    tree.reserve(length_);
    validate_node(root_, std::nullopt, std::nullopt, tree);
    std::vector<double> ring;
    ring.reserve(length_);
    for (std::size_t i = 0; i < length_; ++i) ring.push_back(ring_[(head_ + i) % capacity_]);
    std::sort(ring.begin(), ring.end());
    if (tree != ring || free_.size() + distinct_count(root_) != capacity_)
      throw window_error(STREAM_STATS_WS_INTERNAL_ERROR, "C++ tree/ring partition invariant failure");
  }

 private:
  static double normalized(double value) {
    if (!std::isfinite(value)) throw window_error(STREAM_STATS_WS_INVALID_ARGUMENT, "value must be finite");
    return value == 0.0 ? 0.0 : value;
  }
  void require_values() const {
    if (length_ == 0) throw window_error(STREAM_STATS_WS_EMPTY_WINDOW, "empty window");
  }
  std::uint32_t height(std::int32_t n) const { return n == nil ? 0 : heights_[n]; }
  std::uint64_t node_count(std::int32_t n) const { return n == nil ? 0 : counts_[n]; }
  moments aggregate(std::int32_t n) const { return n == nil ? moments{0, 0.0, 0.0} : moments{counts_[n], means_[n], m2_[n]}; }
  static moments merge(moments a, moments b) {
    if (a.count == 0) return b; if (b.count == 0) return a;
    const auto count = a.count + b.count; const double total = static_cast<double>(count); const double delta = b.mean - a.mean;
    return {count, a.mean * (static_cast<double>(a.count) / total) + b.mean * (static_cast<double>(b.count) / total),
            a.m2 + b.m2 + delta * delta * static_cast<double>(a.count) * (static_cast<double>(b.count) / total)};
  }
  void pull(std::int32_t n) {
    const auto l = left_[n], r = right_[n]; const double key = keys_[n];
    const auto all = merge(merge(aggregate(l), {multiplicity_[n], key, 0.0}), aggregate(r));
    counts_[n] = all.count; means_[n] = all.mean; m2_[n] = all.m2;
    sums_[n] = (l == nil ? 0.0 : sums_[l]) + key * static_cast<double>(multiplicity_[n]) + (r == nil ? 0.0 : sums_[r]);
    mins_[n] = l == nil ? key : mins_[l]; maxes_[n] = r == nil ? key : maxes_[r];
    heights_[n] = 1 + std::max(height(l), height(r));
  }
  std::int32_t acquire(double key) {
    if (free_.empty()) throw window_error(STREAM_STATS_WS_INTERNAL_ERROR, "node pool exhausted");
    const auto n = free_.back(); free_.pop_back(); keys_[n] = key; multiplicity_[n] = counts_[n] = 1;
    left_[n] = right_[n] = nil; heights_[n] = 1; sums_[n] = means_[n] = mins_[n] = maxes_[n] = key; m2_[n] = 0.0; return n;
  }
  void release(std::int32_t n) { multiplicity_[n] = counts_[n] = heights_[n] = 0; left_[n] = right_[n] = nil; free_.push_back(n); }
  int balance(std::int32_t n) const { return static_cast<int>(height(left_[n])) - static_cast<int>(height(right_[n])); }
  std::int32_t rotate_left(std::int32_t n) { const auto p = right_[n], middle = left_[p]; left_[p] = n; right_[n] = middle; pull(n); pull(p); return p; }
  std::int32_t rotate_right(std::int32_t n) { const auto p = left_[n], middle = right_[p]; right_[p] = n; left_[n] = middle; pull(n); pull(p); return p; }
  std::int32_t rebalance(std::int32_t n) {
    pull(n); const int b = balance(n);
    if (b > 1) { if (balance(left_[n]) < 0) left_[n] = rotate_left(left_[n]); return rotate_right(n); }
    if (b < -1) { if (balance(right_[n]) > 0) right_[n] = rotate_right(right_[n]); return rotate_left(n); }
    return n;
  }
  std::int32_t insert_at(std::int32_t n, double key) {
    if (n == nil) return acquire(key);
    if (key < keys_[n]) left_[n] = insert_at(left_[n], key);
    else if (key > keys_[n]) right_[n] = insert_at(right_[n], key);
    else { ++multiplicity_[n]; pull(n); return n; }
    return rebalance(n);
  }
  std::int32_t minimum(std::int32_t n) const { while (left_[n] != nil) n = left_[n]; return n; }
  std::int32_t erase_at(std::int32_t n, double key, bool all = false) {
    if (n == nil) throw window_error(STREAM_STATS_WS_VALUE_NOT_FOUND, "value is not present in tree");
    if (key < keys_[n]) left_[n] = erase_at(left_[n], key, all);
    else if (key > keys_[n]) right_[n] = erase_at(right_[n], key, all);
    else if (multiplicity_[n] > 1 && !all) { --multiplicity_[n]; pull(n); return n; }
    else {
      if (left_[n] == nil || right_[n] == nil) { const auto replacement = left_[n] == nil ? right_[n] : left_[n]; release(n); return replacement; }
      const auto successor = minimum(right_[n]); keys_[n] = keys_[successor]; multiplicity_[n] = multiplicity_[successor]; right_[n] = erase_at(right_[n], keys_[n], true);
    }
    return rebalance(n);
  }
  void insert(double key) { root_ = insert_at(root_, key); }
  void erase(double key) { root_ = erase_at(root_, key); }
  double select(std::uint64_t rank) const {
    auto n = root_; while (n != nil) { const auto left_count = node_count(left_[n]); if (rank < left_count) n = left_[n]; else if (rank < left_count + multiplicity_[n]) return keys_[n]; else { rank -= left_count + multiplicity_[n]; n = right_[n]; } }
    throw window_error(STREAM_STATS_WS_INTERNAL_ERROR, "rank selection invariant failure");
  }
  std::size_t distinct_count(std::int32_t n) const { return n == nil ? 0 : 1 + distinct_count(left_[n]) + distinct_count(right_[n]); }
  std::pair<std::uint32_t, std::uint64_t> validate_node(std::int32_t n, std::optional<double> lower, std::optional<double> upper, std::vector<double> &values) const {
    if (n == nil) return {0, 0};
    if ((lower && keys_[n] <= *lower) || (upper && keys_[n] >= *upper) || multiplicity_[n] == 0) throw window_error(STREAM_STATS_WS_INTERNAL_ERROR, "C++ AVL ordering invariant failure");
    const auto l = validate_node(left_[n], lower, keys_[n], values); values.insert(values.end(), multiplicity_[n], keys_[n]); const auto r = validate_node(right_[n], keys_[n], upper, values);
    const auto h = 1 + std::max(l.first, r.first); const auto c = l.second + multiplicity_[n] + r.second;
    if (std::abs(static_cast<int>(l.first) - static_cast<int>(r.first)) > 1 || heights_[n] != h || counts_[n] != c) throw window_error(STREAM_STATS_WS_INTERNAL_ERROR, "C++ AVL metadata invariant failure");
    return {h, c};
  }

  std::size_t capacity_, head_ = 0, length_ = 0;
  std::vector<double> keys_, sums_, means_, m2_, mins_, maxes_, ring_;
  std::vector<std::uint64_t> multiplicity_, counts_;
  std::vector<std::int32_t> left_, right_, free_;
  std::vector<std::uint32_t> heights_;
  std::int32_t root_ = nil;
};

template <class F> stream_stats_ws_status guarded(F &&operation) noexcept {
  try { operation(); return STREAM_STATS_WS_OK; }
  catch (const window_error &error) { last_error = error.what(); return error.status; }
  catch (const std::bad_alloc &) { last_error = "C++ backend allocation failed"; return STREAM_STATS_WS_OUT_OF_MEMORY; }
  catch (const std::exception &error) { last_error = error.what(); return STREAM_STATS_WS_INTERNAL_ERROR; }
  catch (...) { last_error = "unknown C++ backend failure"; return STREAM_STATS_WS_INTERNAL_ERROR; }
}
WindowStatistics &state(stream_stats_ws_state *raw) { if (!raw) throw window_error(STREAM_STATS_WS_INVALID_ARGUMENT, "null state"); return *reinterpret_cast<WindowStatistics *>(raw); }
const WindowStatistics &state(const stream_stats_ws_state *raw) { if (!raw) throw window_error(STREAM_STATS_WS_INVALID_ARGUMENT, "null state"); return *reinterpret_cast<const WindowStatistics *>(raw); }
void snapshot(const WindowStatistics &window, stream_stats_ws_snapshot *out) {
  if (!out || out->abi_version != STREAM_STATS_WS_ABI_VERSION || out->struct_size < sizeof(*out)) throw window_error(STREAM_STATS_WS_INVALID_ARGUMENT, "incompatible snapshot structure");
  out->count = window.count(); out->sum = window.sum(); out->has_values = window.count() != 0; std::fill(std::begin(out->reserved), std::end(out->reserved), 0);
  if (out->has_values) { out->min = window.min(); out->max = window.max(); out->mean = window.mean(); out->variance = window.variance(); out->std = std::sqrt(out->variance); }
}
}

extern "C" {
STREAM_STATS_WS_API uint32_t stream_stats_ws_abi_version(void) { return STREAM_STATS_WS_ABI_VERSION; }
STREAM_STATS_WS_API const char *stream_stats_ws_last_error(void) { return last_error.c_str(); }
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_create(uint64_t size, stream_stats_ws_state **out) { return guarded([&] { if (!out || size > std::numeric_limits<std::size_t>::max()) throw window_error(STREAM_STATS_WS_INVALID_ARGUMENT, "invalid output or window size"); *out = reinterpret_cast<stream_stats_ws_state *>(new WindowStatistics(static_cast<std::size_t>(size))); }); }
STREAM_STATS_WS_API void stream_stats_ws_destroy(stream_stats_ws_state *raw) { delete reinterpret_cast<WindowStatistics *>(raw); }
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_clear(stream_stats_ws_state *raw) { return guarded([&] { state(raw).clear(); }); }
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_add(stream_stats_ws_state *raw, double value, uint8_t *did_evict, double *evicted) { return guarded([&] { if (!did_evict || !evicted) throw window_error(STREAM_STATS_WS_INVALID_ARGUMENT, "null output"); const auto result = state(raw).add(value); *did_evict = result.has_value(); *evicted = result.value_or(0.0); }); }
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_add_many(stream_stats_ws_state *raw, const double *values, uint64_t length, uint8_t *did_evict, double *evicted, uint64_t capacity, stream_stats_ws_snapshot *final) { return guarded([&] { if ((length && !values) || ((!did_evict || !evicted || capacity < length) && !(did_evict == nullptr && evicted == nullptr && capacity == 0))) throw window_error(STREAM_STATS_WS_INVALID_ARGUMENT, "invalid bulk buffers"); for (uint64_t i = 0; i < length; ++i) if (!std::isfinite(values[i])) throw window_error(STREAM_STATS_WS_INVALID_ARGUMENT, "value must be finite"); auto &window = state(raw); for (uint64_t i = 0; i < length; ++i) { const auto result = window.add(values[i]); if (did_evict) { did_evict[i] = result.has_value(); evicted[i] = result.value_or(0.0); } } if (final) snapshot(window, final); }); }
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_remove_oldest(stream_stats_ws_state *raw, double *removed) { return guarded([&] { if (!removed) throw window_error(STREAM_STATS_WS_INVALID_ARGUMENT, "null output"); *removed = state(raw).remove_oldest(); }); }
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_remove_value(stream_stats_ws_state *raw, double value) { return guarded([&] { state(raw).remove(value); }); }
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_snapshot_get(const stream_stats_ws_state *raw, stream_stats_ws_snapshot *out) { return guarded([&] { snapshot(state(raw), out); }); }
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_count(const stream_stats_ws_state *raw, uint64_t *out) { return guarded([&] { if (!out) throw window_error(STREAM_STATS_WS_INVALID_ARGUMENT, "null output"); *out = state(raw).count(); }); }
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_sum(const stream_stats_ws_state *raw, double *out) { return guarded([&] { if (!out) throw window_error(STREAM_STATS_WS_INVALID_ARGUMENT, "null output"); *out = state(raw).sum(); }); }
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_min(const stream_stats_ws_state *raw, double *out) { return guarded([&] { if (!out) throw window_error(STREAM_STATS_WS_INVALID_ARGUMENT, "null output"); *out = state(raw).min(); }); }
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_max(const stream_stats_ws_state *raw, double *out) { return guarded([&] { if (!out) throw window_error(STREAM_STATS_WS_INVALID_ARGUMENT, "null output"); *out = state(raw).max(); }); }
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_percentile(const stream_stats_ws_state *raw, double p, double *out) { return guarded([&] { if (!out) throw window_error(STREAM_STATS_WS_INVALID_ARGUMENT, "null output"); *out = state(raw).percentile(p); }); }
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_percentile_of(const stream_stats_ws_state *raw, double value, double *out) { return guarded([&] { if (!out) throw window_error(STREAM_STATS_WS_INVALID_ARGUMENT, "null output"); *out = state(raw).percentile_of(value); }); }
STREAM_STATS_WS_API stream_stats_ws_status stream_stats_ws_validate(const stream_stats_ws_state *raw) { return guarded([&] { state(raw).validate(); }); }
}
