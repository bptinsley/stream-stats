from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from importlib import metadata
from typing import Any

from ._protocol import Backend, BackendFactory, Statistic
from ._python_backend import PythonBackend

FIRST_PARTY_BACKENDS = ("python", "assembly", "c", "cpp", "rust", "go", "scala", "java")


class BackendUnavailableError(RuntimeError):
    """Raised when a configured backend is known but not installed or built."""


@dataclass(frozen=True, slots=True)
class BackendInfo:
    name: str
    available: bool
    provider: str
    detail: str = ""


_factories: dict[str, BackendFactory] = {"python": PythonBackend}
_plugins_loaded = False


def register_backend(name: str, factory: BackendFactory, *, replace: bool = False) -> None:
    """Register a backend factory, primarily for first- and third-party adapters."""
    normalized = name.strip().lower()
    if not normalized:
        raise ValueError("backend name cannot be empty")
    if normalized in _factories and not replace:
        raise ValueError(f"backend already registered: {normalized}")
    _factories[normalized] = factory


def _load_plugins() -> None:
    global _plugins_loaded
    if _plugins_loaded:
        return
    _plugins_loaded = True
    for entry_point in metadata.entry_points(group="stream_stats.backends"):
        if entry_point.name not in _factories:
            _factories[entry_point.name] = entry_point.load()


def list_backends() -> tuple[BackendInfo, ...]:
    _load_plugins()
    names = list(FIRST_PARTY_BACKENDS)
    names.extend(sorted(set(_factories) - set(names)))
    return tuple(
        BackendInfo(
            name=name,
            available=name in _factories,
            provider="built-in" if name == "python" else ("plugin" if name in _factories else "first-party workspace"),
            detail="" if name in _factories else f"build backends/{name} and install its Python adapter",
        )
        for name in names
    )


def get_backend_info(name: str) -> BackendInfo:
    normalized = name.strip().lower()
    for info in list_backends():
        if info.name == normalized:
            return info
    return BackendInfo(normalized, False, "unknown", "no backend with this name is registered")


def create_backend(
    name: str, window_size: int, statistics: Iterable[Statistic]
) -> Backend:
    _load_plugins()
    normalized = name.strip().lower()
    factory = _factories.get(normalized)
    if factory is None:
        info = get_backend_info(normalized)
        raise BackendUnavailableError(f"backend '{normalized}' is unavailable: {info.detail}")
    backend: Any = factory(window_size, statistics)
    if not hasattr(backend, "push") or not hasattr(backend, "reset"):
        raise TypeError(f"backend '{normalized}' does not implement the backend contract")
    return backend
