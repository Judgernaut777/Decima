#!/usr/bin/env python3
"""A bounded latency / structured-output probe of the configured LOCAL model endpoint.

Operator convenience only — NOT part of any gate and NOT a source of authority. It
drives the SAME product transport (`decima.services.api.models_setup.openai_chat_backend`)
the live path uses, against the endpoint named by the standard `DECIMA_LIVE_*` env
vars, and prints a small deterministic-shaped report (latencies as INT milliseconds).

It is model-AGNOSTIC: the model id always comes from `DECIMA_LIVE_MODEL` / the
registry, never hardcoded. The forward-guidance recommendation
(`models_setup.RECOMMENDED_LOCAL_MODEL`) is printed for the operator's reference but
is never asserted to be the running model — the box may serve a different id.

Skips CLEANLY (exit 0, nothing measured) when no endpoint is configured, so it is
safe to invoke unconditionally. It measures wall-clock latency for the operator's
eyes; nothing it produces is recorded on the Weft, so no determinism invariant is
touched.

Usage:

    DECIMA_LIVE_PROVIDER=local \\
    DECIMA_LIVE_MODEL=<model-id-the-endpoint-serves> \\
    DECIMA_LIVE_BASE_URL=http://127.0.0.1:8080 \\
    python3 scripts/bench_local_provider.py [--runs N] [--max-output-tokens N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from decima.models.providers import ModelRequest
from decima.services.api.models_setup import (
    ENV_BASE_URL,
    ENV_MODEL,
    ENV_PROVIDER,
    ENV_TIMEOUT,
    RECOMMENDED_LOCAL_MODEL,
    openai_chat_backend,
)

# A single fixed, synthetic probe — no user data, no instructions to obey.
_PROBE_PROMPT = "Reply with the single word: pong."
_STRUCT_SCHEMA = {
    "action": "echo",
    "fields": {
        "word": {"type": "string", "required": True},
        "score": {"type": "int", "min": 0, "max": 5},
    },
}


def _config(env: dict) -> dict | None:
    kind = (env.get(ENV_PROVIDER) or "").strip().lower()
    model = (env.get(ENV_MODEL) or "").strip()
    base = (env.get(ENV_BASE_URL) or "").strip()
    if kind != "local" or not model or not base:
        return None
    try:
        timeout_s = int(env.get(ENV_TIMEOUT) or 30)
    except ValueError:
        timeout_s = 30
    return {"model": model, "base": base, "timeout_s": timeout_s}


class _Caps:
    """Minimal capabilities shim the transport reads (`.model`)."""

    def __init__(self, model: str) -> None:
        self.model = model


def probe(env: dict | None = None, *, runs: int = 3, max_output_tokens: int = 32) -> dict:
    """Run a bounded probe. Returns a report dict; when no endpoint is configured the
    report has ``configured=False`` and measures nothing (clean skip)."""
    env = os.environ if env is None else env
    cfg = _config(env)
    report: dict = {
        "kind": "decima-local-provider-bench",
        "recommended_local_model": RECOMMENDED_LOCAL_MODEL,
        "configured": cfg is not None,
    }
    if cfg is None:
        report["note"] = (
            "no local endpoint configured — set "
            f"{ENV_PROVIDER}=local / {ENV_MODEL} / {ENV_BASE_URL} to measure"
        )
        return report

    backend = openai_chat_backend(cfg["base"], timeout_s=cfg["timeout_s"])
    caps = _Caps(cfg["model"])
    report["model"] = cfg["model"]
    report["base_url"] = cfg["base"]

    latencies_ms: list[int] = []
    ok = 0
    for _ in range(max(1, int(runs))):
        req = ModelRequest(prompt=_PROBE_PROMPT, max_output_tokens=int(max_output_tokens))
        t0 = time.monotonic()
        resp = backend(req, caps)
        latencies_ms.append(int((time.monotonic() - t0) * 1000))
        if not resp.failed and resp.text:
            ok += 1

    # one structured-output probe: did the endpoint return a parseable JSON object?
    sreq = ModelRequest(
        prompt=_PROBE_PROMPT,
        max_output_tokens=int(max_output_tokens),
        structured_schema=_STRUCT_SCHEMA,
    )
    sresp = backend(sreq, caps)
    report["runs"] = len(latencies_ms)
    report["ok_runs"] = ok
    report["latency_ms"] = sorted(latencies_ms)
    report["latency_ms_min"] = min(latencies_ms)
    report["latency_ms_max"] = max(latencies_ms)
    report["structured_parseable"] = sresp.structured is not None
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--max-output-tokens", type=int, default=32)
    args = parser.parse_args(argv)
    report = probe(runs=args.runs, max_output_tokens=args.max_output_tokens)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0  # clean exit whether or not an endpoint was configured


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    sys.exit(main())
