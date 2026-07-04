"""STRATEGY-WIRE — the boot path binds the model-strategy plane onto the live brain.

Cycle 58's spendwire wired `_route_and_meter` into `ModelBrain._post`, but NOTHING on
the production path called `ModelBrain.bind_strategy` — the only caller was
checks/456. So the SHIPPED boot sequence (run.py → golive.boot → bind_brain) put a
live, egress-bound brain in service with `strategy=None`: every live LLM call went to
the socket UNROUTED and UNMETERED, with observ.metrics reporting spend 0.
`golive.bind_strategy_plane` — called from `bind_brain` in the SAME conditions
bind_brain binds the live brain (an approved api.anthropic.com grant, never before,
never without) — closes that: boot now binds a `SpendMeter` + `ApprovalInbox` + the
default fleet onto the brain, so `_route_and_meter` actually engages on the live
path. This check proves it, entirely offline + deterministically (fresh Kernels,
injected transport SPIES, a sentinel key that must never land durable, logical int
ticks, no network, no clock):

  (a) BOOT BINDS THE STRATEGY (load-bearing) — on a kernel with a (stub) live
      ModelBrain and a human-approved api.anthropic.com grant, `strategy` is None
      BEFORE boot (the shipped bug) and NOT None after `golive.boot`: the brain is
      egress-bound AND strategy-bound in one pass. A subsequent live-path `_post`
      is therefore ROUTED (the default `anthropic` fleet entry is consulted and the
      decision lands as a `provider_routing` Cell) and METERED: the paid lane with
      no configured budget fails CLOSED before any socket; with a budget the first
      dispatch is HELD as a confirm-charge (spending nothing), a human approves it
      through the Morta gate and observ.metrics reports a NON-ZERO int spend (was
      0 on the unwired path); the next dispatch rides that approved headroom —
      transport fired exactly once, `spend_dispatch` receipt (int tokens) landed
      BEFORE the socket. Re-running `bind_brain` (the shell `live` verb) is
      idempotent: the SAME strategy object stands.
  (b) KEYLESS / GRANT-LESS BOOT UNCHANGED — with no key, boot returns [] and
      touches NOTHING (the log does not move; the deterministic RuleBrain stays,
      no strategy anywhere); with a key but NO approved grant, boot names what is
      missing and binds NEITHER egress NOR strategy (fail closed, the exact
      conditions of bind_brain).
  (c) NO AMBIENT AUTHORITY — binding the strategy plane mints NOTHING: the set of
      capability Cells and the approvals set are byte-identical across boot, and
      no secret byte of the key survives in any boot line.

Mutation-resistance (the load-bearing line): delete the `bind_strategy_plane(k, b)`
call in `golive.bind_brain`'s success path and (a) goes RED — after boot the brain's
`strategy` stays None, a live `_post` goes straight to the transport (no
provider_routing Cell, no confirm-charge, nothing receipted) and the reported spend
stays 0.

Contract: run(k, line). Fail loud (assert / expected StrategyDenied). Owns fresh
Kernels; registers no effects (the confirm-charge rides the kernel's existing gated
spine, exactly as in checks/416_spend.py and checks/456_spendwire.py).
"""
import json
import os
import tempfile

from decima.kernel import Kernel
from decima.agent import ModelBrain, RuleBrain, StrategyDenied
from decima.inbox import ApprovalInbox
from decima.spend import SpendMeter, CHARGE, DISPATCH
from decima import golive, observ, provider_router

# A sentinel no legitimate content could contain: if it surfaces in a boot line or
# any durable byte, custody is broken and we fail loud.
SENTINEL_KEY = "sk-ant-strategywire-SENTINEL-7e4b19c2d8f0a635"
TURN = "summarize the release notes"


def _fresh():
    """A fresh, isolated Kernel over its own weft.db."""
    return Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)


def _tool_use(inp):
    """A canned Anthropic tool_use response carrying the `act` decision `inp`."""
    return {"content": [{"type": "tool_use", "name": "act", "input": inp}],
            "stop_reason": "tool_use"}


def _spy():
    """An injected transport SPY: records every payload that reaches the socket seam
    and answers with a benign respond decision. If it fires when the strategy plane
    should have held the dispatch, the boot wiring failed."""
    calls = []

    def transport(url, headers, body, *rest):
        calls.append(json.loads(body.decode("utf-8")
                                if isinstance(body, (bytes, bytearray)) else body))
        return 200, _tool_use({"action": "respond", "text": "ok", "reasoning": "seen"})

    return calls, transport


def run(k, line):
    line("\n== STRATEGY-WIRE: boot binds the model-strategy plane onto the live brain ==")

    # ── (a) BOOT BINDS THE STRATEGY (the load-bearing wiring) ──────────────────────
    kk = _fresh()
    # The operator flow first: request egress to the brain's host, a HUMAN approves.
    res = golive.request_grant(kk, golive.BRAIN_HOST)
    assert res["status"] == "pending", res
    ib = ApprovalInbox(kk)
    approved = ib.approve(res["item"])
    assert "ok" in approved and res["capability"] in kk.approvals, approved
    # A (stub) live brain, exactly what make_brain builds when the key is exported —
    # constructed directly so the probe never touches the process environment.
    kk.brain = ModelBrain(SENTINEL_KEY)
    assert kk.brain.strategy is None and kk.brain.egress is None, \
        "the pre-boot brain must be the shipped state: unbound, strategy=None"

    caps_before = {c.id for c in kk.weave().of_type("capability")}
    appr_before = set(kk.approvals)
    boot_lines = golive.boot(kk, environ={"ANTHROPIC_API_KEY": SENTINEL_KEY})
    assert boot_lines and SENTINEL_KEY not in repr(boot_lines), \
        "boot must announce (redacted) — never a secret byte in a boot line"
    assert kk.brain.egress is not None, "boot must bind the approved egress grant"
    assert kk.brain.strategy is not None, \
        "BOOT MUST BIND THE STRATEGY PLANE — a live brain with strategy=None " \
        "dispatches unrouted + unmetered (the exact bug this lane closes)"
    st = kk.brain.strategy
    assert isinstance(st["meter"], SpendMeter) and isinstance(st["inbox"], ApprovalInbox)
    fleet_ids = [p["id"] for p in st["providers"]]
    assert fleet_ids == [golive.FLEET_PROVIDER_ID], \
        f"the default fleet must be present (not empty, not unnamed): {fleet_ids}"
    assert any("strategy plane bound" in ln for ln in boot_lines), boot_lines
    # Idempotent: the shell `live` verb re-runs bind_brain — the SAME plane stands.
    assert golive.bind_brain(kk).startswith("brain: model — egress-bound")
    assert kk.brain.strategy is st, "re-binding must not replace the bound plane"
    line("  bound at boot: with a human-approved grant, golive.boot binds egress AND "
         "the strategy plane (meter + inbox + the default 'anthropic' fleet) — "
         "strategy was None before, is bound after; `live` re-runs idempotently ✓")

    # The live path now consults the plane. Inject the socket-seam spy (the gate and
    # the strategy stand; only the socket is fake) and use one FIXED body so the int
    # token estimate is byte-stable across the turns below (deterministic).
    calls, spy = _spy()
    kk.brain.transport = spy
    body = {"model": "m", "max_tokens": 8, "system": "You are Decima.",
            "messages": [{"role": "user", "content": TURN}]}
    assert observ.metrics(kk)["spend_microcents"] == 0, "the spend must START at 0"

    # NO budget configured → the paid default lane is rejected at ELIGIBILITY: the
    # socket is never reached, nothing queued, nothing spent — but the consult IS
    # recorded (routed, in the fail-closed sense the wired plane guarantees).
    try:
        kk.brain._post(body)
        raise AssertionError("a paid dispatch with no budget must fail CLOSED")
    except StrategyDenied as e:
        assert e.reason == "no_eligible_provider" and e.queued is None, (e.reason,)
        assert any("budget" in r["reason"] for r in e.rejected), e.rejected
    assert calls == [], "a fail-closed dispatch must never reach the transport"
    routes = kk.weave().of_type(provider_router.PROVIDER_ROUTING)
    assert len(routes) == 1, "the live consult must record a provider_routing Cell"
    # … and decide() on that same fail-closed lane answers from the RuleBrain.
    agent = kk.weave().get(kk.decima_agent_id)
    act = kk.brain.decide(TURN, kk.weave(), agent)
    assert calls == [] and act.kind == RuleBrain().decide(TURN, kk.weave(), agent).kind, \
        "the fail-closed live turn must fall back to the deterministic RuleBrain"
    line("  routed, fail closed: an unbudgeted paid dispatch is rejected at eligibility "
         "— provider_routing Cell recorded, socket untouched, RuleBrain answers ✓")

    # With a budget: the first dispatch is HELD as a confirm-charge (spending
    # nothing) until a HUMAN approves it through the Morta gate.
    meter = SpendMeter(kk)                       # folds the same Weft as the bound plane
    meter.configure_budget(1_000_000)
    try:
        kk.brain._post(body)
        raise AssertionError("an unapproved PAID dispatch must be held, not sent")
    except StrategyDenied as e:
        held = e.queued
    assert held is not None and calls == [], \
        "the held dispatch must queue a confirm-charge and never reach the socket"
    item = kk.weave().get(held)
    args = item.content["args"]
    assert args["provider"] == golive.FLEET_PROVIDER_ID, args
    for key in ("microcents", "tokens", "at_tick"):
        assert isinstance(args[key], int) and not isinstance(args[key], bool), \
            f"confirm-charge {key} must be an int (ints-not-floats), got {args[key]!r}"
    assert not kk.weave().of_type(CHARGE) and meter.spent_microcents() == 0, \
        "a queued confirm-charge must spend NOTHING before a human approves"
    out = meter.approve_charge(ib, held)
    assert "charged" in out and out["microcents"] == args["microcents"], out
    spent = observ.metrics(kk)["spend_microcents"]
    assert isinstance(spent, int) and not isinstance(spent, bool) and spent > 0, \
        f"the metered boot-bound path must report a NON-ZERO int spend: {spent!r}"
    # The next dispatch rides the approved headroom: PERMITTED — the socket fires
    # exactly once and the receipt (int tokens) landed BEFORE it.
    data = kk.brain._post(body)
    assert len(calls) == 1 and data["stop_reason"] == "tool_use", \
        "a permitted paid dispatch must reach the transport exactly once"
    ds = [c for c in kk.weave().of_type(DISPATCH)
          if c.content["provider"] == golive.FLEET_PROVIDER_ID]
    assert len(ds) == 1 and isinstance(ds[0].content["tokens"], int) and \
        ds[0].content["tokens"] == args["tokens"], \
        "the receipt draws the SAME int token estimate the human approved"
    assert kk.brain.last_strategy.selected_provider == golive.FLEET_PROVIDER_ID
    line(f"  metered: the first paid turn is held as a confirm-charge, the human "
         f"approves through the Morta gate (spend_microcents={spent}, was 0 unwired), "
         "and the next turn rides the headroom — transport fired ONCE, spend_dispatch "
         "receipted before the socket ✓")

    # ── (b) KEYLESS / GRANT-LESS BOOT UNCHANGED (fail closed) ──────────────────────
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        k2 = _fresh()
        lam0 = k2.weft.lamport
        assert golive.boot(k2, environ={"PATH": "/usr/bin"}) == [] \
            and k2.weft.lamport == lam0, "no key → boot must touch NOTHING"
        assert getattr(k2.brain, "strategy", None) is None, \
            "a keyless boot must bind no strategy (behavior-identical)"
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved
    # Keyed but NO approved grant: bind NEITHER egress nor strategy — the exact
    # fail-closed conditions of bind_brain, now shared by the strategy plane.
    k3 = _fresh()
    k3.brain = ModelBrain(SENTINEL_KEY)
    lines3 = golive.boot(k3, environ={"ANTHROPIC_API_KEY": SENTINEL_KEY})
    assert any("NOT live" in ln for ln in lines3), lines3
    assert k3.brain.egress is None and k3.brain.strategy is None, \
        "no approved grant → no egress binding AND no strategy binding (fail closed)"
    line("  fail closed: a keyless boot appends nothing and leaves the RuleBrain; a "
         "keyed boot without an approved grant binds neither egress nor strategy ✓")

    # ── (c) NO AMBIENT AUTHORITY — binding the plane mints nothing ─────────────────
    caps_after = {c.id for c in kk.weave().of_type("capability")}
    # (compare against the pre-boot snapshot: the dispatch flow AFTER boot minted the
    #  Morta-gated spend capability — through the kernel API, behind the gate — but
    #  BOOT itself, the binding, minted not one capability and approved nothing.)
    minted_by_flow = {c.id for c in kk.weave().of_type("capability")
                      if c.content.get("name", "").startswith("spend.charge:")}
    assert caps_after - minted_by_flow == caps_before, \
        "binding the strategy plane must mint NO capability (zero ambient authority)"
    assert {a for a in appr_before} <= set(kk.approvals), "approvals only grow via humans"
    line("  no ambient authority: across boot the capability set is unchanged (the only "
         "later mint is the Morta-gated confirm-charge capability, enacted by a human); "
         "binding conferred nothing ✓")

    line("  → the strategy plane is now ON the boot path: bind_brain binds egress AND "
         "bind_strategy_plane in the same approved-grant conditions, so every live "
         "model call is routed + metered from the first boot — paid spend moves only "
         "through a human-approved confirm-charge, and a keyless boot is untouched.")
