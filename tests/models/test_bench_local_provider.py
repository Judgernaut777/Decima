"""The optional local-provider bench probe skips cleanly with no endpoint and never
hardcodes a model id — the recommendation is reported, the running id decides."""

from __future__ import annotations

import importlib.util
import pathlib

_BENCH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "bench_local_provider.py"


def _load():
    spec = importlib.util.spec_from_file_location("bench_local_provider", _BENCH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_probe_skips_cleanly_with_no_endpoint():
    bench = _load()
    report = bench.probe(env={}, runs=3)
    assert report["configured"] is False
    assert "latency_ms" not in report  # nothing measured
    assert report["recommended_local_model"] == "Qwen3.6-35B-A3B"


def test_probe_skips_on_partial_config():
    bench = _load()
    # provider set but no base URL ⇒ not configured, no network touched.
    report = bench.probe(
        env={"DECIMA_LIVE_PROVIDER": "local", "DECIMA_LIVE_MODEL": "some-id"}, runs=1
    )
    assert report["configured"] is False


def test_main_exits_zero_without_endpoint(capsys):
    bench = _load()
    rc = bench.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "decima-local-provider-bench" in out
