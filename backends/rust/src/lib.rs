//! Recycled-array AVL implementation for streaming window statistics.

use std::cell::RefCell;
use std::ffi::{CString, c_char, c_void};
use std::panic::{AssertUnwindSafe, catch_unwind};

const NULL: i32 = -1;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum WindowError {
    InvalidWindowSize,
    InvalidValue,
    Empty,
    NotFound,
    Internal,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Snapshot {
    pub count: u64,
    pub sum: f64,
    pub min: Option<f64>,
    pub max: Option<f64>,
    pub mean: Option<f64>,
    pub variance: Option<f64>,
    pub std: Option<f64>,
}

#[derive(Clone, Copy)]
struct Moments {
    count: u64,
    mean: f64,
    m2: f64,
}

pub struct WindowStatistics {
    capacity: usize,
    keys: Vec<f64>,
    multiplicity: Vec<u64>,
    left: Vec<i32>,
    right: Vec<i32>,
    height: Vec<u32>,
    counts: Vec<u64>,
    sums: Vec<f64>,
    means: Vec<f64>,
    m2: Vec<f64>,
    mins: Vec<f64>,
    maxes: Vec<f64>,
    free: Vec<i32>,
    ring: Vec<f64>,
    root: i32,
    head: usize,
    length: usize,
}

impl WindowStatistics {
    pub fn new(capacity: usize) -> Result<Self, WindowError> {
        if capacity == 0 || capacity > i32::MAX as usize {
            return Err(WindowError::InvalidWindowSize);
        }
        let mut free = Vec::with_capacity(capacity);
        for index in (0..capacity).rev() {
            free.push(index as i32);
        }
        Ok(Self {
            capacity,
            keys: vec![0.0; capacity],
            multiplicity: vec![0; capacity],
            left: vec![NULL; capacity],
            right: vec![NULL; capacity],
            height: vec![0; capacity],
            counts: vec![0; capacity],
            sums: vec![0.0; capacity],
            means: vec![0.0; capacity],
            m2: vec![0.0; capacity],
            mins: vec![0.0; capacity],
            maxes: vec![0.0; capacity],
            free,
            ring: vec![0.0; capacity],
            root: NULL,
            head: 0,
            length: 0,
        })
    }

    fn normalize(value: f64) -> Result<f64, WindowError> {
        if !value.is_finite() {
            Err(WindowError::InvalidValue)
        } else if value == 0.0 {
            Ok(0.0)
        } else {
            Ok(value)
        }
    }
    fn h(&self, i: i32) -> u32 {
        if i == NULL {
            0
        } else {
            self.height[i as usize]
        }
    }
    fn count_at(&self, i: i32) -> u64 {
        if i == NULL {
            0
        } else {
            self.counts[i as usize]
        }
    }
    fn aggregate(&self, i: i32) -> Moments {
        if i == NULL {
            Moments {
                count: 0,
                mean: 0.0,
                m2: 0.0,
            }
        } else {
            let u = i as usize;
            Moments {
                count: self.counts[u],
                mean: self.means[u],
                m2: self.m2[u],
            }
        }
    }
    fn merge(a: Moments, b: Moments) -> Moments {
        if a.count == 0 {
            return b;
        }
        if b.count == 0 {
            return a;
        }
        let count = a.count + b.count;
        let total = count as f64;
        let delta = b.mean - a.mean;
        Moments {
            count,
            mean: a.mean * (a.count as f64 / total) + b.mean * (b.count as f64 / total),
            m2: a.m2 + b.m2 + delta * delta * a.count as f64 * (b.count as f64 / total),
        }
    }
    fn pull(&mut self, i: i32) {
        let u = i as usize;
        let l = self.left[u];
        let r = self.right[u];
        let key = self.keys[u];
        let moments = Self::merge(
            Self::merge(
                self.aggregate(l),
                Moments {
                    count: self.multiplicity[u],
                    mean: key,
                    m2: 0.0,
                },
            ),
            self.aggregate(r),
        );
        self.counts[u] = moments.count;
        self.means[u] = moments.mean;
        self.m2[u] = moments.m2;
        self.sums[u] = (if l == NULL {
            0.0
        } else {
            self.sums[l as usize]
        }) + key * self.multiplicity[u] as f64
            + (if r == NULL {
                0.0
            } else {
                self.sums[r as usize]
            });
        self.mins[u] = if l == NULL {
            key
        } else {
            self.mins[l as usize]
        };
        self.maxes[u] = if r == NULL {
            key
        } else {
            self.maxes[r as usize]
        };
        self.height[u] = 1 + self.h(l).max(self.h(r));
    }
    fn acquire(&mut self, key: f64) -> Result<i32, WindowError> {
        let i = self.free.pop().ok_or(WindowError::Internal)?;
        let u = i as usize;
        self.keys[u] = key;
        self.multiplicity[u] = 1;
        self.left[u] = NULL;
        self.right[u] = NULL;
        self.height[u] = 1;
        self.counts[u] = 1;
        self.sums[u] = key;
        self.means[u] = key;
        self.m2[u] = 0.0;
        self.mins[u] = key;
        self.maxes[u] = key;
        Ok(i)
    }
    fn release(&mut self, i: i32) {
        let u = i as usize;
        self.keys[u] = 0.0;
        self.multiplicity[u] = 0;
        self.left[u] = NULL;
        self.right[u] = NULL;
        self.height[u] = 0;
        self.counts[u] = 0;
        self.sums[u] = 0.0;
        self.means[u] = 0.0;
        self.m2[u] = 0.0;
        self.free.push(i);
    }
    fn balance(&self, i: i32) -> i32 {
        self.h(self.left[i as usize]) as i32 - self.h(self.right[i as usize]) as i32
    }
    fn rotate_left(&mut self, i: i32) -> i32 {
        let u = i as usize;
        let p = self.right[u];
        let pu = p as usize;
        let middle = self.left[pu];
        self.left[pu] = i;
        self.right[u] = middle;
        self.pull(i);
        self.pull(p);
        p
    }
    fn rotate_right(&mut self, i: i32) -> i32 {
        let u = i as usize;
        let p = self.left[u];
        let pu = p as usize;
        let middle = self.right[pu];
        self.right[pu] = i;
        self.left[u] = middle;
        self.pull(i);
        self.pull(p);
        p
    }
    fn rebalance(&mut self, i: i32) -> i32 {
        self.pull(i);
        let b = self.balance(i);
        if b > 1 {
            let l = self.left[i as usize];
            if self.balance(l) < 0 {
                self.left[i as usize] = self.rotate_left(l);
            }
            return self.rotate_right(i);
        }
        if b < -1 {
            let r = self.right[i as usize];
            if self.balance(r) > 0 {
                self.right[i as usize] = self.rotate_right(r);
            }
            return self.rotate_left(i);
        }
        i
    }
    fn insert_at(&mut self, i: i32, key: f64) -> Result<i32, WindowError> {
        if i == NULL {
            return self.acquire(key);
        }
        let u = i as usize;
        if key < self.keys[u] {
            self.left[u] = self.insert_at(self.left[u], key)?;
        } else if key > self.keys[u] {
            self.right[u] = self.insert_at(self.right[u], key)?;
        } else {
            self.multiplicity[u] += 1;
            self.pull(i);
            return Ok(i);
        }
        Ok(self.rebalance(i))
    }
    fn min_index(&self, mut i: i32) -> i32 {
        while self.left[i as usize] != NULL {
            i = self.left[i as usize];
        }
        i
    }
    fn delete_at(&mut self, i: i32, key: f64, all: bool) -> Result<i32, WindowError> {
        if i == NULL {
            return Err(WindowError::NotFound);
        }
        let u = i as usize;
        if key < self.keys[u] {
            self.left[u] = self.delete_at(self.left[u], key, all)?;
        } else if key > self.keys[u] {
            self.right[u] = self.delete_at(self.right[u], key, all)?;
        } else if self.multiplicity[u] > 1 && !all {
            self.multiplicity[u] -= 1;
            self.pull(i);
            return Ok(i);
        } else {
            let l = self.left[u];
            let r = self.right[u];
            if l == NULL || r == NULL {
                let replacement = if l == NULL { r } else { l };
                self.release(i);
                return Ok(replacement);
            }
            let successor = self.min_index(r);
            let key2 = self.keys[successor as usize];
            self.keys[u] = key2;
            self.multiplicity[u] = self.multiplicity[successor as usize];
            self.right[u] = self.delete_at(r, key2, true)?;
        }
        Ok(self.rebalance(i))
    }
    fn insert(&mut self, key: f64) -> Result<(), WindowError> {
        self.root = self.insert_at(self.root, key)?;
        Ok(())
    }
    fn delete(&mut self, key: f64) -> Result<(), WindowError> {
        self.root = self.delete_at(self.root, key, false)?;
        Ok(())
    }

    pub fn add(&mut self, value: f64) -> Result<Option<f64>, WindowError> {
        let value = Self::normalize(value)?;
        if self.length < self.capacity {
            self.insert(value)?;
            let p = (self.head + self.length) % self.capacity;
            self.ring[p] = value;
            self.length += 1;
            return Ok(None);
        }
        let old = self.ring[self.head];
        self.delete(old)?;
        self.insert(value)?;
        self.ring[self.head] = value;
        self.head = (self.head + 1) % self.capacity;
        Ok(Some(old))
    }
    pub fn add_many(&mut self, values: &[f64]) -> Result<Vec<Option<f64>>, WindowError> {
        let normalized: Result<Vec<_>, _> = values.iter().copied().map(Self::normalize).collect();
        normalized?.into_iter().map(|v| self.add(v)).collect()
    }
    pub fn remove_oldest(&mut self) -> Result<f64, WindowError> {
        if self.length == 0 {
            return Err(WindowError::Empty);
        }
        let old = self.ring[self.head];
        self.delete(old)?;
        self.head = (self.head + 1) % self.capacity;
        self.length -= 1;
        if self.length == 0 {
            self.head = 0;
        }
        Ok(old)
    }
    pub fn remove(&mut self, value: f64) -> Result<(), WindowError> {
        let value = Self::normalize(value)?;
        let mut found = None;
        for logical in 0..self.length {
            if self.ring[(self.head + logical) % self.capacity] == value {
                found = Some(logical);
                break;
            }
        }
        let found = found.ok_or(WindowError::NotFound)?;
        self.delete(value)?;
        for logical in found..self.length - 1 {
            let target = (self.head + logical) % self.capacity;
            let source = (self.head + logical + 1) % self.capacity;
            self.ring[target] = self.ring[source];
        }
        self.length -= 1;
        if self.length == 0 {
            self.head = 0;
        }
        Ok(())
    }
    pub fn clear(&mut self) {
        for i in 0..self.capacity {
            self.keys[i] = 0.0;
            self.multiplicity[i] = 0;
            self.left[i] = NULL;
            self.right[i] = NULL;
            self.height[i] = 0;
            self.counts[i] = 0;
            self.sums[i] = 0.0;
            self.means[i] = 0.0;
            self.m2[i] = 0.0;
        }
        self.free.clear();
        for i in (0..self.capacity).rev() {
            self.free.push(i as i32);
        }
        self.root = NULL;
        self.head = 0;
        self.length = 0;
    }
    pub fn snapshot(&self) -> Snapshot {
        if self.length == 0 {
            return Snapshot {
                count: 0,
                sum: 0.0,
                min: None,
                max: None,
                mean: None,
                variance: None,
                std: None,
            };
        }
        let u = self.root as usize;
        let variance = self.m2[u] / self.length as f64;
        Snapshot {
            count: self.length as u64,
            sum: self.sums[u],
            min: Some(self.mins[u]),
            max: Some(self.maxes[u]),
            mean: Some(self.means[u]),
            variance: Some(variance),
            std: Some(variance.sqrt()),
        }
    }
    fn select(&self, mut rank: u64) -> f64 {
        let mut i = self.root;
        loop {
            let u = i as usize;
            let left = self.count_at(self.left[u]);
            if rank < left {
                i = self.left[u];
            } else if rank < left + self.multiplicity[u] {
                return self.keys[u];
            } else {
                rank -= left + self.multiplicity[u];
                i = self.right[u];
            }
        }
    }
    pub fn percentile(&self, p: f64) -> Result<f64, WindowError> {
        if self.length == 0 {
            return Err(WindowError::Empty);
        }
        if !p.is_finite() || !(0.0..=100.0).contains(&p) {
            return Err(WindowError::InvalidValue);
        }
        let rank = p / 100.0 * (self.length - 1) as f64;
        let lo = rank.floor() as u64;
        let hi = rank.ceil() as u64;
        let a = self.select(lo);
        if lo == hi {
            return Ok(a);
        }
        let b = self.select(hi);
        let f = rank - lo as f64;
        Ok(a * (1.0 - f) + b * f)
    }
    pub fn percentile_of(&self, value: f64) -> Result<f64, WindowError> {
        if self.length == 0 {
            return Err(WindowError::Empty);
        }
        let value = Self::normalize(value)?;
        let (mut less, mut equal, mut i) = (0u64, 0u64, self.root);
        while i != NULL {
            let u = i as usize;
            if value < self.keys[u] {
                i = self.left[u];
            } else if value > self.keys[u] {
                less += self.count_at(self.left[u]) + self.multiplicity[u];
                i = self.right[u];
            } else {
                less += self.count_at(self.left[u]);
                equal = self.multiplicity[u];
                break;
            }
        }
        Ok(100.0 * (less as f64 + 0.5 * equal as f64) / self.length as f64)
    }

    /// Validate the tree/ring partition and all cached AVL metadata.
    pub fn validate(&self) -> Result<(), WindowError> {
        fn walk(
            window: &WindowStatistics,
            node: i32,
            lower: Option<f64>,
            upper: Option<f64>,
            active: &mut [bool],
            ordered: &mut Vec<f64>,
        ) -> Result<(u32, u64), WindowError> {
            if node == NULL {
                return Ok((0, 0));
            }
            let index = usize::try_from(node).map_err(|_| WindowError::Internal)?;
            if index >= window.capacity || active[index] || window.multiplicity[index] == 0 {
                return Err(WindowError::Internal);
            }
            let key = window.keys[index];
            if lower.is_some_and(|value| key <= value) || upper.is_some_and(|value| key >= value) {
                return Err(WindowError::Internal);
            }
            active[index] = true;
            let (left_height, left_count) = walk(
                window,
                window.left[index],
                lower,
                Some(key),
                active,
                ordered,
            )?;
            ordered.extend(std::iter::repeat_n(
                key,
                window.multiplicity[index] as usize,
            ));
            let (right_height, right_count) = walk(
                window,
                window.right[index],
                Some(key),
                upper,
                active,
                ordered,
            )?;
            let height = 1 + left_height.max(right_height);
            let count = left_count + window.multiplicity[index] + right_count;
            if left_height.abs_diff(right_height) > 1
                || window.height[index] != height
                || window.counts[index] != count
            {
                return Err(WindowError::Internal);
            }
            Ok((height, count))
        }

        let mut active = vec![false; self.capacity];
        let mut tree_values = Vec::with_capacity(self.length);
        let (_, count) = walk(self, self.root, None, None, &mut active, &mut tree_values)?;
        if count != self.length as u64
            || self.free.len() + active.iter().filter(|v| **v).count() != self.capacity
        {
            return Err(WindowError::Internal);
        }
        let mut free_seen = vec![false; self.capacity];
        for &slot in &self.free {
            let index = usize::try_from(slot).map_err(|_| WindowError::Internal)?;
            if index >= self.capacity || active[index] || free_seen[index] {
                return Err(WindowError::Internal);
            }
            free_seen[index] = true;
        }
        let mut ring_values: Vec<_> = (0..self.length)
            .map(|offset| self.ring[(self.head + offset) % self.capacity])
            .collect();
        ring_values.sort_by(f64::total_cmp);
        if tree_values != ring_values {
            return Err(WindowError::Internal);
        }
        Ok(())
    }
}

const ABI_VERSION: u32 = 1;
const OK: i32 = 0;
const INVALID_ARGUMENT: i32 = 1;
const EMPTY_WINDOW: i32 = 2;
const VALUE_NOT_FOUND: i32 = 3;
const INTERNAL_ERROR: i32 = 5;

#[repr(C)]
pub struct CWindowSnapshot {
    abi_version: u32,
    struct_size: u32,
    count: u64,
    sum: f64,
    min: f64,
    max: f64,
    mean: f64,
    variance: f64,
    std: f64,
    has_values: u8,
    reserved: [u8; 7],
}

thread_local! {
    static LAST_ERROR: RefCell<CString> = RefCell::new(CString::new("no error").unwrap());
}

fn set_error(message: &str) {
    let sanitized = message.replace('\0', " ");
    LAST_ERROR.with(|slot| *slot.borrow_mut() = CString::new(sanitized).unwrap());
}

fn error_status(error: WindowError) -> i32 {
    let (status, message) = match error {
        WindowError::InvalidWindowSize => (
            INVALID_ARGUMENT,
            "window size must be positive and representable",
        ),
        WindowError::InvalidValue => (INVALID_ARGUMENT, "value must be finite"),
        WindowError::Empty => (EMPTY_WINDOW, "empty window"),
        WindowError::NotFound => (VALUE_NOT_FOUND, "value is not present in window"),
        WindowError::Internal => (INTERNAL_ERROR, "Rust backend invariant failure"),
    };
    set_error(message);
    status
}

fn ffi_status(operation: impl FnOnce() -> Result<(), WindowError>) -> i32 {
    match catch_unwind(AssertUnwindSafe(operation)) {
        Ok(Ok(())) => OK,
        Ok(Err(error)) => error_status(error),
        Err(_) => {
            set_error("panic contained at Rust ABI boundary");
            INTERNAL_ERROR
        }
    }
}

unsafe fn state_mut<'a>(state: *mut c_void) -> Result<&'a mut WindowStatistics, WindowError> {
    if state.is_null() {
        Err(WindowError::InvalidValue)
    } else {
        // SAFETY: handles are created by `stream_stats_ws_create` and uniquely owned by the caller.
        Ok(unsafe { &mut *state.cast::<WindowStatistics>() })
    }
}

unsafe fn state_ref<'a>(state: *const c_void) -> Result<&'a WindowStatistics, WindowError> {
    if state.is_null() {
        Err(WindowError::InvalidValue)
    } else {
        // SAFETY: handles are created by `stream_stats_ws_create` and remain alive for this call.
        Ok(unsafe { &*state.cast::<WindowStatistics>() })
    }
}

unsafe fn write_snapshot(
    state: &WindowStatistics,
    out: *mut CWindowSnapshot,
) -> Result<(), WindowError> {
    if out.is_null() {
        return Err(WindowError::InvalidValue);
    }
    // SAFETY: checked non-null and the C ABI requires writable storage of this exact structure.
    let output = unsafe { &mut *out };
    if output.abi_version != ABI_VERSION
        || output.struct_size < std::mem::size_of::<CWindowSnapshot>() as u32
    {
        return Err(WindowError::InvalidValue);
    }
    let snapshot = state.snapshot();
    output.count = snapshot.count;
    output.sum = snapshot.sum;
    output.min = snapshot.min.unwrap_or(0.0);
    output.max = snapshot.max.unwrap_or(0.0);
    output.mean = snapshot.mean.unwrap_or(0.0);
    output.variance = snapshot.variance.unwrap_or(0.0);
    output.std = snapshot.std.unwrap_or(0.0);
    output.has_values = u8::from(snapshot.count != 0);
    output.reserved = [0; 7];
    Ok(())
}

#[unsafe(no_mangle)]
extern "C" fn stream_stats_ws_abi_version() -> u32 {
    ABI_VERSION
}

#[unsafe(no_mangle)]
extern "C" fn stream_stats_ws_last_error() -> *const c_char {
    LAST_ERROR.with(|slot| slot.borrow().as_ptr())
}

#[unsafe(no_mangle)]
extern "C" fn stream_stats_ws_create(window_size: u64, out: *mut *mut c_void) -> i32 {
    ffi_status(|| {
        if out.is_null() || window_size > usize::MAX as u64 {
            return Err(WindowError::InvalidWindowSize);
        }
        let state = WindowStatistics::new(window_size as usize)?;
        // SAFETY: validated writable out pointer supplied by the ABI caller.
        unsafe { *out = Box::into_raw(Box::new(state)).cast() };
        Ok(())
    })
}

#[unsafe(no_mangle)]
extern "C" fn stream_stats_ws_destroy(state: *mut c_void) {
    if state.is_null() {
        return;
    }
    let _ = catch_unwind(AssertUnwindSafe(|| {
        // SAFETY: ownership of a handle from create is returned exactly once.
        unsafe { drop(Box::from_raw(state.cast::<WindowStatistics>())) };
    }));
}

#[unsafe(no_mangle)]
extern "C" fn stream_stats_ws_clear(state: *mut c_void) -> i32 {
    ffi_status(|| {
        unsafe { state_mut(state)? }.clear();
        Ok(())
    })
}

#[unsafe(no_mangle)]
extern "C" fn stream_stats_ws_add(
    state: *mut c_void,
    value: f64,
    did_evict: *mut u8,
    evicted: *mut f64,
) -> i32 {
    ffi_status(|| {
        if did_evict.is_null() || evicted.is_null() {
            return Err(WindowError::InvalidValue);
        }
        let result = unsafe { state_mut(state)? }.add(value)?;
        unsafe {
            *did_evict = u8::from(result.is_some());
            *evicted = result.unwrap_or(0.0);
        }
        Ok(())
    })
}

#[unsafe(no_mangle)]
extern "C" fn stream_stats_ws_add_many(
    state: *mut c_void,
    values: *const f64,
    length: u64,
    did_evict: *mut u8,
    evicted: *mut f64,
    eviction_capacity: u64,
    final_snapshot: *mut CWindowSnapshot,
) -> i32 {
    ffi_status(|| {
        let length = usize::try_from(length).map_err(|_| WindowError::InvalidValue)?;
        if length != 0 && values.is_null() {
            return Err(WindowError::InvalidValue);
        }
        let outputs_omitted = did_evict.is_null() && evicted.is_null() && eviction_capacity == 0;
        if !outputs_omitted
            && (did_evict.is_null() || evicted.is_null() || eviction_capacity < length as u64)
        {
            return Err(WindowError::InvalidValue);
        }
        let input = if length == 0 {
            &[]
        } else {
            unsafe { std::slice::from_raw_parts(values, length) }
        };
        if input.iter().any(|value| !value.is_finite()) {
            return Err(WindowError::InvalidValue);
        }
        let window = unsafe { state_mut(state)? };
        for (index, &value) in input.iter().enumerate() {
            let result = window.add(value)?;
            if !outputs_omitted {
                unsafe {
                    *did_evict.add(index) = u8::from(result.is_some());
                    *evicted.add(index) = result.unwrap_or(0.0);
                }
            }
        }
        if !final_snapshot.is_null() {
            unsafe { write_snapshot(window, final_snapshot)? };
        }
        Ok(())
    })
}

#[unsafe(no_mangle)]
extern "C" fn stream_stats_ws_remove_oldest(state: *mut c_void, removed: *mut f64) -> i32 {
    ffi_status(|| {
        if removed.is_null() {
            return Err(WindowError::InvalidValue);
        }
        let value = unsafe { state_mut(state)? }.remove_oldest()?;
        unsafe { *removed = value };
        Ok(())
    })
}

#[unsafe(no_mangle)]
extern "C" fn stream_stats_ws_remove_value(state: *mut c_void, value: f64) -> i32 {
    ffi_status(|| unsafe { state_mut(state)? }.remove(value))
}

#[unsafe(no_mangle)]
extern "C" fn stream_stats_ws_snapshot_get(state: *const c_void, out: *mut CWindowSnapshot) -> i32 {
    ffi_status(|| unsafe { write_snapshot(state_ref(state)?, out) })
}

#[unsafe(no_mangle)]
extern "C" fn stream_stats_ws_count(state: *const c_void, out: *mut u64) -> i32 {
    ffi_status(|| {
        if out.is_null() {
            return Err(WindowError::InvalidValue);
        }
        let value = unsafe { state_ref(state)? }.snapshot().count;
        unsafe { *out = value };
        Ok(())
    })
}

#[unsafe(no_mangle)]
extern "C" fn stream_stats_ws_sum(state: *const c_void, out: *mut f64) -> i32 {
    ffi_status(|| {
        if out.is_null() {
            return Err(WindowError::InvalidValue);
        }
        let value = unsafe { state_ref(state)? }.snapshot().sum;
        unsafe { *out = value };
        Ok(())
    })
}

#[unsafe(no_mangle)]
extern "C" fn stream_stats_ws_min(state: *const c_void, out: *mut f64) -> i32 {
    ffi_status(|| {
        if out.is_null() {
            return Err(WindowError::InvalidValue);
        }
        let value = unsafe { state_ref(state)? }
            .snapshot()
            .min
            .ok_or(WindowError::Empty)?;
        unsafe { *out = value };
        Ok(())
    })
}

#[unsafe(no_mangle)]
extern "C" fn stream_stats_ws_max(state: *const c_void, out: *mut f64) -> i32 {
    ffi_status(|| {
        if out.is_null() {
            return Err(WindowError::InvalidValue);
        }
        let value = unsafe { state_ref(state)? }
            .snapshot()
            .max
            .ok_or(WindowError::Empty)?;
        unsafe { *out = value };
        Ok(())
    })
}

#[unsafe(no_mangle)]
extern "C" fn stream_stats_ws_percentile(
    state: *const c_void,
    percentile: f64,
    out: *mut f64,
) -> i32 {
    ffi_status(|| {
        if out.is_null() {
            return Err(WindowError::InvalidValue);
        }
        let value = unsafe { state_ref(state)? }.percentile(percentile)?;
        unsafe { *out = value };
        Ok(())
    })
}

#[unsafe(no_mangle)]
extern "C" fn stream_stats_ws_percentile_of(
    state: *const c_void,
    value: f64,
    out: *mut f64,
) -> i32 {
    ffi_status(|| {
        if out.is_null() {
            return Err(WindowError::InvalidValue);
        }
        let result = unsafe { state_ref(state)? }.percentile_of(value)?;
        unsafe { *out = result };
        Ok(())
    })
}

#[unsafe(no_mangle)]
extern "C" fn stream_stats_ws_validate(state: *const c_void) -> i32 {
    ffi_status(|| unsafe { state_ref(state)? }.validate())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn basic_and_wrap() {
        let mut w = WindowStatistics::new(3).unwrap();
        assert_eq!(
            w.add_many(&[1.0, 2.0, 3.0, 4.0]).unwrap(),
            vec![None, None, None, Some(1.0)]
        );
        let s = w.snapshot();
        assert_eq!(s.count, 3);
        assert_eq!(s.sum, 9.0);
        assert!((s.variance.unwrap() - 2.0 / 3.0).abs() < 1e-12);
        assert_eq!(w.percentile(50.0).unwrap(), 3.0);
    }
    #[test]
    fn duplicate_remove() {
        let mut w = WindowStatistics::new(5).unwrap();
        w.add_many(&[1.0, 2.0, 2.0, 4.0, 9.0]).unwrap();
        assert_eq!(w.percentile_of(2.0).unwrap(), 40.0);
        w.remove(2.0).unwrap();
        assert_eq!(w.snapshot().sum, 16.0);
    }
    #[test]
    fn validates_batch_before_mutation() {
        let mut w = WindowStatistics::new(2).unwrap();
        w.add(9.0).unwrap();
        assert_eq!(w.add_many(&[1.0, f64::NAN]), Err(WindowError::InvalidValue));
        assert_eq!(w.snapshot().sum, 9.0);
    }

    #[test]
    fn validates_recycled_structure() {
        let mut w = WindowStatistics::new(32).unwrap();
        for index in 0..500 {
            w.add(((index * 37) % 53) as f64).unwrap();
            if index % 11 == 0 {
                w.remove_oldest().unwrap();
            }
            w.validate().unwrap();
        }
    }
}
