"""LIVE-WIRE — the gate is the ONLY live path (Phase 2 · GO LIVE, wire half).

Phase 1 (checks/396) armed the wire: ungated urlopen raises at http/https_open.
But the engines predated the gate — each carried a module-level bare-urlopen
`_urllib_transport` default, so flipping an engine live died with a DEEP guard
traceback, and there was no standard way to hand an engine a gated transport.
This check proves the Phase-2 sweep closed that, four ways:

  (a) FAIL CLOSED, LEGIBLY: every swept engine default — and an engine driven
      live with `transport=None` — refuses with `live_wire.NoGatedTransport`
      (an `EgressDenied`), whose message NAMES the sanctioned path
      (`live_wire.gated_transport(k, agent_cell, cap_id)`), before any socket;
      constructing an adapter WITHOUT a granted egress capability refuses the
      same way (no ambient authority) — construction or first use, never deep;
  (b) THE FULL LIVE CONSTRUCTION WORKS, OFFLINE: three low-liability READ
      flagships — shipping rate quote (canonical POST seam), weather reading
      (2-arg GET seam), sms delivery status (per-call-verb seam) — run the
      complete path end-to-end: CRED1 broker handle (key applied inside, never
      disclosed) + a gated transport from a granted, Morta-approved egress cap
      → engine call → `wire_decision` ALLOW Cell on the Weft BEFORE the (fake,
      injected at the wire's socket seam) open runs → the engine's DATA cell;
      an unapproved cap is refused before the socket (Morta), and stripe's
      TEST-MODE invariant still refuses a non-`sk_test_` key even through a
      properly gated transport (domain invariants survive the sweep);
  (c) THE SEAM TIER TOO: `mcp.http_transport` and `agent.live_engine_fn`
      require the same gated transport — without one they fail closed with the
      clear error; with one they work through the gate;
  (d) NO BYPASS REMAINS: an AST audit (isolation.py's import-time-audit idiom)
      over EVERY decima/*.py proves no module references `urlopen` at all —
      no engine retains a bare socket default reachable on the live path — and
      every swept default, called, raises the fail-closed refusal.

Entirely OFFLINE: the fake replaces the SOCKET the gate performs (`_open`, the
same seam wire.real_transport exposes) — the rule of egress runs first, every
time. Contract: run(k, line). Fail loud via assert. Owns a fresh Kernel.
"""
import ast
import importlib
import inspect
import os
import pathlib
import tempfile

from decima.kernel import Kernel
from decima import (agent as agent_mod, egress, live_wire, mcp, secrets, shipping,
                    sms, stripe_rail, weather_engine, wire)

CARRIER_KEY = "shippo_test_SECRET_414"
WEATHER_KEY = "owm_SECRET_414"
SMS_AUTH = "AC_414:AUTHTOKEN_SECRET_414"
STRIPE_TEST_KEY = "sk_test_414_abc"

SHIP_URL = "https://api.carrier.example/v1/rates"
WEATHER_URL = "https://api.weather.example/data/2.5/weather"
SMS_STATUS_URL = "https://api.twilio.example/2010-04-01/Messages/SM_414.json"
RPC_URL = "https://rpc.mcp.example/rpc"

ALLOWLIST = ["api.carrier.example", "api.weather.example", "api.twilio.example",
             "api.stripe.com", "rpc.mcp.example", "api.anthropic.com"]

# The engine modules the Phase-2 sweep gutted: (module, default-transport attr).
SWEPT = ([(m, "_urllib_transport") for m in
          ("accounting", "ads", "background_check", "banking", "brokerage_engine",
           "calendar_engine", "cloud_compute", "cloud_storage", "comms",
           "crm_engine", "dns", "ecommerce", "embed_engine", "esign", "exchange",
           "insurance_claim", "kyc", "maps_engine", "ocr_engine", "oidc",
           "paging", "payouts", "payroll", "ride", "shipping", "sms",
           "stripe_rail", "tax_engine", "ticketing", "translate_engine",
           "weather_engine")]
         + [("storage", "_urllib_put"), ("storage", "_urllib_get"),
            ("esign", "_urllib_get_transport")])


def _agent(kk):
    """A FRESH decima agent cell (its envelope/lease state advances on the Weave)."""
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== LIVE-WIRE (Phase 2 · GO LIVE — the gate is the ONLY live path) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)

    # ── (a) every default fails CLOSED with the sanctioned path NAMED ────────
    try:
        stripe_rail._urllib_transport("https://api.stripe.com/x", {}, "{}")
        raise AssertionError("a swept engine default must refuse")
    except live_wire.NoGatedTransport as e:
        assert isinstance(e, wire.EgressDenied), "the refusal is an egress denial"
        assert "live_wire.gated_transport" in str(e) and "stripe_rail" in str(e), e
        assert "urlopen default is not a path to the network" in str(e), e
    # an engine driven LIVE with transport=None: the refusal (not a deep guard
    # traceback) surfaces through the engine's own error mapping, path still named.
    try:
        stripe_rail.charge(STRIPE_TEST_KEY,
                           {"amount": 100, "payee": "x", "idempotency_key": "i-1"},
                           transport=None)
        raise AssertionError("charge with transport=None must fail closed")
    except Exception as e:
        assert "live_wire.gated_transport" in str(e), e
    line("  (a) transport=None is a dead end: NoGatedTransport (an EgressDenied) "
         "naming live_wire.gated_transport — before any socket ✓")

    # ── (a) adapters REQUIRE a granted egress capability to construct ────────
    for bad in (None, ""):
        try:
            live_wire.gated_transport(kk, _agent(kk), bad)
            raise AssertionError("no cap id → construction must refuse")
        except live_wire.NoGatedTransport as e:
            assert "granted egress capability" in str(e), e
    non_cap = kk.decima_agent_id                     # a cell that is NOT a capability
    try:
        live_wire.gated_method_transport(kk, _agent(kk), non_cap)
        raise AssertionError("a non-capability cell must not construct a transport")
    except live_wire.NoGatedTransport as e:
        assert "not a live egress capability" in str(e), e
    line("  (a) no grant → no transport: construction refuses (no ambient authority) ✓")

    # ── the egress grant + CRED1 broker for the flagship READ engines ────────
    cap_id, _hosts = egress.install(kk, allowlist=ALLOWLIST)
    broker = secrets.SecretsBroker(kk)
    broker.store("carrier", CARRIER_KEY, service="carrier")
    broker.store("weather", WEATHER_KEY, service="weather")
    broker.store("twilio", SMS_AUTH, service="twilio")
    h_ship = broker.issue("carrier", _agent(kk), "quote shipping rates")
    h_wthr = broker.issue("weather", _agent(kk), "read the weather")
    h_sms = broker.issue("twilio", _agent(kk), "read delivery status")

    # ── the fake WIRE: replaces the SOCKET the gate performs, never the gate ──
    calls = []
    canned = {
        "api.carrier.example": (200, {"rates": [
            {"carrier": "usps", "service": "priority", "amount_cents": 875,
             "rate_id": "rate_414"}], "shipment_id": "shp_414"}),
        "api.weather.example": (200, {"main": {"temp": 21.5, "humidity": 40},
                                      "wind": {"speed": 3.0}, "pop": 0,
                                      "weather": [{"main": "Clouds"}],
                                      "dt": 1700000000, "id": 414}),
        "api.twilio.example": (200, {"sid": "SM_414", "status": "delivered"}),
        "api.stripe.com": (200, {"id": "pi_414", "status": "succeeded"}),
        "rpc.mcp.example": (200, {"jsonrpc": "2.0", "id": 1, "result": {"pong": True}}),
        "api.anthropic.com": (200, {"content": [
            {"type": "text", "text": "hello from the gated wire"}]}),
    }

    def fake_open(url, headers, body, method, timeout):
        # provenance-before-socket: the ALLOW decision is already on the Weft —
        # recorded REDACTED (scheme://host/path): a key riding the query string
        # (weather's appid) must never land on the Weft (CRED1).
        recorded = url.split("?", 1)[0]
        assert any(c.content.get("decision") == wire.ALLOW
                   and c.content.get("url") == recorded
                   for c in kk.weave().of_type(wire.WIRE_DECISION)), \
            "the wire_decision ALLOW Cell must land BEFORE the socket layer runs"
        host = url.split("/")[2]
        calls.append({"url": url, "headers": dict(headers or {}), "method": method})
        return canned[host]

    a = _agent(kk)                                   # envelope now holds the grant
    t_post = live_wire.gated_transport(kk, a, cap_id, _open=fake_open)
    t_get2 = live_wire.gated_get_transport(kk, a, cap_id, _open=fake_open)
    t_verb = live_wire.gated_method_transport(kk, a, cap_id, _open=fake_open)
    assert all(getattr(t, "wire_gated", False) for t in (t_post, t_get2, t_verb)), \
        "every adapter shape must be a wire-gated transport"

    # ── (b) Morta first: constructed, granted — but UNAPPROVED → no socket ───
    q = shipping.quote(kk, endpoint=SHIP_URL, request={"to_address": "a", "weight": 1},
                       credential_handle=h_ship, broker=broker, agent_cell=a,
                       transport=t_post)
    assert "denied" in q and "approval" in q["denied"], q
    assert calls == [], "an unapproved egress cap must NEVER reach the socket"
    line("  (b) Morta: granted + gated but unapproved → refused before the wire, "
         "recorded, fail closed ✓")

    kk.approve(cap_id)                               # the human says yes

    # ── (b) flagship 1: shipping rate quote (canonical POST seam) ────────────
    q = shipping.quote(kk, endpoint=SHIP_URL, request={"to_address": "a", "weight": 1},
                       credential_handle=h_ship, broker=broker, agent_cell=a,
                       transport=t_post)
    assert q.get("rates") and q["rates"][0]["amount_cents"] == 875, q
    assert calls[-1]["method"] == "POST" and CARRIER_KEY in calls[-1]["headers"]["Authorization"], \
        "the broker applies the key INSIDE the gated call (dispense, don't disclose)"

    # ── (b) flagship 2: weather reading (2-arg GET seam) → DATA cell ─────────
    r = weather_engine.reading(kk, endpoint=WEATHER_URL, location={"place": "delphi"},
                               credential_handle=h_wthr, broker=broker,
                               agent_cell=a, transport=t_get2)
    assert r.get("weather_reading") and r["temp_dc"] == 215, r
    cell = kk.weave().get(r["weather_reading"])
    assert cell.content["instruction_eligible"] is False, "a reading is DATA"
    assert calls[-1]["method"] == "GET" and WEATHER_KEY in calls[-1]["url"]

    # ── (b) flagship 3: sms delivery status (per-call-verb seam) → DATA cell ─
    s = sms.delivery_status(kk, endpoint=SMS_STATUS_URL, provider_ref="SM_414",
                            credential_handle=h_sms, broker=broker,
                            agent_cell=a, transport=t_verb)
    assert s.get("sms_status") and s["status"] == "SUCCEEDED", s
    assert calls[-1]["method"] == "GET", calls[-1]

    allows = [c for c in kk.weave().of_type(wire.WIRE_DECISION)
              if c.content.get("decision") == wire.ALLOW]
    assert len(allows) == 3 == len(calls), (len(allows), len(calls))
    assert {c.content["host"] for c in allows} == \
        {"api.carrier.example", "api.weather.example", "api.twilio.example"}
    assert all(c.content["capability"] == cap_id for c in allows)
    # the raw secrets never land on the Weft — CRED1 holds through the gate.
    for c in kk.weave().cells.values():
        blob = str(c.content)
        for secret in (CARRIER_KEY, WEATHER_KEY, SMS_AUTH):
            assert secret not in blob, f"secret leaked onto the Weft in {c.type}"
    line("  (b) flagships LIVE end-to-end, offline: quote (POST seam) · weather "
         "(2-arg GET seam) · sms status (verb seam) — grant → Morta approval → "
         "gated transport → wire_decision ALLOW before the socket → DATA cell; "
         "keys applied inside the broker, never on the Weft ✓")

    # ── (b) stripe's TEST-MODE invariant survives the gated transport ────────
    n = len(calls)
    try:
        stripe_rail.charge("sk_live_EVIL", {"amount": 100, "payee": "x",
                                            "idempotency_key": "i-2"}, transport=t_post)
        raise AssertionError("a non-test key must be refused even through the gate")
    except Exception as e:
        assert "non-test key" in str(e), e
    assert len(calls) == n, "the refused live key must never reach the wire"
    ok = stripe_rail.charge(STRIPE_TEST_KEY, {"amount": 100, "payee": "acct",
                                              "idempotency_key": "i-3"},
                            transport=t_post)
    assert ok["provider_ref"] == "pi_414" and len(calls) == n + 1, ok
    line("  (b) stripe TEST-MODE invariant holds THROUGH the gate: sk_live_ refused "
         "before any request; sk_test_ charges via the gated wire ✓")

    # ── (c) the seam tier: mcp client + the live engine fn ───────────────────
    mtr = mcp.http_transport(RPC_URL)                # constructs (URL guard runs)…
    try:
        mtr({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        raise AssertionError("mcp http without a gated transport must fail closed")
    except live_wire.NoGatedTransport as e:
        assert "mcp.http_transport" in str(e) and "live_wire.gated_transport" in str(e), e
    resp = mcp.http_transport(RPC_URL, wire_transport=t_post)(
        {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp.get("result") == {"pong": True}, resp
    try:
        agent_mod.live_engine_fn("api-key-x")
        raise AssertionError("live_engine_fn without a gated transport must fail closed")
    except live_wire.NoGatedTransport as e:
        assert "agent.live_engine_fn" in str(e), e
    fn = agent_mod.live_engine_fn("api-key-x", transport=t_post)
    assert fn("say hi", None, "claude-opus-4-8", "frontier") == "hello from the gated wire"
    line("  (c) mcp.http_transport and agent.live_engine_fn ride the SAME gate: "
         "no gated transport → fail closed; with one → through the wire ✓")

    # ── (d) NO BYPASS: no decima module references urlopen AT ALL ────────────
    decima_dir = pathlib.Path(wire.__file__).parent
    offenders = []
    for path in sorted(decima_dir.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and node.attr == "urlopen") \
               or (isinstance(node, ast.Name) and node.id == "urlopen") \
               or (isinstance(node, ast.ImportFrom)
                   and (node.module or "").startswith("urllib")
                   and any(al.name == "urlopen" for al in node.names)):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"bare urlopen paths remain in decima/: {offenders}"
    # …and every swept default, CALLED, raises the fail-closed refusal.
    for mod_name, attr in SWEPT:
        fn = getattr(importlib.import_module(f"decima.{mod_name}"), attr)
        n_pos = sum(1 for p in inspect.signature(fn).parameters.values()
                    if p.default is inspect.Parameter.empty)
        try:
            fn(*["https://x.example/", {}, "{}"][:n_pos])
            raise AssertionError(f"{mod_name}.{attr} must refuse (fail closed)")
        except live_wire.NoGatedTransport:
            pass
    line(f"  (d) no bypass: AST audit over decima/*.py finds ZERO urlopen references, "
         f"and all {len(SWEPT)} swept engine defaults raise the fail-closed refusal ✓")
    line("  → the egress gate is the ONLY live path: defaults refuse legibly, adapters "
         "demand the grant, the full live construction is proven offline, and the "
         "domain invariants ride through it unchanged.")
