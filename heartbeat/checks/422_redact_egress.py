"""REDACTION EGRESS GATE wired onto the LIVE loop — a secret never leaves to the model.

Cycle 50 built `decima/redact.py` (the fail-closed scrubber upstream of the router) but
nothing on the live path called it: `ModelBrain._post` shipped the whole turn — system
prompt + every message — to an EXTERNAL provider with ZERO outbound secret screening. So a
turn like "debug this: sk-live… postgres://user:pass@host" would have carried a live
credential straight onto the socket. This check proves the wiring closes that gap, entirely
offline (a fresh Kernel, an injected transport SPY, no network, no key):

  (a) FAIL CLOSED — a turn whose text carries a raw high-value secret (api key / DB URL)
      is BLOCKED before the socket: the injected transport is NEVER called, and `decide()`
      falls back to the deterministic RuleBrain. The secret does not leave the device;
  (b) THE GATE IS SPECIFIC — calling `_post` directly on a secret-bearing body raises
      `redact.RedactionBlocked` BEFORE the transport, not some incidental failure;
  (c) CLEAN PASSES — an innocuous public turn is screened, found clean, and DOES reach the
      transport (the payload leaves), and the model's decision is used — the gate blocks
      secrets, it does not muzzle the brain;
  (d) PROVENANCE — when the secret turn is screened with an egress binding, a `redaction`
      Cell lands on the Weft recording CLASSES + COUNTS (ints) and NEVER the secret bytes;
  (e) REPO-SENSITIVE too — an internal-host / fs-path turn (repo_sensitive) is likewise
      held back from the external model (fail toward local), proving the block keys off the
      classification, not just literal keys.

Mutation-resistance (the load-bearing line): delete the `self._screen_egress(body)` call at
the top of `_post` and (a)+(b) go red — the secret body reaches the transport and no block
is raised.

Contract: run(k, line). Fail loud (assert / expected RedactionBlocked). Owns a fresh Kernel.
"""
import json
import os
import tempfile

from decima.kernel import Kernel
from decima.agent import ModelBrain, RuleBrain, held_capabilities
from decima import redact


def _tool_use(inp):
    """A canned Anthropic tool_use response carrying the `act` decision `inp`."""
    return {"content": [{"type": "tool_use", "name": "act", "input": inp}],
            "stop_reason": "tool_use"}


# Synthetic secrets — NOT real credentials; shaped to match redact's detectors.
_API_KEY = "sk-livedeadbeef0123456789ABCDEFghij"
_DB_URL = "postgres://admin:hunter2@db.prod:5432/customers"


def run(k, line):
    line("\n== REDACTION EGRESS GATE (live) — a secret-bearing turn never reaches the model ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    aid = kk.decima_agent_id

    def agent():
        return kk.weave().get(aid)

    assert "echo" in {c.content["name"] for c in held_capabilities(kk.weave(), agent())}, \
        "the default agent must hold 'echo' for the clean-turn control"

    # A transport SPY: it records every call and, if reached, answers with a benign respond.
    # If it is EVER called on a secret turn, the gate failed.
    calls = []

    def spy(url, headers, body, *rest):
        payload = json.loads(body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body)
        calls.append(payload)
        return 200, _tool_use({"action": "respond", "text": "ok", "reasoning": "seen"})

    # Bind an egress carrying the kernel so provenance can be recorded (the binding's
    # transport is unused here because we inject `spy` directly — but its `k` is read for
    # the redaction record).
    brain = ModelBrain("k-fake", transport=spy).bind_egress(kk, agent(), "cap-unused")

    # ── (a) FAIL CLOSED — a secret turn is blocked before the socket; decide → RuleBrain.
    secret_turn = f"debug prod please: api key {_API_KEY} and db {_DB_URL}"
    calls.clear()
    act = brain.decide(secret_turn, kk.weave(), agent())
    assert calls == [], "a secret-bearing turn REACHED the transport — the egress gate failed"
    # decide fell back to the deterministic RuleBrain: its decision equals RuleBrain's own.
    rb = RuleBrain().decide(secret_turn, kk.weave(), agent())
    assert act.kind == rb.kind, \
        f"a blocked live call must fall back to RuleBrain's decision (got {act.kind}, want {rb.kind})"
    line("  fail closed: a turn carrying a raw api-key + DB-URL never reaches the transport "
         "(0 calls) — decide() falls back to the offline RuleBrain ✓")

    # ── (b) THE GATE IS SPECIFIC — _post on a secret body raises RedactionBlocked, no socket.
    secret_body = {"model": "m", "max_tokens": 8, "system": "You are Decima.",
                   "messages": [{"role": "user", "content": secret_turn}]}
    calls.clear()
    try:
        brain._post(secret_body)
        raise AssertionError("_post shipped a secret payload (no RedactionBlocked raised)")
    except redact.RedactionBlocked as e:
        assert e.classification == redact.SECRET_SENSITIVE, e.classification
    assert calls == [], "the transport ran despite a RedactionBlocked — the gate is upstream of the socket"
    line("  specific: _post(secret_body) raises RedactionBlocked (secret_sensitive) BEFORE "
         "the transport — the block is the gate, not an incidental failure ✓")

    # ── (c) CLEAN PASSES — a public turn is screened clean and DOES reach the transport.
    calls.clear()
    clean = "echo hello team"                      # a benign, held-capability turn
    _ = brain.decide(clean, kk.weave(), agent())
    assert len(calls) == 1, "a clean public turn must reach the transport exactly once (the gate is not a muzzle)"
    convo = "\n".join(m.get("content", "") for m in calls[0]["messages"])
    assert "echo hello team" in convo, "the clean turn's text must have been sent to the model"
    for raw in (_API_KEY, _DB_URL, "hunter2"):
        assert raw not in json.dumps(calls[0]), "no secret bytes may appear in a sent payload"
    line("  clean passes: an innocuous public turn is screened clean and reaches the "
         "transport once — the gate blocks secrets, it does not muzzle the brain ✓")

    # ── (d) PROVENANCE — the secret screening recorded a redaction Cell (classes+counts).
    reds = kk.weave().of_type(redact.REDACTION)
    assert reds, "screening a secret turn with an egress binding must record a redaction Cell"
    body = reds[-1].content
    assert body["classification"] == redact.SECRET_SENSITIVE
    assert isinstance(body["total"], int) and all(isinstance(v, int) for v in body["counts"].values())
    blob = json.dumps([r.content for r in reds])
    for raw in (_API_KEY, _DB_URL, "hunter2", "livedeadbeef0123456789ABCDEFghij"):
        assert raw not in blob, f"a redaction record leaked a secret value: {raw!r}"
    line(f"  provenance: a redaction Cell records classes+counts (total={body['total']}, "
         f"ints) with NO secret bytes — the leak is auditable, the secret is not stored ✓")

    # ── (e) REPO-SENSITIVE too — an infra turn is held back from the external model.
    calls.clear()
    repo_turn = "tail the log on worker-7.internal at /var/lib/decima/state.db"
    act2 = brain.decide(repo_turn, kk.weave(), agent())
    assert calls == [], "a repo_sensitive (internal host / fs path) turn reached the external model"
    assert act2.kind == RuleBrain().decide(repo_turn, kk.weave(), agent()).kind, \
        "a repo_sensitive turn must fall back to local (RuleBrain) handling"
    line("  repo-sensitive: an internal-host + fs-path turn is held back from the external "
         "model and handled locally — the block keys off the classification, not just keys ✓")

    line("  → Cycle 50's redaction is now LIVE on the say/ModelBrain loop: every outbound "
         "payload is screened before the socket, a secret / infra turn fails CLOSED to the "
         "offline RuleBrain, and only classes+counts (never the secret) reach the Weft.")
