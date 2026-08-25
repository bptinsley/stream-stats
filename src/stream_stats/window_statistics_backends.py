from __future__ import annotations

from collections.abc import Callable
from importlib import metadata
from typing import Any

from .backends import BackendInfo, BackendUnavailableError

WindowStatisticsFactory = Callable[[int], Any]
FIRST_PARTY_WINDOW_STATISTICS_BACKENDS = (
    "python",
    "cython",
    "assembly",
    "c",
    "cpp",
    "rust",
    "go",
    "java",
    "scala",
)


def _create_python_engine(window_size: int):
    from ._window_statistics import _PythonWindowStatisticsEngine

    return _PythonWindowStatisticsEngine(window_size)


_factories: dict[str, WindowStatisticsFactory] = {"python": _create_python_engine}
_plugins_loaded = False


def register_window_statistics_backend(
    name: str,
    factory: WindowStatisticsFactory,
    *,
    replace: bool = False,
) -> None:
    normalized = name.strip().lower()
    if not normalized:
        raise ValueError("backend name cannot be empty")
    if normalized in _factories and not replace:
        raise ValueError(f"window statistics backend already registered: {normalized}")
    _factories[normalized] = factory


def _load_plugins() -> None:
    global _plugins_loaded
    if _plugins_loaded:
        return
    _plugins_loaded = True
    for entry_point in metadata.entry_points(
        group="stream_stats.window_statistics"
    ):
        if entry_point.name not in _factories:
            _factories[entry_point.name] = entry_point.load()
    try:
        from ._cython_native import CythonWindowStatisticsEngine

        _factories.setdefault("cython", CythonWindowStatisticsEngine)
    except (ImportError, OSError):
        pass
    try:
        from ._c_window_statistics import CWindowStatisticsEngine, c_library_available

        if c_library_available():
            _factories.setdefault("c", CWindowStatisticsEngine)
    except (ImportError, OSError):
        pass
    try:
        from ._scala_window_statistics import (
            ScalaWindowStatisticsEngine,
            scala_backend_available,
        )

        if scala_backend_available():
            _factories.setdefault("scala", ScalaWindowStatisticsEngine)
    except (ImportError, OSError):
        pass
    try:
        from ._java_window_statistics import (
            JavaWindowStatisticsEngine,
            java_backend_available,
        )

        if java_backend_available():
            _factories.setdefault("java", JavaWindowStatisticsEngine)
    except (ImportError, OSError):
        pass
    try:
        from ._go_window_statistics import (
            GoWindowStatisticsEngine,
            go_library_available,
        )

        if go_library_available():
            _factories.setdefault("go", GoWindowStatisticsEngine)
    except (ImportError, OSError):
        pass
    try:
        from ._cpp_window_statistics import (
            CppWindowStatisticsEngine,
            cpp_library_available,
        )

        if cpp_library_available():
            _factories.setdefault("cpp", CppWindowStatisticsEngine)
    except (ImportError, OSError):
        pass
    try:
        from ._rust_window_statistics import (
            RustWindowStatisticsEngine,
            rust_library_available,
        )

        if rust_library_available():
            _factories.setdefault("rust", RustWindowStatisticsEngine)
    except (ImportError, OSError):
        pass


def list_window_statistics_backends() -> tuple[BackendInfo, ...]:
    _load_plugins()
    names = list(FIRST_PARTY_WINDOW_STATISTICS_BACKENDS)
    names.extend(sorted(set(_factories) - set(names)))
    return tuple(
        BackendInfo(
            name=name,
            available=name in _factories,
            provider=(
                "built-in"
                if name == "python"
                else ("plugin" if name in _factories else "first-party workspace")
            ),
            detail=(
                ""
                if name in _factories
                else f"install stream-stats-{name} or build backends/{name}"
            ),
        )
        for name in names
    )


def get_window_statistics_backend_info(name: str) -> BackendInfo:
    normalized = name.strip().lower()
    for info in list_window_statistics_backends():
        if info.name == normalized:
            return info
    return BackendInfo(
        normalized,
        False,
        "unknown",
        "no window statistics backend with this name is registered",
    )


def create_window_statistics_engine(name: str, window_size: int):
    _load_plugins()
    normalized = name.strip().lower()
    factory = _factories.get(normalized)
    if factory is None:
        info = get_window_statistics_backend_info(normalized)
        raise BackendUnavailableError(
            f"backend '{normalized}' is unavailable: {info.detail}"
        )
    engine = factory(window_size)
    required = (
        "add",
        "add_many",
        "remove_oldest",
        "remove",
        "clear",
        "snapshot",
        "close",
    )
    missing = [method for method in required if not hasattr(engine, method)]
    if missing:
        raise TypeError(
            f"backend '{normalized}' does not implement WindowStatisticsEngine: "
            + ", ".join(missing)
        )
    return engine
