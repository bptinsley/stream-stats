import json

from stream_stats.cli import main


def test_backends_command(capsys):
    assert main(["backends"]) == 0
    output = capsys.readouterr().out
    assert "python" in output
    assert "available" in output


def test_benchmark_json(capsys):
    assert main(
        [
            "benchmark",
            "--backend",
            "python",
            "--size",
            "8",
            "--samples",
            "20",
            "--warmup",
            "2",
            "--repeats",
            "1",
            "--format",
            "json",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result[0]["backend"] == "python"
    assert result[0]["samples"] == 20
    assert result[0]["median_samples_per_second"] > 0


def test_unavailable_backend_exits_cleanly(capsys):
    assert main(["benchmark", "--backend", "java"]) == 2
    assert "backend 'java' is unavailable" in capsys.readouterr().err
