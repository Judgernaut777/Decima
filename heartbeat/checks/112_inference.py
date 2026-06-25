"""INF1 — self-hosted / private inference: the data never leaves.

Proves:
  - a SENSITIVE prompt routes to the LOCAL (on-host) engine and produces a
    completion with NO egress;
  - a network attempt by the local engine is REFUSED by the executor's sandbox
    (network=False) — the proof that the data cannot leave (SB1);
  - a NON-sensitive prompt may use the REMOTE engine (network allowed);
  - both engines plug into AR1's Router via its public engines seam.

The router confers ZERO authority — it only selects the engine; the sandbox is
what bounds egress. Real engines (llama.cpp / vLLM on your GPU; a hosted API) slot
in behind the same contract. Contract: run(k, line). Fail loud.
"""
from decima import inference, executor
from decima.router import Router, LOCAL_SMALL, FRONTIER


def run(k, line):
    line("\n== PRIVATE INFERENCE (self-hosted; sensitive data never leaves) ==")
    rt = inference.inference_router()

    # both engines plugged into AR1's Router via the public seam (Router(engines=…))
    assert isinstance(rt, Router)
    assert isinstance(rt.engines[LOCAL_SMALL], inference.LocalInferenceEngine)
    assert isinstance(rt.engines[FRONTIER], inference.RemoteInferenceEngine)

    # 1. SENSITIVE prompt → local engine, completion produced, no egress.
    s = inference.private_infer(k, "summarize my private medical notes", sensitive=True, router=rt)
    line(f"  sensitive → tier={s['tier']} engine={s['engine']} egress={s['egress']}")
    line(f"    out: {s['output']}")
    assert s["tier"] == LOCAL_SMALL and s["engine"] == "local" and s["egress"] is False, s
    rec = k.weave().get(s["record"])           # audited on the Weft
    assert rec.content["engine"] == "local" and rec.content["egress"] is False

    # 2. The local engine tries to reach the network → REFUSED by the sandbox.
    local_engine = rt.engines[LOCAL_SMALL]
    try:
        local_engine.attempt_egress("the user's private prompt")
        assert False, "egress was NOT refused — data could leave!"
    except executor.SandboxViolation as e:
        line(f"  local engine network attempt → ✋ sandbox-refused: {e}")

    # 3. NON-sensitive prompt → remote engine (network allowed).
    n = inference.private_infer(k, "what's a fun fact about the moon", sensitive=False, router=rt)
    line(f"  non-sensitive → tier={n['tier']} engine={n['engine']} egress={n['egress']}")
    assert n["engine"] == "remote" and n["egress"] is True, n

    # the remote engine genuinely uses a network-needing effect (allowed by its profile)
    assert "remote" in n["output"]

    line("  → sensitive work stays on-host (no egress, sandbox-enforced); non-sensitive "
         "may use a hosted API. Real engines: deferred behind the same contract.")
