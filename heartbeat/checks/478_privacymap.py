"""PRIVACY-MAP RECONCILE — agent's privacy→router map agrees with redact (no widening).

`ModelBrain._route_and_meter` turns the redaction gate's privacy CLASSIFICATION into the
router privacy class that hard-gates provider ELIGIBILITY (`provider_router.
allowed_privacy_tiers`). That map used to be a hand-maintained copy in agent.py — and it
DIVERGED from redact's canonical `to_router_privacy`: repo_sensitive was mapped to the
'repo_sensitive' router class, which admits the OFF-DEVICE private_rented tier, an
eligibility WIDENING of a class redact pins LOCAL-ONLY. The reconcile derives
`_PRIVACY_TO_ROUTER_CLASS` from redact.CLASSES × redact.to_router_privacy — one source of
truth on the live strategy consult. This check proves, offline + deterministically (fresh
Kernels, an injected transport SPY, an injected meter/inbox/fleet, no network, no clock):

  (a) REPO/RESTRICTED STAY LOCAL-ONLY (load-bearing) — a payload redact CLASSIFIES
      repo_sensitive (a corp hostname + an fs path) and one it classifies restricted map,
      through agent's live map, to the router class whose allowed tiers are EXACTLY
      {local_only}. Driven through the REAL live consult (`_route_and_meter`, the exact
      call `_post` makes): over an external-only fleet (private_rented + external +
      external_paid, budget configured) BOTH classes yield ZERO eligible providers —
      `StrategyDenied('no_eligible_provider')`, every rejection a privacy reason, nothing
      queued, the socket never reached. With an on-device lane in the fleet, the SAME
      dispatch is forced onto it (routed to local_only; the `provider_routing` Cell
      records privacy_class 'private').
  (b) AGREEMENT WITH REDACT — for EVERY redact privacy class, agent's map equals
      redact.to_router_privacy (no divergence, and the map covers exactly redact.CLASSES);
      an UNKNOWN class still resolves to secret_sensitive → zero tiers (fail closed —
      never widened to 'public').
  (c) NO REGRESSION — a public turn with a bound strategy still routes normally through
      the transport (metered, model decision used), and the Cycle-52 redaction gate still
      fires FIRST: a repo_sensitive turn through `_post` is RedactionBlocked before the
      strategy plane (defence in depth — the reconciled map is the second wall).

Mutation-resistance (the load-bearing line): revert the map derivation
`c: _redact.to_router_privacy(c) for c in _redact.CLASSES` in decima/agent.py to the old
divergent hand-written dict (repo_sensitive → 'repo_sensitive') and (a) goes RED — the
repo_sensitive dispatch becomes eligible for the private_rented lane (routed/queued
instead of 'no_eligible_provider', and its allowed tiers are no longer {local_only}).

Contract: run(k, line). Fail loud (assert / expected StrategyDenied). Owns fresh Kernels;
registers no effects.
"""
import json
import os
import tempfile

from decima.kernel import Kernel
from decima.agent import (ModelBrain, RuleBrain, StrategyDenied,
                          _PRIVACY_TO_ROUTER_CLASS)
from decima.inbox import ApprovalInbox
from decima.spend import SpendMeter, DISPATCH
from decima import provider_router, redact


def _tool_use(inp):
    """A canned Anthropic tool_use response carrying the `act` decision `inp`."""
    return {"content": [{"type": "tool_use", "name": "act", "input": inp}],
            "stop_reason": "tool_use"}


def _fresh():
    """A fresh, isolated Kernel + spend meter + approval inbox over it."""
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    return kk, SpendMeter(kk), ApprovalInbox(kk)


def _spy():
    """An injected transport SPY: records every payload that reaches the socket seam."""
    calls = []

    def transport(url, headers, body, *rest):
        calls.append(json.loads(body.decode("utf-8")
                                if isinstance(body, (bytes, bytearray)) else body))
        return 200, _tool_use({"action": "respond", "text": "ok", "reasoning": "seen"})

    return calls, transport


def _body(text):
    """A minimal live request body carrying `text` as the outbound payload."""
    return {"model": "m", "max_tokens": 8, "system": "You are Decima.",
            "messages": [{"role": "user", "content": text}]}


# The injected fleet (shared live-status entries — every numeric an INT). The three
# non-local lanes are all healthy, quota'd, capacitied and (where paid) inside a
# configured budget, so the ONLY thing that can hold a sensitive dispatch off them
# is the privacy eligibility gate — exactly the wall under test.
RENTED = {"id": "rented", "tier": "frontier", "privacy_tier": "private_rented",
          "model": "rented-m", "cost_per_1k_microcents": 500, "healthy": True,
          "quota_remaining": 50_000, "capacity": 16, "residency": "in_vpc",
          "scorecard": 0}
EXT = {"id": "ext-free", "tier": "local-small", "privacy_tier": "external",
       "model": "ext-m", "cost_per_1k_microcents": 0, "healthy": True,
       "quota_remaining": 50_000, "capacity": 16, "residency": "external",
       "scorecard": 0}
EXTP = {"id": "ext-paid", "tier": "frontier", "privacy_tier": "external_paid",
        "model": "extp-m", "cost_per_1k_microcents": 1000, "healthy": True,
        "quota_remaining": 50_000, "capacity": 16, "residency": "external",
        "scorecard": 0}
LOCAL = {"id": "on-device", "tier": "local-small", "privacy_tier": "local_only",
         "model": "local-m", "cost_per_1k_microcents": 0, "healthy": True,
         "quota_remaining": 50_000, "capacity": 16, "residency": "local",
         "scorecard": 0}

# Payloads redact itself classifies — the check never hand-stamps the class it gates on.
REPO_TURN = "sync the deploy config from build-01.corp under /etc/decima/keys"
RESTRICTED_TURN = "RESTRICTED — do not distribute: summarize the draft board minutes"
PUBLIC_TURN = "summarize the release notes"


def run(k, line):
    line("\n== PRIVACY-MAP RECONCILE — repo/restricted dispatches stay LOCAL-ONLY on the live path ==")

    # ── (a) REPO/RESTRICTED STAY LOCAL-ONLY (the load-bearing reconcile). ───────────
    kk, meter, inbox = _fresh()
    aid = kk.decima_agent_id

    def agent():
        return kk.weave().get(aid)

    meter.configure_budget(1_000_000)          # paid lanes have headroom: privacy is
    calls, spy = _spy()                        # the ONLY gate that can hold them back
    brain = ModelBrain("k-fake", transport=spy).bind_strategy(
        meter, inbox, agent(), providers=[RENTED, EXT, EXTP])

    held = {}
    for name, turn in (("repo_sensitive", REPO_TURN), ("restricted", RESTRICTED_TURN)):
        # redact CLASSIFIES the payload (the same call _screen_egress makes) …
        _scrubbed, findings = redact.scrub(turn)
        cls = redact.classify_privacy(turn, findings)
        assert cls == name, f"redact must classify the probe payload {name}: got {cls!r}"
        # … agent's live map must resolve it to the LOCAL-ONLY router class …
        tier = _PRIVACY_TO_ROUTER_CLASS.get(cls, "secret_sensitive")
        allowed = provider_router.allowed_privacy_tiers(tier)
        assert allowed == frozenset({provider_router.LOCAL_ONLY}), \
            (f"a {name} dispatch must map to the LOCAL-ONLY router tier — got "
             f"{tier!r} admitting {sorted(allowed)} (eligibility WIDENED)")
        # … and the REAL live consult must find ZERO eligible in an external-only fleet.
        try:
            brain._route_and_meter(_body(turn), cls)
            raise AssertionError(
                f"a {name} dispatch was routed to an external-only fleet — the "
                "privacy map has eligibility-widened it")
        except StrategyDenied as e:
            assert e.reason == "no_eligible_provider" and e.queued is None, \
                (f"a {name} dispatch must fail closed with nothing queued: "
                 f"{e.reason!r}, queued={e.queued!r}")
            assert {r["provider_id"] for r in e.rejected} == \
                {"rented", "ext-free", "ext-paid"}, e.rejected
            assert all(r["reason"].startswith("privacy") for r in e.rejected), \
                f"every rejection must be a PRIVACY hard-fail: {e.rejected}"
        held[name] = tier
    assert calls == [] and inbox.pending() == [], \
        "a held sensitive dispatch must reach no socket and queue no charge"
    line("  local-only: redact classifies the corp-host/fs-path turn repo_sensitive and the "
         "marked turn restricted; agent's live map resolves BOTH to the "
         f"{held['repo_sensitive']!r} router class (allowed tiers exactly {{local_only}}), "
         "and the live consult rejects every external/rented lane with a privacy reason — "
         "nothing routed, nothing queued, no socket ✓")

    # With an ON-DEVICE lane in the fleet, the same repo_sensitive dispatch is FORCED
    # onto it — the redact/router intent (`_r_private` → the local lane), now on the
    # live consult.
    brain2 = ModelBrain("k-fake", transport=spy).bind_strategy(
        meter, inbox, agent(), providers=[RENTED, EXT, EXTP, LOCAL])
    decision = brain2._route_and_meter(_body(REPO_TURN), "repo_sensitive")
    assert decision.routed and decision.selected_provider == "on-device", decision
    assert {r["provider_id"] for r in decision.rejected} == \
        {"rented", "ext-free", "ext-paid"}, decision.rejected
    routes = kk.weave().of_type(provider_router.PROVIDER_ROUTING)
    assert routes and routes[-1].content["selected_provider"] == "on-device"
    assert routes[-1].content["privacy_class"] == "private", \
        "the recorded decision must carry the RECONCILED (local-only) privacy class"
    ds = [c for c in kk.weave().of_type(DISPATCH) if c.content["provider"] == "on-device"]
    assert len(ds) == 1 and isinstance(ds[0].content["tokens"], int), \
        "the permitted on-device dispatch must be receipted (int tokens) before the socket"
    line("  forced local: with an on-device lane present the SAME repo_sensitive dispatch "
         "routes to it (every off-device lane privacy-rejected), the provider_routing Cell "
         "records privacy_class 'private', and the dispatch is receipted ✓")

    # ── (b) AGREEMENT WITH REDACT — one source of truth, no divergence. ─────────────
    assert set(_PRIVACY_TO_ROUTER_CLASS) == set(redact.CLASSES), \
        "the map must cover exactly redact's privacy classes (derived, not hand-kept)"
    for cls in redact.CLASSES:
        assert _PRIVACY_TO_ROUTER_CLASS[cls] == redact.to_router_privacy(cls), \
            (f"agent's map diverges from redact.to_router_privacy for {cls!r}: "
             f"{_PRIVACY_TO_ROUTER_CLASS[cls]!r} != {redact.to_router_privacy(cls)!r}")
    # An UNKNOWN class still fails CLOSED on the live consult's default — never the
    # 'public' reading to_router_privacy would give it.
    unknown = _PRIVACY_TO_ROUTER_CLASS.get("no_such_class", "secret_sensitive")
    assert provider_router.allowed_privacy_tiers(unknown) == frozenset(), \
        "an unknown privacy class must keep ZERO eligible providers (fail closed)"
    line("  reconciled: agent's map equals redact.to_router_privacy for every class "
         "(public→public, low_sensitive→sensitive, repo/restricted/secret→private) and an "
         "unknown class still resolves to zero eligible tiers ✓")

    # ── (c) NO REGRESSION — public routes normally; the redaction gate stays FIRST. ─
    k3, m3, ib3 = _fresh()
    a3id = k3.decima_agent_id

    def agent3():
        return k3.weave().get(a3id)

    calls3, spy3 = _spy()
    brain3 = ModelBrain("k-fake", transport=spy3).bind_strategy(
        m3, ib3, agent3(), providers=[EXT])
    act3 = brain3.decide(PUBLIC_TURN, k3.weave(), agent3())
    assert len(calls3) == 1 and act3.kind == "respond" and act3.text == "ok", \
        "a PUBLIC turn must still route to the external free lane and use the model's decision"
    assert len(k3.weave().of_type(DISPATCH)) == 1, "the public dispatch must be receipted"
    # The Cycle-52 redaction gate still fires BEFORE the strategy plane: a repo_sensitive
    # turn through _post is blocked at redaction (no socket) — the map is the second wall.
    try:
        brain3._post(_body(REPO_TURN))
        raise AssertionError("_post shipped a repo_sensitive payload (no RedactionBlocked)")
    except redact.RedactionBlocked as e:
        assert e.classification == "repo_sensitive", e.classification
    assert len(calls3) == 1, "the blocked turn must never reach the transport"
    act3b = brain3.decide(REPO_TURN, k3.weave(), agent3())
    assert act3b.kind == RuleBrain().decide(REPO_TURN, k3.weave(), agent3()).kind, \
        "a redaction-blocked turn must still fall back to the offline RuleBrain"
    line("  no regression: a public turn flows through the transport exactly once "
         "(metered, model decision used) and the redaction gate still blocks a "
         "repo_sensitive payload BEFORE the strategy plane, falling back offline ✓")

    line("  → the live strategy consult now speaks redact's canonical privacy language: "
         "_PRIVACY_TO_ROUTER_CLASS is DERIVED from redact.to_router_privacy, so a "
         "repo_sensitive or restricted dispatch is structurally local-only (zero external "
         "eligibility, forced onto the on-device lane) — the eligibility-widening "
         "divergence cannot recur, and unknown classes still fail closed.")
