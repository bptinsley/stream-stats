from __future__ import annotations

import os
import sys

from setuptools import Extension, setup


compile_args = ["-O3"]
if sys.platform != "win32":
    compile_args.extend(
        ["-fvisibility=hidden", "-fno-fast-math", "-ffp-contract=off"]
    )

extensions = []
if os.environ.get("STREAM_STATS_BUILD_CYTHON") == "1":
    from Cython.Build import cythonize

    extensions = cythonize(
        [
            Extension(
                "stream_stats._cython_native",
                sources=[
                    "src/stream_stats/_cython_native.pyx",
                    "backends/c/src/stream_stats_window.c",
                ],
                include_dirs=["backends/c/include"],
                extra_compile_args=compile_args,
            )
        ],
        language_level=3,
    )

setup(ext_modules=extensions)
