"""SPEND + PROVIDER ROUTING wired onto the LIVE brain path — no unmetered model call.

Cycle 50 built the model-strategy plane — `provider_router.select` (WHICH provider
instance serves a turn: hard eligibility, then an additive integer score) and
`spend.SpendMeter` (WHAT it costs: budget, confirm-charge, quota, scorecards) — and
Cycle 52 wired ONLY the redaction stage onto `ModelBrain._post`. Provider selection
and spend metering were still exercised only by checks: a real live-path model call
was neither routed nor metered, so a live call would spend money while observ.metrics
reported spend 0. `ModelBrain.bind_strategy` + the `_route_and_meter` consult in
`_post` close that gap. This check proves it, entirely offline + deterministically
(fresh Kernels, injected transport SPIES, an injected meter/inbox/fleet, logical int
ticks, no network, no clock):

  (a) LIVE CALL IS METERED + ROUTED — a live-path dispatch consults the provider
      router (the paid lane is SELECTED, an unhealthy one REJECTED with an
      explainable reason, and the decision lands as a `provider_routing` Cell) AND
      the meter: the first paid turn is held as a confirm-charge (spending nothing),
      the human approves it through the Morta gate, and observ.metrics then reports
      the spend as a NON-ZERO int (was 0). The next turn rides that approved
      headroom: it reaches the transport exactly once and its `spend_dispatch`
      receipt (int tokens) lands BEFORE the socket; a third turn finds the headroom
      drawn down and is queued again — never an unaccounted call.
  (b) PAID OVER-BUDGET / UNCONFIGURED FAILS CLOSED — with NO budget configured a
      paid dispatch is rejected at eligibility (`StrategyDenied`, budget reason):
      nothing queued, nothing charged, the socket never reached; with a budget too
      small the confirm-charge is denied 'budget_exhausted' outright (still nothing
      queued, budget unchanged). Money moves ONLY when a human approves a queued
      charge — exactly as spend.py models it.
  (c) NO REGRESSION — the Cycle-52 redaction gate still fires FIRST (a secret turn
      is blocked before the strategy plane: no routing Cell, no queue, no socket);
      the RuleBrain fallback still engages on a transport failure (and the FREE-lane
      dispatch that failed is still receipted — conservative accounting); an
      UNBOUND brain behaves exactly as before (the offline/oracle configuration);
      a float provider metric is refused at the door (ints-not-floats).

Mutation-resistance (the load-bearing line): delete the
`self._route_and_meter(body, classification)` call in `ModelBrain._post` and (a)
goes RED — the first paid live call goes straight to the transport (unrouted,
unmetered), nothing is queued, no provider_routing / spend Cell lands, and the
reported spend stays 0.

Contract: run(k, line). Fail loud (assert / expected StrategyDenied). Owns fresh
Kernels; registers no effects (the meter's spend capability rides the kernel's
existing gated spine, as in checks/416_spend.py).
"""
import json
import os
import tempfile

from decima.kernel import Kernel
from decima.agent import ModelBrain, RuleBrain, StrategyDenied
from decima.inbox import ApprovalInbox
from decima.spend import SpendMeter, CHARGE, DISPATCH
from decima import observ, provider_router, redact


def _tool_use(inp):
    """A canned Anthropic tool_use response carrying the `act` decision `inp`."""
    return {"content": [{"type": "tool_use", "name": "act", "input": inp}],
            "stop_reason": "tool_use"}


def _fresh():
    """A fresh, isolated Kernel + spend meter + approval inbox over it."""
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    return kk, SpendMeter(kk), ApprovalInbox(kk)


def _spy():
    """An injected transport SPY: records every payload that reaches the socket seam
    and answers with a benign respond decision. If it fires when the strategy plane
    should have held the dispatch, the wiring failed."""
    calls = []

    def transport(url, headers, body, *rest):
        calls.append(json.loads(body.decode("utf-8")
                                if isinstance(body, (bytes, bytearray)) else body))
        return 200, _tool_use({"action": "respond", "text": "ok", "reasoning": "seen"})

    return calls, transport


# The injected fleet (shared live-status entries — every numeric an INT).
PAID = {"id": "frontier-paid", "tier": "frontier", "privacy_tier": "external_paid",
        "model": "paid-m", "cost_per_1k_microcents": 1000, "healthy": True,
        "quota_remaining": 50_000, "capacity": 16, "residency": "external",
        "scorecard": 0}
SICK = {"id": "sick", "tier": "frontier", "privacy_tier": "external",
        "model": "sick-m", "cost_per_1k_microcents": 0, "healthy": False,
        "quota_remaining": 50_000, "capacity": 16, "residency": "external",
        "scorecard": 0}
# NOTE: the FREE lane declares quota_remaining=0 statically — it is eligible ONLY
# because the METER holds a configured quota for it (the live overlay), proving the
# meter, not the static fleet claim, is the quota ground truth on the live path.
FREE = {"id": "free-lane", "tier": "local-small", "privacy_tier": "external",
        "model": "free-m", "cost_per_1k_microcents": 0, "healthy": True,
        "quota_remaining": 0, "capacity": 16, "residency": "external",
        "scorecard": 0}

TURN = "summarize the release notes"


def run(k, line):
    line("\n== SPEND + PROVIDER ROUTING on the LIVE brain path — no unmetered model call ==")

    # ── (a) LIVE CALL IS METERED + ROUTED (the load-bearing wiring). ───────────────
    kk, meter, inbox = _fresh()
    aid = kk.decima_agent_id

    def agent():
        return kk.weave().get(aid)

    meter.configure_budget(1_000_000)
    # Pre-mint the spend capability (through the kernel API; Morta still gates it)
    # so the held-capability catalog — hence the payload and its token estimate —
    # is byte-stable across the turns below (deterministic).
    spend_cap = meter.mint_spend_capability(agent(), "frontier-paid")
    calls, spy = _spy()
    brain = ModelBrain("k-fake", transport=spy).bind_strategy(
        meter, inbox, agent(), providers=[PAID, SICK],
        spend_caps={"frontier-paid": spend_cap})

    assert observ.metrics(kk)["spend_microcents"] == 0, "the spend must START at 0"

    # Turn 1: the paid lane is selected but has NO approved headroom → the dispatch
    # is HELD as a confirm-charge (spending nothing) and decide falls back offline.
    act1 = brain.decide(TURN, kk.weave(), agent())
    assert calls == [], "an unapproved PAID dispatch must never reach the transport"
    pend = inbox.pending()
    assert len(pend) == 1, "the held paid dispatch must enqueue exactly one confirm-charge"
    args = pend[0].content["args"]
    assert args["provider"] == "frontier-paid"
    for key in ("microcents", "tokens", "at_tick"):
        assert isinstance(args[key], int) and not isinstance(args[key], bool), \
            f"confirm-charge {key} must be an int (ints-not-floats), got {args[key]!r}"
    assert args["microcents"] > 0
    assert not kk.weave().of_type(CHARGE) and meter.spent_microcents() == 0, \
        "a queued confirm-charge must spend NOTHING before a human approves"
    assert act1.kind == RuleBrain().decide(TURN, kk.weave(), agent()).kind, \
        "a held dispatch must fall back to the deterministic RuleBrain"
    # ROUTED: the consult recorded its decision — paid selected, sick rejected.
    routes = kk.weave().of_type(provider_router.PROVIDER_ROUTING)
    assert len(routes) == 1, "the live consult must record a provider_routing Cell"
    rc = routes[0].content
    assert rc["selected_provider"] == "frontier-paid", rc
    assert any(r["provider_id"] == "sick" and "health" in r["reason"]
               for r in rc["rejected"]), \
        "the ineligible (unhealthy) provider must be rejected with an explainable reason"
    line("  routed + held: the live turn consults provider_router (paid lane selected, "
         "unhealthy lane rejected 'health', decision recorded on the Weft) and the meter "
         f"holds it as a confirm-charge ({args['microcents']} microcents queued, 0 spent, "
         "0 transport calls) ✓")

    # The HUMAN approves the queued charge → it enacts through the Morta gate and the
    # spend becomes a NON-ZERO int on the operator dashboard (was 0).
    out = meter.approve_charge(inbox, pend[0].id)
    assert "charged" in out and out["microcents"] == args["microcents"], out
    spent = observ.metrics(kk)["spend_microcents"]
    assert isinstance(spent, int) and not isinstance(spent, bool) and spent > 0, \
        f"after the metered call observ.metrics must report a NON-ZERO int spend: {spent!r}"
    assert spent == out["microcents"] == meter.spent_microcents()
    line(f"  metered: the human approves the charge through the Morta gate → a spend_charge "
         f"Cell lands and observ.metrics reports spend_microcents={spent} — a NON-ZERO int, "
         "where the unwired live path reported 0 ✓")

    # Turn 2 rides the approved headroom: PERMITTED — the socket fires exactly once and
    # the dispatch receipt (int tokens) landed BEFORE it.
    act2 = brain.decide(TURN, kk.weave(), agent())
    assert len(calls) == 1, "a permitted paid dispatch must reach the transport exactly once"
    assert act2.kind == "respond" and act2.text == "ok", \
        "the permitted dispatch must use the MODEL's decision (the plane is not a muzzle)"
    ds = [c for c in kk.weave().of_type(DISPATCH)
          if c.content["provider"] == "frontier-paid"]
    assert len(ds) == 1, "the permitted dispatch must be receipted on the Weft"
    assert isinstance(ds[0].content["tokens"], int) and \
        ds[0].content["tokens"] == args["tokens"], \
        "the receipt draws the SAME int token estimate the human approved"
    # Turn 3 finds the headroom drawn down → held again (queued), socket untouched.
    brain.decide(TURN, kk.weave(), agent())
    assert len(calls) == 1, "with the approved headroom drawn down, the socket stays shut"
    assert len(inbox.pending()) == 1, "the over-headroom turn must queue a fresh confirm-charge"
    line("  headroom: the next turn rides the approved charge — transport fired ONCE, the "
         "model's decision used, a spend_dispatch receipt (int tokens) recorded BEFORE the "
         "socket; the turn after finds the headroom drawn down and is queued again ✓")

    # ── (b) PAID OVER-BUDGET / UNCONFIGURED FAILS CLOSED. ──────────────────────────
    k2, m2, ib2 = _fresh()
    a2id = k2.decima_agent_id

    def agent2():
        return k2.weave().get(a2id)

    calls2, spy2 = _spy()
    brain2 = ModelBrain("k-fake", transport=spy2).bind_strategy(
        m2, ib2, agent2(), providers=[PAID])
    body = {"model": "m", "max_tokens": 8, "system": "You are Decima.",
            "messages": [{"role": "user", "content": TURN}]}
    # UNCONFIGURED: the paid lane is rejected at ELIGIBILITY — typed, explainable.
    try:
        brain2._post(body)
        raise AssertionError("an unconfigured-budget paid dispatch must fail CLOSED")
    except StrategyDenied as e:
        assert e.reason == "no_eligible_provider" and e.queued is None, (e.reason, e.queued)
        assert any("budget" in r["reason"] for r in e.rejected), e.rejected
    assert calls2 == [], "a fail-closed paid dispatch must never reach the transport"
    assert ib2.pending() == [], "an unconfigured paid dispatch must ENQUEUE nothing"
    assert not k2.weave().of_type(CHARGE) and m2.spent_microcents() == 0
    assert m2.budget_block() == {"remaining_microcents": 0, "pressure": 0,
                                 "configured": False}, "the budget must be untouched"
    act = brain2.decide(TURN, k2.weave(), agent2())
    assert calls2 == [] and act.kind == RuleBrain().decide(TURN, k2.weave(), agent2()).kind, \
        "decide() on the fail-closed paid lane must fall back to the offline RuleBrain"
    # OVER-BUDGET: a configured budget too small for the estimate → the confirm-charge
    # itself is denied 'budget_exhausted' — still nothing queued, nothing spent.
    m2.configure_budget(10)                       # 10 microcents << the estimate
    try:
        brain2._post(body)
        raise AssertionError("an over-budget paid dispatch must fail CLOSED")
    except StrategyDenied as e:
        assert e.reason == "budget_exhausted" and e.queued is None, (e.reason, e.queued)
    assert calls2 == [] and ib2.pending() == [] and not k2.weave().of_type(CHARGE)
    assert m2.remaining_microcents() == 10, "a denied charge must leave the budget unchanged"
    line("  fail closed: an UNCONFIGURED budget rejects the paid lane at eligibility and an "
         "over-budget estimate is denied 'budget_exhausted' — nothing queued, nothing "
         "charged, budget unchanged, the socket never reached; money moves only through a "
         "human-approved confirm-charge ✓")

    # ── (c) NO REGRESSION — redaction first; RuleBrain fallback; unbound unchanged. ─
    # The Cycle-52 redaction gate fires BEFORE the strategy plane: a secret turn is
    # blocked with NO routing Cell, NO queue traffic and NO socket.
    secret = "debug prod please: api key sk-livedeadbeef0123456789ABCDEFghij"
    routes_before = len(kk.weave().of_type(provider_router.PROVIDER_ROUTING))
    pend_before = len(inbox.pending())
    acts = brain.decide(secret, kk.weave(), agent())
    assert len(calls) == 1, "a secret-bearing turn reached the transport — redaction regressed"
    assert len(kk.weave().of_type(provider_router.PROVIDER_ROUTING)) == routes_before, \
        "a redaction-blocked turn must never reach the strategy plane (no routing Cell)"
    assert len(inbox.pending()) == pend_before, "a blocked turn must queue nothing"
    assert acts.kind == RuleBrain().decide(secret, kk.weave(), agent()).kind
    try:
        brain._post({"model": "m", "max_tokens": 8, "system": "s",
                     "messages": [{"role": "user", "content": secret}]})
        raise AssertionError("_post shipped a secret payload (no RedactionBlocked raised)")
    except redact.RedactionBlocked:
        pass
    line("  redaction intact: a secret turn is blocked BEFORE the strategy plane — no "
         "routing Cell, no confirm-charge, no socket — and _post still raises "
         "RedactionBlocked (the Cycle-52 gate stays first) ✓")

    # A FREE-lane transport failure still falls back to RuleBrain — and the failed
    # attempt was receipted BEFORE the socket (conservative accounting), drawing the
    # meter-held quota (the static fleet claim of 0 was overridden by the live meter).
    k3, m3, ib3 = _fresh()
    a3id = k3.decima_agent_id

    def agent3():
        return k3.weave().get(a3id)

    m3.configure_quota("free-lane", 100_000)

    def boom(url, headers, body, *rest):
        raise RuntimeError("wire down")

    brain3 = ModelBrain("k-fake", transport=boom).bind_strategy(
        m3, ib3, agent3(), providers=[FREE])
    act3 = brain3.decide("echo wire probe", k3.weave(), agent3())
    rb3 = RuleBrain().decide("echo wire probe", k3.weave(), agent3())
    assert act3.kind == rb3.kind == "invoke", \
        "a transport failure must still fall back to RuleBrain (which invokes echo)"
    ds3 = k3.weave().of_type(DISPATCH)
    assert len(ds3) == 1 and ds3[0].content["provider"] == "free-lane" and \
        isinstance(ds3[0].content["tokens"], int), \
        "the attempted free dispatch must be receipted (int tokens) even though it failed"
    assert m3.quota_remaining("free-lane", 10 ** 9) < 100_000, \
        "the receipt must draw down the METER-held quota (the live overlay)"
    # and a healthy free-lane dispatch flows: routed, receipted, model decision used.
    calls3, spy3 = _spy()
    brain3b = ModelBrain("k-fake", transport=spy3).bind_strategy(
        m3, ib3, agent3(), providers=[FREE])
    act3b = brain3b.decide(TURN, k3.weave(), agent3())
    assert len(calls3) == 1 and act3b.text == "ok", \
        "a permitted FREE dispatch must flow through the transport (metered, not muzzled)"
    assert len(k3.weave().of_type(DISPATCH)) == 2
    line("  fallback intact: a free-lane transport failure still falls back to RuleBrain "
         "(the failed attempt receipted first — never an unaccounted call); a healthy "
         "free-lane turn flows, metered ✓")

    # UNBOUND = the Cycle-52 behavior exactly (the offline/oracle configuration) …
    calls4, spy4 = _spy()
    unbound = ModelBrain("k-fake", transport=spy4)
    actu = unbound.decide(TURN, kk.weave(), agent())
    assert len(calls4) == 1 and actu.text == "ok", \
        "an UNBOUND brain must keep the exact pre-wiring behavior (existing checks green)"
    # … and a float provider metric is refused at the strategy door (ints-not-floats).
    try:
        ModelBrain("k-fake").bind_strategy(m3, ib3, agent3(),
                                           providers=[dict(FREE, cost_per_1k_microcents=1.5)])
        raise AssertionError("a float provider cost was accepted (ints-not-floats violated)")
    except TypeError:
        pass
    line("  compatibility: an unbound brain is byte-for-byte the Cycle-52 path, and a float "
         "fleet metric is refused at bind_strategy (ints-not-floats at the door) ✓")

    line("  → Cycle 50 is now LIVE on the brain path: every live model call is ROUTED "
         "(eligibility fail-closed, selection recorded) and METERED (receipted before the "
         "socket); a paid dispatch moves money ONLY inside human-approved confirm-charge "
         "headroom via the ApprovalInbox + Morta gate — and the redaction gate + RuleBrain "
         "fallback stand exactly as before.")
