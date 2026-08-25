from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from statistics import StatisticsError

from ._models import WindowStatisticsSnapshot


def _aggregate_sum(values: tuple[float, ...]) -> float:
    try:
        return math.fsum(values)
    except OverflowError:
        return sum(values)


def _positive_window_size(window_size: int) -> int:
    if (
        isinstance(window_size, bool)
        or not isinstance(window_size, int)
        or window_size <= 0
    ):
        raise ValueError("window_size must be a positive integer")
    return window_size


def _finite_float(value: float) -> float:
    if isinstance(value, bool):
        raise TypeError("values must be real numbers, not bool")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError("values must be real numbers") from exc
    if not math.isfinite(number):
        raise ValueError("values must be finite numbers")
    return 0.0 if number == 0.0 else number


class _IndexedAVL:
    """Fixed-capacity AVL multiset backed by recycled parallel-array slots."""

    _NULL = -1

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.keys = [0.0] * capacity
        self.multiplicities = [0] * capacity
        self.left = [self._NULL] * capacity
        self.right = [self._NULL] * capacity
        self.heights = [0] * capacity
        self.subtree_counts = [0] * capacity
        self.subtree_sums = [0.0] * capacity
        self.subtree_means = [0.0] * capacity
        self.subtree_m2 = [0.0] * capacity
        self.subtree_mins = [0.0] * capacity
        self.subtree_maxes = [0.0] * capacity
        self.free_indexes = list(range(capacity - 1, -1, -1))
        self.root = self._NULL
        self.distinct_count = 0

    def _height(self, index: int) -> int:
        return 0 if index == self._NULL else self.heights[index]

    def _count(self, index: int) -> int:
        return 0 if index == self._NULL else self.subtree_counts[index]

    def _aggregate(self, index: int) -> tuple[int, float, float]:
        if index == self._NULL:
            return 0, 0.0, 0.0
        return (
            self.subtree_counts[index],
            self.subtree_means[index],
            self.subtree_m2[index],
        )

    @staticmethod
    def _merge_moments(
        first: tuple[int, float, float], second: tuple[int, float, float]
    ) -> tuple[int, float, float]:
        first_count, first_mean, first_m2 = first
        second_count, second_mean, second_m2 = second
        if first_count == 0:
            return second
        if second_count == 0:
            return first
        count = first_count + second_count
        delta = second_mean - first_mean
        mean = _aggregate_sum(
            (
                first_mean * (first_count / count),
                second_mean * (second_count / count),
            )
        )
        m2 = (
            first_m2
            + second_m2
            + delta * delta * first_count * second_count / count
        )
        return count, mean, m2

    def _pull(self, index: int) -> None:
        left = self.left[index]
        right = self.right[index]
        multiplicity = self.multiplicities[index]
        key = self.keys[index]

        moments = self._merge_moments(
            self._aggregate(left), (multiplicity, key, 0.0)
        )
        moments = self._merge_moments(moments, self._aggregate(right))
        self.subtree_counts[index] = moments[0]
        self.subtree_means[index] = moments[1]
        self.subtree_m2[index] = moments[2]
        self.subtree_sums[index] = _aggregate_sum(
            (
                0.0 if left == self._NULL else self.subtree_sums[left],
                key * multiplicity,
                0.0 if right == self._NULL else self.subtree_sums[right],
            )
        )
        self.subtree_mins[index] = (
            key if left == self._NULL else self.subtree_mins[left]
        )
        self.subtree_maxes[index] = (
            key if right == self._NULL else self.subtree_maxes[right]
        )
        self.heights[index] = 1 + max(self._height(left), self._height(right))

    def _acquire(self, key: float) -> int:
        if not self.free_indexes:
            raise RuntimeError("AVL node pool is exhausted")
        index = self.free_indexes.pop()
        self.keys[index] = key
        self.multiplicities[index] = 1
        self.left[index] = self._NULL
        self.right[index] = self._NULL
        self.heights[index] = 1
        self.subtree_counts[index] = 1
        self.subtree_sums[index] = key
        self.subtree_means[index] = key
        self.subtree_m2[index] = 0.0
        self.subtree_mins[index] = key
        self.subtree_maxes[index] = key
        self.distinct_count += 1
        return index

    def _release(self, index: int) -> None:
        self.keys[index] = 0.0
        self.multiplicities[index] = 0
        self.left[index] = self._NULL
        self.right[index] = self._NULL
        self.heights[index] = 0
        self.subtree_counts[index] = 0
        self.subtree_sums[index] = 0.0
        self.subtree_means[index] = 0.0
        self.subtree_m2[index] = 0.0
        self.subtree_mins[index] = 0.0
        self.subtree_maxes[index] = 0.0
        self.free_indexes.append(index)
        self.distinct_count -= 1

    def _balance_factor(self, index: int) -> int:
        return self._height(self.left[index]) - self._height(self.right[index])

    def _rotate_left(self, index: int) -> int:
        promoted = self.right[index]
        if promoted == self._NULL:
            raise RuntimeError("invalid left rotation")
        middle = self.left[promoted]
        self.left[promoted] = index
        self.right[index] = middle
        self._pull(index)
        self._pull(promoted)
        return promoted

    def _rotate_right(self, index: int) -> int:
        promoted = self.left[index]
        if promoted == self._NULL:
            raise RuntimeError("invalid right rotation")
        middle = self.right[promoted]
        self.right[promoted] = index
        self.left[index] = middle
        self._pull(index)
        self._pull(promoted)
        return promoted

    def _rebalance(self, index: int) -> int:
        self._pull(index)
        balance = self._balance_factor(index)
        if balance > 1:
            left = self.left[index]
            if self._balance_factor(left) < 0:
                self.left[index] = self._rotate_left(left)
            return self._rotate_right(index)
        if balance < -1:
            right = self.right[index]
            if self._balance_factor(right) > 0:
                self.right[index] = self._rotate_right(right)
            return self._rotate_left(index)
        return index

    def _insert(self, index: int, key: float) -> int:
        if index == self._NULL:
            return self._acquire(key)
        if key < self.keys[index]:
            self.left[index] = self._insert(self.left[index], key)
        elif key > self.keys[index]:
            self.right[index] = self._insert(self.right[index], key)
        else:
            self.multiplicities[index] += 1
            self._pull(index)
            return index
        return self._rebalance(index)

    def insert(self, key: float) -> None:
        self.root = self._insert(self.root, key)

    def _minimum_index(self, index: int) -> int:
        while self.left[index] != self._NULL:
            index = self.left[index]
        return index

    def _delete(self, index: int, key: float, *, all_occurrences: bool) -> int:
        if index == self._NULL:
            raise KeyError(key)
        if key < self.keys[index]:
            self.left[index] = self._delete(
                self.left[index], key, all_occurrences=all_occurrences
            )
        elif key > self.keys[index]:
            self.right[index] = self._delete(
                self.right[index], key, all_occurrences=all_occurrences
            )
        elif self.multiplicities[index] > 1 and not all_occurrences:
            self.multiplicities[index] -= 1
            self._pull(index)
            return index
        else:
            left = self.left[index]
            right = self.right[index]
            if left == self._NULL or right == self._NULL:
                replacement = right if left == self._NULL else left
                self._release(index)
                return replacement
            successor = self._minimum_index(right)
            successor_key = self.keys[successor]
            self.keys[index] = successor_key
            self.multiplicities[index] = self.multiplicities[successor]
            self.right[index] = self._delete(
                right, successor_key, all_occurrences=True
            )
        return self._rebalance(index)

    def remove(self, key: float) -> None:
        self.root = self._delete(self.root, key, all_occurrences=False)

    def select(self, rank: int) -> float:
        if rank < 0 or rank >= self.count:
            raise IndexError("rank is outside the tree")
        index = self.root
        while index != self._NULL:
            left_count = self._count(self.left[index])
            if rank < left_count:
                index = self.left[index]
            elif rank < left_count + self.multiplicities[index]:
                return self.keys[index]
            else:
                rank -= left_count + self.multiplicities[index]
                index = self.right[index]
        raise RuntimeError("tree rank metadata is inconsistent")

    def counts_around(self, key: float) -> tuple[int, int]:
        less = 0
        index = self.root
        while index != self._NULL:
            if key < self.keys[index]:
                index = self.left[index]
            elif key > self.keys[index]:
                less += self._count(self.left[index]) + self.multiplicities[index]
                index = self.right[index]
            else:
                less += self._count(self.left[index])
                return less, self.multiplicities[index]
        return less, 0

    @property
    def count(self) -> int:
        return self._count(self.root)

    @property
    def total(self) -> float:
        return 0.0 if self.root == self._NULL else self.subtree_sums[self.root]

    @property
    def mean(self) -> float:
        return self.subtree_means[self.root]

    @property
    def m2(self) -> float:
        return self.subtree_m2[self.root]

    @property
    def minimum(self) -> float:
        return self.subtree_mins[self.root]

    @property
    def maximum(self) -> float:
        return self.subtree_maxes[self.root]

    def clear(self) -> None:
        for index in range(self.capacity):
            self.keys[index] = 0.0
            self.multiplicities[index] = 0
            self.left[index] = self._NULL
            self.right[index] = self._NULL
            self.heights[index] = 0
            self.subtree_counts[index] = 0
            self.subtree_sums[index] = 0.0
            self.subtree_means[index] = 0.0
            self.subtree_m2[index] = 0.0
            self.subtree_mins[index] = 0.0
            self.subtree_maxes[index] = 0.0
        self.free_indexes.clear()
        self.free_indexes.extend(range(self.capacity - 1, -1, -1))
        self.root = self._NULL
        self.distinct_count = 0

    def items(self) -> list[tuple[float, int]]:
        items: list[tuple[float, int]] = []

        def visit(index: int) -> None:
            if index == self._NULL:
                return
            visit(self.left[index])
            items.append((self.keys[index], self.multiplicities[index]))
            visit(self.right[index])

        visit(self.root)
        return items

    def validate(self) -> None:
        active: set[int] = set()

        def inspect(index: int) -> tuple[int, int, float, float, float, float, float]:
            if index == self._NULL:
                return 0, 0, 0.0, 0.0, 0.0, math.inf, -math.inf
            if index in active:
                raise AssertionError("tree contains a cycle or repeated slot")
            active.add(index)
            if not 0 <= index < self.capacity:
                raise AssertionError("tree index is outside its pool")
            left = inspect(self.left[index])
            right = inspect(self.right[index])
            key = self.keys[index]
            if self.left[index] != self._NULL and not left[6] < key:
                raise AssertionError("left subtree violates ordering")
            if self.right[index] != self._NULL and not key < right[5]:
                raise AssertionError("right subtree violates ordering")
            if self.multiplicities[index] <= 0:
                raise AssertionError("active node has no occurrences")
            height = 1 + max(left[0], right[0])
            if abs(left[0] - right[0]) > 1 or self.heights[index] != height:
                raise AssertionError("AVL height or balance is invalid")
            count = left[1] + self.multiplicities[index] + right[1]
            total = _aggregate_sum(
                (left[2], key * self.multiplicities[index], right[2])
            )
            moments = self._merge_moments(
                (left[1], left[3], left[4]),
                (self.multiplicities[index], key, 0.0),
            )
            moments = self._merge_moments(
                moments, (right[1], right[3], right[4])
            )
            minimum = left[5] if self.left[index] != self._NULL else key
            maximum = right[6] if self.right[index] != self._NULL else key
            if self.subtree_counts[index] != count:
                raise AssertionError("subtree count is invalid")
            for actual, expected, label in (
                (self.subtree_sums[index], total, "sum"),
                (self.subtree_means[index], moments[1], "mean"),
                (self.subtree_m2[index], moments[2], "M2"),
                (self.subtree_mins[index], minimum, "minimum"),
                (self.subtree_maxes[index], maximum, "maximum"),
            ):
                if not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12):
                    raise AssertionError(f"subtree {label} is invalid")
            return height, count, total, moments[1], moments[2], minimum, maximum

        inspect(self.root)
        free = set(self.free_indexes)
        if len(free) != len(self.free_indexes):
            raise AssertionError("free-index pool contains duplicates")
        if active & free or active | free != set(range(self.capacity)):
            raise AssertionError("active and free indexes do not partition the pool")
        if len(active) != self.distinct_count:
            raise AssertionError("distinct count is invalid")


class _PythonWindowStatisticsEngine:
    """Exact statistics over a fixed-size window using a recycled AVL pool."""

    name = "python"

    def __init__(self, window_size: int) -> None:
        size = _positive_window_size(window_size)
        tree = _IndexedAVL(size)
        ring = [0.0] * size
        self.window_size = size
        self._tree = tree
        self._ring = ring
        self._head = 0
        self._length = 0
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("window statistics engine is closed")

    def add(self, value: float) -> float | None:
        self._ensure_open()
        number = _finite_float(value)
        if self._length < self.window_size:
            self._tree.insert(number)
            position = (self._head + self._length) % self.window_size
            self._ring[position] = number
            self._length += 1
            return None

        outgoing = self._ring[self._head]
        self._tree.remove(outgoing)
        try:
            self._tree.insert(number)
        except Exception:
            self._tree.insert(outgoing)
            raise
        self._ring[self._head] = number
        self._head = (self._head + 1) % self.window_size
        return outgoing

    def add_many(self, values: Iterable[float]) -> list[float | None]:
        self._ensure_open()
        normalized = tuple(_finite_float(value) for value in values)
        return [self.add(value) for value in normalized]

    def remove_oldest(self) -> float:
        self._require_values()
        outgoing = self._ring[self._head]
        self._tree.remove(outgoing)
        self._head = (self._head + 1) % self.window_size
        self._length -= 1
        if self._length == 0:
            self._head = 0
        return outgoing

    def remove(self, value: float) -> None:
        self._ensure_open()
        number = _finite_float(value)
        match = -1
        for logical in range(self._length):
            position = (self._head + logical) % self.window_size
            if self._ring[position] == number:
                match = logical
                break
        if match < 0:
            raise ValueError(f"value is not present in the window: {number!r}")

        self._tree.remove(number)
        for logical in range(match, self._length - 1):
            target = (self._head + logical) % self.window_size
            source = (self._head + logical + 1) % self.window_size
            self._ring[target] = self._ring[source]
        self._length -= 1
        if self._length == 0:
            self._head = 0

    def clear(self) -> None:
        self._ensure_open()
        self._tree.clear()
        self._head = 0
        self._length = 0

    def _require_values(self) -> None:
        self._ensure_open()
        if self._length == 0:
            raise StatisticsError("no values in the window")

    @property
    def count(self) -> int:
        self._ensure_open()
        return self._length

    @property
    def sum(self) -> float:
        self._ensure_open()
        return self._tree.total

    @property
    def min(self) -> float:
        self._require_values()
        return self._tree.minimum

    @property
    def max(self) -> float:
        self._require_values()
        return self._tree.maximum

    @property
    def mean(self) -> float:
        self._require_values()
        return self._tree.mean

    @property
    def variance(self) -> float:
        self._require_values()
        variance = self._tree.m2 / self._length
        if variance < 0.0:
            tolerance = 16 * math.ulp(self._tree.mean) ** 2
            if variance < -tolerance:
                raise RuntimeError("tree produced a materially negative variance")
            return 0.0
        return variance

    @property
    def std(self) -> float:
        return math.sqrt(self.variance)

    @property
    def standard_deviation(self) -> float:
        return self.std

    def percentile(self, percentile: float) -> float:
        self._require_values()
        if isinstance(percentile, bool):
            raise TypeError("percentile must be a real number, not bool")
        try:
            requested = float(percentile)
        except (TypeError, ValueError) as exc:
            raise TypeError("percentile must be a real number") from exc
        if not math.isfinite(requested) or not 0.0 <= requested <= 100.0:
            raise ValueError("percentile must be finite and between 0 and 100")
        rank = requested / 100.0 * (self._length - 1)
        lower_rank = math.floor(rank)
        upper_rank = math.ceil(rank)
        lower = self._tree.select(lower_rank)
        if lower_rank == upper_rank:
            return lower
        upper = self._tree.select(upper_rank)
        fraction = rank - lower_rank
        return math.fsum((lower * (1.0 - fraction), upper * fraction))

    def percentile_of(self, value: float) -> float:
        self._require_values()
        number = _finite_float(value)
        less, equal = self._tree.counts_around(number)
        return 100.0 * (less + 0.5 * equal) / self._length

    def snapshot(self) -> WindowStatisticsSnapshot:
        self._ensure_open()
        if self._length == 0:
            return WindowStatisticsSnapshot(0, 0.0, None, None, None, None, None)
        variance = self.variance
        return WindowStatisticsSnapshot(
            count=self._length,
            sum=self._tree.total,
            min=self._tree.minimum,
            max=self._tree.maximum,
            mean=self._tree.mean,
            variance=variance,
            std=math.sqrt(variance),
        )

    def close(self) -> None:
        if self._closed:
            return
        self._tree.clear()
        self._head = 0
        self._length = 0
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _values_in_order(self) -> list[float]:
        return [
            self._ring[(self._head + logical) % self.window_size]
            for logical in range(self._length)
        ]

    def _validate(self) -> None:
        self._ensure_open()
        self._tree.validate()
        if not 0 <= self._length <= self.window_size:
            raise AssertionError("ring length is invalid")
        if not 0 <= self._head < self.window_size:
            raise AssertionError("ring head is invalid")
        ring_counts = Counter(self._values_in_order())
        tree_counts = Counter(dict(self._tree.items()))
        if ring_counts != tree_counts:
            raise AssertionError("ring and tree contain different values")
        if self._tree.count != self._length:
            raise AssertionError("ring and tree counts differ")


class WindowStatistics:
    """Backend-neutral facade for exact fixed-window statistics."""

    def __init__(self, window_size: int, *, backend: str = "python") -> None:
        from .window_statistics_backends import create_window_statistics_engine

        self.backend_name = backend.strip().lower()
        self._engine = create_window_statistics_engine(
            self.backend_name, window_size
        )
        self.window_size = self._engine.window_size

    def add(self, value: float) -> float | None:
        return self._engine.add(value)

    def add_many(self, values: Iterable[float]) -> list[float | None]:
        return self._engine.add_many(values)

    def remove_oldest(self) -> float:
        return self._engine.remove_oldest()

    def remove(self, value: float) -> None:
        self._engine.remove(value)

    def clear(self) -> None:
        self._engine.clear()

    def snapshot(self) -> WindowStatisticsSnapshot:
        return self._engine.snapshot()

    @property
    def count(self) -> int:
        return self._engine.count

    @property
    def sum(self) -> float:
        return self._engine.sum

    @property
    def min(self) -> float:
        return self._engine.min

    @property
    def max(self) -> float:
        return self._engine.max

    @property
    def mean(self) -> float:
        return self._engine.mean

    @property
    def variance(self) -> float:
        return self._engine.variance

    @property
    def std(self) -> float:
        return self._engine.std

    @property
    def standard_deviation(self) -> float:
        return self._engine.std

    def percentile(self, percentile: float) -> float:
        return self._engine.percentile(percentile)

    def percentile_of(self, value: float) -> float:
        return self._engine.percentile_of(value)

    def close(self) -> None:
        self._engine.close()

    @property
    def closed(self) -> bool:
        return self._engine.closed

    def __enter__(self):
        if self.closed:
            raise RuntimeError("window statistics engine is closed")
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def __getattr__(self, name: str):
        if name.startswith("_") and name != "_engine":
            return getattr(self._engine, name)
        raise AttributeError(name)
