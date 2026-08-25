from __future__ import annotations

import os
from pathlib import Path

from ._jvm_window_statistics import JVMWindowStatisticsEngine


class ScalaWindowStatisticsEngine(JVMWindowStatisticsEngine):
    name = "scala"

    @classmethod
    def artifact_path(cls) -> Path | None:
        configured = os.environ.get("STREAM_STATS_SCALA_JAR")
        candidates = [Path(configured)] if configured else []
        repository = Path(__file__).resolve().parents[2]
        candidates.append(
            repository
            / "backends"
            / "scala"
            / "build"
            / "stream-stats-scala.jar"
        )
        return next((path for path in candidates if path.is_file()), None)

    @classmethod
    def available(cls) -> bool:
        return cls.artifact_path() is not None and cls.java_executable() is not None


def scala_backend_available() -> bool:
    return ScalaWindowStatisticsEngine.available()
