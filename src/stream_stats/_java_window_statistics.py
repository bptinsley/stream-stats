from __future__ import annotations

import os
from pathlib import Path

from ._jvm_window_statistics import JVMWindowStatisticsEngine


class JavaWindowStatisticsEngine(JVMWindowStatisticsEngine):
    name = "java"

    @classmethod
    def artifact_path(cls) -> Path | None:
        configured = os.environ.get("STREAM_STATS_JAVA_JAR")
        candidates = [Path(configured)] if configured else []
        repository = Path(__file__).resolve().parents[2]
        candidates.append(
            repository / "backends" / "java" / "build" / "stream-stats-java.jar"
        )
        return next((path for path in candidates if path.is_file()), None)


def java_backend_available() -> bool:
    return JavaWindowStatisticsEngine.available()
