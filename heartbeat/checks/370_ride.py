"""Real ride / delivery dispatch rail — a REAL external engine, wrapped (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — requesting a ride/delivery SPENDS MONEY and dispatches a real-world action
(a car rolls, a courier is sent), so re-rolling it is the liability. A dispatcher is an
HTTPS API, so the real engine rides stdlib `urllib` (zero pip deps). This check drives the
rail entirely OFFLINE via an injected fake transport (the real `urllib` transport is never
called), so the oracle stays deterministic and network-free while proving the full
contract:

  - Morta-gated: unapproved → denied, and NO dispatch request is made before approval;
  - success: dispatched → SUCCEEDED receipt carrying the `provider_ref` (trip id), the
    `eta_min`, FINANCIAL class, and the idempotency key sent as the Idempotency-Key header;
    the running spend cap is decremented by the fare;
  - idempotent replay: the same key returns the prior receipt and makes NO second call;
  - no drivers / 4xx → FAILED (no ride dispatched, no money moved);
  - network error / timeout → UNKNOWN (outcome unobservable — never fabricated);
  - TEST-MODE invariant: a non-`rt_test_` (live) key is refused BEFORE any request;
  - dispense-don't-disclose: the raw dispatcher key never appears on the Weft (receipts,
    audits, any event) — CRED1 applies it inside the broker;
  - the engine registers a discoverable manifest (name "ride", EFFECT, FINANCIAL).

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import ride, secrets, payments, manifest

TEST_KEY = "rt_test_DECIMA123"
LIVE_KEY = "rt_live_DANGER"
ENDPOINT = "https://api.example-dispatch.test/v1/rides"


def _transport(calls, response):
    """A fake dispatch transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def _decima(kk):
    """A FRESH agent cell (re-read before each pay/invoke — the cell is re-asserted as
    state mutates, so a stale handle must never be reused)."""
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== REAL RIDE / DELIVERY DISPATCH RAIL (wrapped engine, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("ride", TEST_KEY, service="ride")
    handle = broker.issue("ride", _decima(kk), "dispatch rides")

    # 1. SUCCESS + Morta gate + provider_ref + eta + idempotency header + spend cap. ────
    calls = []
    cap = ride.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=_decima(kk), credential_handle=handle,
        name="ride_ok", endpoint=ENDPOINT,
        transport=_transport(calls, (201, {"id": "trip_1", "status": "dispatched",
                                            "eta_min": 4, "fare": 1800})))
    # Morta: no approval yet → denied, and NO dispatch request made.
    denied = payments.pay(kk, _decima(kk), cap, amount=1800, payee="123 Main St", idempotency_key="ride-1")
    assert denied.get("denied") and "approval" in denied["denied"], denied
    assert calls == [], "no dispatch request may be made before Morta approval"
    kk.approve(cap)
    spent_before = kk.spent.get(kk.decima_agent_id, 0)
    ok = payments.pay(kk, _decima(kk), cap, amount=1800, payee="123 Main St", idempotency_key="ride-1")
    assert ok["status"] == "SUCCEEDED", ok
    rc = kk.weave().get(ok["result_cell"]).content
    assert rc["provider_ref"] == "trip_1" and rc["rail"] == "ride" and rc["effect_class"] == "FINANCIAL", rc
    assert rc["eta_min"] == 4 and isinstance(rc["eta_min"], int), rc
    assert rc["fare"] == 1800 and rc["dropoff"] == "123 Main St", rc
    assert len(calls) == 1 and calls[0]["headers"]["Idempotency-Key"] == "ride-1", calls
    spent_after = kk.spent.get(kk.decima_agent_id, 0)
    assert spent_after == spent_before + 1800, ("spend cap must be decremented by the fare", spent_before, spent_after)
    line("  success: Morta-gated (no request pre-approval) → SUCCEEDED receipt with "
         "provider_ref (trip id) + eta_min; idempotency key sent as the header; spend cap decremented ✓")

    # 2. IDEMPOTENT REPLAY — same key returns the prior receipt, NO second dispatch. ────
    before = len(calls)
    again = payments.pay(kk, _decima(kk), cap, amount=1800, payee="123 Main St", idempotency_key="ride-1")
    assert again["idempotent_replay"] is True and again["result_cell"] == ok["result_cell"], again
    assert len(calls) == before, "a replay must not make a second dispatch"
    line("  idempotent replay: same key → prior receipt, no second dispatch ✓")

    # 3. NO DRIVERS / 4xx → FAILED (no ride dispatched). ───────────────────────────────
    dcalls = []
    cap_d = ride.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=_decima(kk), credential_handle=handle,
        name="ride_nodriver", endpoint=ENDPOINT,
        transport=_transport(dcalls, (409, {"error": {"message": "no drivers available"}})))
    kk.approve(cap_d)
    dec = payments.pay(kk, _decima(kk), cap_d, amount=1500, payee="9 Elm Ave", idempotency_key="ride-2")
    assert dec["status"] == "FAILED", dec
    line("  no drivers (4xx) → FAILED receipt — a definite no-effect (no ride, no money moved) ✓")

    # 4. NETWORK ERROR / TIMEOUT → UNKNOWN (outcome unobservable). ─────────────────────
    def boom():
        raise TimeoutError("connection timed out")
    tcalls = []
    cap_t = ride.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=_decima(kk), credential_handle=handle,
        name="ride_timeout", endpoint=ENDPOINT, transport=_transport(tcalls, boom))
    kk.approve(cap_t)
    unk = payments.pay(kk, _decima(kk), cap_t, amount=1700, payee="5 Oak Rd", idempotency_key="ride-3")
    assert unk["status"] == "UNKNOWN", unk
    line("  timeout → UNKNOWN receipt — outcome unobservable, never fabricated ✓")

    # 5. TEST-MODE invariant — a live key is refused BEFORE any request. ──────────────
    broker.store("ride_live", LIVE_KEY, service="ride")
    handle_live = broker.issue("ride_live", _decima(kk), "dispatch rides")
    lcalls = []
    cap_l = ride.install_rail(
        kk, cap=1_000_000, broker=broker, agent_cell=_decima(kk), credential_handle=handle_live,
        name="ride_live", endpoint=ENDPOINT,
        transport=_transport(lcalls, (201, {"id": "trip_x", "status": "dispatched", "eta_min": 2, "fare": 100})),
        test_mode=True)
    kk.approve(cap_l)
    refused = payments.pay(kk, _decima(kk), cap_l, amount=100, payee="X", idempotency_key="ride-4")
    assert refused["status"] == "FAILED", refused
    assert lcalls == [], "a live key must be refused before any dispatch request is made"
    line("  test-mode: a live (rt_live_) key is refused before any request — no real "
         "car/courier can be dispatched from the reference ✓")

    # 6. DISPENSE-DON'T-DISCLOSE — the raw key never appears anywhere on the Weft. ─────
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert TEST_KEY not in all_payloads and LIVE_KEY not in all_payloads, \
        "a raw dispatcher key must never be written to the Weft (dispense-don't-disclose)"
    line("  no raw dispatcher key on the Weft — CRED1 applies it inside the broker ✓")

    # 7. DISCOVERABLE MANIFEST — the engine registers a findable manifest. ─────────────
    ride.register_manifest(kk)
    m = manifest.get(kk, "ride")
    assert m is not None, "ride manifest must be registered"
    assert m.content["archetype"] == "EFFECT" and m.content["effect_class"] == "FINANCIAL", m.content
    assert m.content["caveats"].get("requires_approval") is True, m.content
    assert set(["transport", "delivery", "dispatch"]).issubset(set(m.content["tags"])), m.content
    found = manifest.find(kk, query="delivery dispatch")
    assert any(c.content["name"] == "ride" for c in found), "ride must be discoverable by query"
    line("  manifest: 'ride' registered (EFFECT, FINANCIAL, requires_approval) and discoverable ✓")

    line("  → the real ride/delivery engine is wrapped over stdlib urllib (zero deps): Morta-"
         "gated, idempotent, receipts map dispatched/no-drivers/timeout → SUCCEEDED/FAILED/"
         "UNKNOWN with provider_ref + eta; test-mode-only; the key is never disclosed.")
