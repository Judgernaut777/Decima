"""LIVE CODEGEN + THE ENGINE CONSUMER — P3 self-extension is REAL when keyed.

The 4th-quality re-audit ruled P3 red at its heart on two CODE gaps (not operator
gates), both on the running path:

  * `candidate.model_codegen` raised `CodegenUnavailable` UNCONDITIONALLY — the
    "live post" was a comment, not code — and nothing ever handed the golive-bound
    brain to it, so a keyed + granted system STILL could not author source;
  * `golive.activate_engine` recorded a wire-gated transport into
    `k.live_engines` but NOTHING ever handed that transport to an engine module
    fn — zero consumers; the flip was doctor decoration.

This check is the adversarial detector for the closure — entirely OFFLINE and
deterministic (injected stub brains / socket seams, fresh Kernels, no wall clock,
no real key, no network):

  (a) LIVE CODEGEN REAL (load-bearing) — with an injected egress-bound stub brain
      (the wrapped-engine idiom), `model_codegen` POSTS the intent through the
      brain's `_post` and returns the model's source text as DATA — the returned
      source IS the stub's marker (provably from the transport: the marker appears
      nowhere in candidate.py's own source). With NO brain, a keyless ModelBrain,
      or a sourceless reply, it FAILS CLOSED (`CodegenUnavailable`) — never a
      fabricated source.
  (b) BOOT ARMS IT — after the PRODUCTION boot shape (`golive.boot(k)`, environ
      None, exactly run.py's call) with a key intaken and a human-approved
      api.anthropic.com grant: discovery's default codegen is BOUND (live), the
      builtin catalog is NON-EMPTY (register_builtins ran: a production-shaped
      discover() 'use's stripe_rail for a card-charge goal instead of falling to
      an empty registry), the bound codegen FAILS CLOSED offline (the strategy
      meter denies the paid lane before any socket), and — with the socket seam
      injected — a production-shaped discover() (NO forge=) forges a REAL
      promoted organ whose recorded source carries the transport marker and whose
      ocap-gated invoke runs the generated behavior. A rehearsal boot (injected
      environ) binds NO process-global codegen.
  (c) ENGINE CONSUMER (load-bearing) — after `activate_engine` behind an approved
      grant with an injected socket seam, `k.invoke(<the approved capability>)`
      actually drives the REAL engine entry fn (shipping.buy_label) over the
      REGISTERED wire-gated transport: the socket seam fires, the wire_decision
      ALLOW provenance names the grant, the broker credential is APPLIED on the
      wire but never durable, and the receipt keeps the provider's reply as DATA.
      A pruned registry entry and a Morta-REVOKED grant both fail an invoke
      CLOSED — the socket seam never fires again.

Mutation-resistance (the load-bearing lines): revert model_codegen's live post
(`data = brain._post(body)`) to the unconditional raise → (a) and (b) go RED; or
revert activate_engine's consumer install (`_install_consumer(...)`) to a no-op →
(c) goes RED (the invoke answers with the canned gated-fetch body, the engine fn
never runs, the socket seam never fires with the carrier payload).

Contract: run(k, line). Fail loud via assert. Owns fresh, offline Kernels.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import candidate as C
from decima import discovery as D
from decima import golive, wire
from decima import manifest as M
from decima import builtin_manifests as B
from decima import forge as F
from decima.agent import ModelBrain
from decima.inbox import ApprovalInbox

# Markers/sentinels no legitimate content could contain. The MARKER proves the
# source came through the injected transport, not from any hardcoded stub.
MARKER = "lcg494_transport_marker_5e8d13c0a97f"
MARKER_SOURCE = (
    "def marked(text):\n"
    "    \"\"\"" + MARKER + "\"\"\"\n"
    "    return str(text)\n"
)
SENTINEL_KEY = "sk-ant-lcg494-SENTINEL-b41f7e2290cd83aa"
SHIP_SECRET = "shippo_test_lcg494_SENTINEL_77aa03d9"


def _fresh():
    return Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)


def _world_dump(kk) -> str:
    """EVERYTHING durable: every Weft event's payload and every folded Cell's
    content, repr'd — the haystack a sentinel must never appear in."""
    parts = [repr((ev.verb, ev.author, ev.body)) for ev in kk.weft.events()]
    parts += [repr((c.id, c.type, c.content)) for c in kk.weave().cells.values()]
    return "\n".join(parts)


class _StubBrain:
    """A deterministic egress-bound stub brain (the wrapped-engine idiom): its
    `_post` never opens a socket — it records the body and answers a canned
    Anthropic-shaped payload. `source=None` answers a tool_use-only payload
    (NO text blocks), the never-fabricate probe."""
    model = "stub-model-494"
    egress = ("stub-k", "stub-agent", "stub-cap")   # egress-bound marker

    def __init__(self, source):
        self.posted = []
        self._source = source

    def _post(self, body):
        self.posted.append(body)
        if self._source is None:
            return {"stop_reason": "tool_use",
                    "content": [{"type": "tool_use", "name": "act", "input": {}}]}
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": self._source}]}


def run(k, line):
    line("\n== LIVE CODEGEN + ENGINE CONSUMER (P3 real when keyed; fail closed offline) ==")

    # ── (a) LIVE CODEGEN REAL (load-bearing) ────────────────────────────────
    sb = _StubBrain(MARKER_SOURCE)
    src = C.model_codegen("author a marked text organ", brain=sb)
    assert src == MARKER_SOURCE.strip() and MARKER in src, \
        "model_codegen must return the source the brain transport produced"
    assert len(sb.posted) == 1, "exactly one gated post must carry the intent"
    body = sb.posted[0]
    assert body["messages"][0]["content"] == "author a marked text organ", body
    assert isinstance(body["max_tokens"], int) and body["max_tokens"] > 0
    assert body["model"] == "stub-model-494", \
        "the codegen post must ride the INJECTED brain, not a fresh default"
    # the marker is NOT hardcoded anywhere in candidate.py — it truly crossed
    # the injected transport.
    import inspect
    assert MARKER not in inspect.getsource(C), \
        "the marker must come from the brain transport, never a hardcoded stub"
    # fail closed, never fabricate: no brain at all …
    try:
        C.model_codegen("anything")
        raise AssertionError("model_codegen with NO brain must fail closed")
    except C.CodegenUnavailable:
        pass
    # … a keyless, unbound ModelBrain (the production offline default) …
    try:
        C.model_codegen("anything", brain=ModelBrain(api_key=None))
        raise AssertionError("an unbound ModelBrain must fail closed")
    except C.CodegenUnavailable:
        pass
    # … and a brain whose reply carries NO source text (never fabricate).
    try:
        C.model_codegen("anything", brain=_StubBrain(None))
        raise AssertionError("a sourceless model reply must fail closed")
    except C.CodegenUnavailable:
        pass
    line("  (a) live codegen: the injected egress-bound brain's _post carries the "
         "intent and its reply IS the returned source (marker crossed the "
         "transport; not in candidate.py); no brain / unbound / sourceless all "
         "raise CodegenUnavailable — fail closed, never fabricated ✓")

    # ── (b) BOOT ARMS IT — the production boot shape binds the live seam ────
    kk = _fresh()
    res = golive.request_grant(kk, golive.BRAIN_HOST)
    assert res["status"] == "pending", res
    assert "ok" in ApprovalInbox(kk).approve(res["item"])    # the HUMAN decision
    kk.brain = ModelBrain(SENTINEL_KEY)          # what make_brain builds when keyed

    # a REHEARSAL boot (injected environ) must bind NO process-global codegen.
    probe = D.bind_default_codegen(None)
    D.bind_default_codegen(probe)                # snapshot without disturbing
    rehearsal = golive.boot(_fresh(), environ={"ANTHROPIC_API_KEY": SENTINEL_KEY})
    assert rehearsal, "a keyed rehearsal boot must announce"
    after_rehearsal = D.bind_default_codegen(None)
    D.bind_default_codegen(after_rehearsal)
    assert after_rehearsal is probe, \
        "an injected-environ (rehearsal) boot must NOT mutate the process-global " \
        "default-codegen seam"

    saved = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = SENTINEL_KEY
    try:
        boot_lines = golive.boot(kk)             # ← environ None: run.py's EXACT shape
    finally:
        if saved is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = saved
    try:
        assert boot_lines and SENTINEL_KEY not in repr(boot_lines), boot_lines
        assert kk.brain.egress is not None, "boot must bind the approved grant"
        assert any("codegen: LIVE" in ln for ln in boot_lines), boot_lines
        # discovery's default codegen IS the live one — bound, not None.
        bound = D.bind_default_codegen(None)
        D.bind_default_codegen(bound)            # restore immediately (public API)
        assert bound is not None and bound is not probe, \
            "the production boot must bind discovery's default codegen to the " \
            "live brain (bind_default_codegen gains a REAL production caller)"
        # the catalog is NON-EMPTY: register_builtins ran at boot, so a
        # production-shaped discover() USEs a real engine (never falls to empty).
        assert len(M.registry(kk)) >= len(B.BUILTINS), \
            "boot must register the builtin engine catalog (discovery non-empty)"
        used = D.discover(kk, "charge a customer's credit card",
                          threshold=kk.DISCOVERY_THRESHOLD)
        assert used["action"] == "use" and used["name"] == "stripe_rail", used
        # offline the LIVE binding fails CLOSED: the strategy meter (bound by the
        # same boot) denies the unbudgeted paid lane BEFORE any socket.
        try:
            bound("a probe intent")
            raise AssertionError("the live codegen must fail closed offline")
        except C.CodegenUnavailable:
            pass
        line("  (b1) boot armed it: rehearsal boots bind nothing; the PRODUCTION "
             "boot binds discovery's default codegen to the live brain, registers "
             f"the {len(B.BUILTINS)}-engine catalog (discover → use stripe_rail), "
             "and the bound codegen fails CLOSED offline (metered, no socket) ✓")

        # the wrapped-engine idiom: inject the socket-side transport and drive a
        # PRODUCTION-shaped discover (NO forge=, NO codegen bound by this check)
        # through the BOOT-bound live seam to a real promoted organ.
        calls = []
        gen_source = C.NORMALIZER_SOURCE + "\n# forged-via-live-brain " + MARKER + "\n"

        def stub_transport(url, headers, body):
            calls.append(url)
            return 200, {"stop_reason": "end_turn",
                         "content": [{"type": "text", "text": gen_source}]}

        kk.brain.transport = stub_transport      # the injected wire seam
        kk.brain.strategy = None                 # offline: lift the paid meter only
        goal = "chromatic sigil entropy weaver 494"
        forged = D.discover(kk, goal, threshold=kk.DISCOVERY_THRESHOLD)
        assert forged["action"] == "forged", forged
        assert forged.get("stub") is False and forged.get("promoted") is True, \
            f"the boot-bound live codegen must feed the REAL pipeline: {forged}"
        assert calls and calls[0].endswith("/v1/messages"), \
            "the codegen intent must have crossed the brain transport"
        cand = kk.weave().get(forged["candidate"])
        assert MARKER in cand.content["source_blobs"], \
            "the recorded candidate source must be the transport's (marker present)"
        agent = kk.weave().get(kk.decima_agent_id)
        r = kk.invoke(agent, forged["cap"], {"text": "  Hello   WORLD  "})
        assert r.get("status") == "SUCCEEDED" and r["ok"]["out"] == "hello world", r
        assert F.slug(goal) == forged["name"], forged
        assert SENTINEL_KEY not in _world_dump(kk), \
            "the live-armed boot + forge must leave zero secret bytes durable"
        line("  (b2) production discover() (NO forge=) through the BOOT-bound live "
             "codegen: the intent crossed the injected brain transport, the organ "
             "was born quarantined → evaluated → PROMOTED, its source carries the "
             "transport marker, and it runs ('hello world') ✓")
    finally:
        D.bind_default_codegen(probe)            # ALWAYS restore the process default

    # ── (c) ENGINE CONSUMER (load-bearing) — invoke drives the engine fn ────
    k2 = _fresh()
    golive.intake_env(k2, environ={"DECIMA_SECRET_SHIPPING": SHIP_SECRET})
    res = golive.request_grant(k2, "api.ship494.example")
    assert res["status"] == "pending", res
    assert "ok" in ApprovalInbox(k2).approve(res["item"])
    ecap = res["capability"]

    opened = []

    def fake_open(url, headers, body, method, timeout):   # the SOCKET seam only
        opened.append((url, dict(headers), body))
        return 200, {"status": "purchased", "object_id": "shp_494",
                     "tracking_code": "TRK494"}

    flip = golive.activate_engine(k2, "shipping", "api.ship494.example",
                                  _open=fake_open)
    assert flip["status"] == "live" and flip["capability"] == ecap, flip
    assert flip.get("consumer"), \
        "an approved flip of a bundled engine must INSTALL its consumer"
    agent = k2.weave().get(k2.decima_agent_id)
    args = {"endpoint": "https://api.ship494.example/v1/transactions",
            "amount": 700, "payee": "addr_494", "weight": 120,
            "idempotency_key": "lcg-494-1"}
    r = k2.invoke(agent, ecap, args)
    assert r.get("status") == "SUCCEEDED", \
        f"invoking the flipped engine capability must drive the engine fn: {r}"
    assert str(r["ok"].get("out", "")).startswith("bought"), \
        f"the REAL shipping.buy_label must have run (not the canned fetch): {r['ok']}"
    assert r["ok"]["tracking_code"] == "TRK494" and r["ok"]["rail"] == "shipping"
    assert r["ok"]["instruction_eligible"] is False and r["ok"]["untrusted"] is True, \
        "the provider's reply must be recorded as DATA"
    # the REGISTERED wire-gated transport was exercised: the socket seam fired
    # once and the wire ALLOW provenance names the approving grant.
    assert len(opened) == 1, "the engine call must ride the registered transport"
    url, headers, _b = opened[0]
    assert url == args["endpoint"], (url,)
    assert SHIP_SECRET in headers.get("Authorization", ""), \
        "the broker credential must be APPLIED on the wire (never disclosed)"
    allows = [c for c in k2.weave().of_type(wire.WIRE_DECISION)
              if c.content.get("decision") == wire.ALLOW]
    assert allows and allows[-1].content["capability"] == ecap \
        and allows[-1].content["host"] == "api.ship494.example", allows
    assert SHIP_SECRET not in _world_dump(k2), \
        "the applied credential must never land durable"
    receipt = k2.weave().get(r["result_cell"]).content
    assert receipt["instruction_eligible"] is False \
        and receipt["status"] == "SUCCEEDED", receipt

    # fail closed at CALL time: a pruned registry entry refuses BEFORE any wire …
    k2.live_engines.pop("shipping")
    r2 = k2.invoke(agent, ecap, dict(args, idempotency_key="lcg-494-2"))
    assert "denied" in r2 and "OFFLINE" in r2["denied"], r2
    assert len(opened) == 1, "a refused engine call must never reach the socket"
    # … and a Morta-REVOKED grant refuses the invoke itself (no socket, no fn).
    re_flip = golive.activate_engine(k2, "shipping", "api.ship494.example",
                                     _open=fake_open)
    assert re_flip["status"] == "live", re_flip
    k2.revoke(ecap)
    r3 = k2.invoke(agent, ecap, dict(args, idempotency_key="lcg-494-3"))
    assert "denied" in r3, r3
    assert len(opened) == 1, "a revoked grant must close the engine (fail closed)"
    line("  (c) engine consumer: k.invoke on the approved capability ran the REAL "
         "shipping.buy_label over the REGISTERED wire-gated transport (socket seam "
         "fired once, wire ALLOW names the grant, credential applied never "
         "durable, reply kept DATA); a pruned entry and a revoked grant both "
         "fail an invoke CLOSED with the socket untouched ✓")

    line("  → P3's red core is closed on the RUNNING path: model_codegen really "
         "POSTS through the egress-bound brain and fails closed otherwise; the "
         "production boot binds that live codegen into discovery and registers "
         "the builtin catalog (use-before-forge); and a flipped engine has a "
         "REAL consumer — invoking its approved capability drives the engine fn "
         "over its wire-gated transport, minting nothing, revocable everywhere.")
