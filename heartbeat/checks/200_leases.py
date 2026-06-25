"""LEASE1 — leases: time-locked + single-use authority (the kernel primitive behind
ephemeral single-use cards + time-locked wallets, CAPABILITY_MAP D3.4 / B4 Web3).

A grant may carry LEASE caveats that fail CLOSED on expiry/exhaustion exactly like a
revoked grant — they compose the SAME DERIVED_AUTHORITY cascade (FOLD §10.2):

  - `expires_at` (int logical time): authorize() denies once the current logical
    frontier time (lamport — never wall-clock) reaches expires_at. A time-locked
    wallet that can only act before a deadline.
  - `max_uses` (int): authorize() denies once the count of prior INVOKEs this grant
    authorized — folded deterministically from the Weave — reaches max_uses
    (single-use = max_uses 1). An ephemeral single-use card.

This proves:
  • a SINGLE-USE lease — first INVOKE OK, the second fails CLOSED (use folded from
    the Log, not an in-memory counter);
  • a TIME-LOCKED lease — INVOKE OK before expiry, then fails CLOSED once the logical
    frontier passes expires_at — and the SAME lapse fails closed a grant DELEGATED
    from it (the cascade);
  • the lease's asserts + the use/expiry event skeletons stay on the Log (Law 1);
  • two folds give an identical state_root, and time-travel to a pre-expiry frontier
    still folds the lease LIVE — the lapse is "after its frontier", never a rewrite.

DETERMINISM: "now" is the logical frontier (lamport); uses are folded from the Weft;
ints not floats; fail CLOSED. Contract: run(k, line). Fail loud.
"""


def run(k, line):
    line("\n== LEASES (time-locked + single-use authority — fail closed) ==")

    decima = k.weave().get(k.decima_agent_id)

    # A fresh, no-cost READ capability to lease out (the shared kernel's bootstrap
    # caps have spent budgets from earlier sections — README: forge what you need).
    base = k.integrate_tool(
        "lease.echo", lambda impl, args: {"out": args.get("text", "")},
        caveats={"effect_class": "READ"})

    # ── 1. SINGLE-USE lease (max_uses = 1) — an ephemeral single-use card ──────
    # Delegate a grant whose lease allows exactly ONE invoke. The use count is folded
    # from the Weave's INVOKE events for this exact grant — deterministic, not a seam.
    once_id, once_grant, _ = k.spawn(decima, "OneShot", base,
                                     {"max_uses": 1}, "spend a single-use card")
    once = k.weave().get(once_id)

    # Delegate a sub-grant from the single-use card BEFORE it is spent. The child's
    # OWN use-count is separate (a distinct grant id), so it never exhausts itself —
    # which makes it a clean probe of the PURE cascade: when the PARENT is exhausted,
    # the child fails closed only because its authority DESCENDS from a dead lease.
    sub_once_id, sub_once_grant, _ = k.spawn(once, "OneShotSub", once_grant,
                                             {}, "delegated under the single-use card")
    sub_once = k.weave().get(sub_once_id)

    first = k.invoke(once, once_grant, {"text": "charge once"})
    assert "ok" in first, f"single-use first invoke must succeed: {first}"
    second = k.invoke(once, once_grant, {"text": "charge twice"})
    # The spent lease fails CLOSED exactly like a revoked grant: once its single use is
    # folded, the fold treats it as retracted (a lease_expired cascade root), so the
    # authorize gate denies — naming the LEASE reason (exhausted), not a bare revoke.
    assert "denied" in second and "exhaust" in second["denied"], \
        f"single-use second invoke must fail closed (exhausted): {second}"
    line(f"  single-use: 1st INVOKE OK → {first['ok']['out']!r}; "
         f"2nd ✋ {second['denied']}")

    # Exhaustion fails it closed in the FOLD too — like a revoked grant it leaves
    # of_type and is marked lease_expired (a DERIVED_AUTHORITY cascade root).
    w = k.weave()
    spent_cell = w.get(once_grant)
    assert spent_cell.lease_expired and spent_cell.retracted, \
        "an exhausted single-use lease must be lease_expired + retracted in the fold"
    assert once_grant not in {c.id for c in w.of_type("capability")}, \
        "an exhausted lease must drop out of of_type (fails closed everywhere)"
    # PURE cascade: the child has 0 uses of its OWN, yet fails closed because its
    # authority descends from the exhausted parent (cascaded=True) — the SAME
    # DERIVED_AUTHORITY cascade as a revoked grant (FOLD §10.2).
    sub_cell = w.get(sub_once_grant)
    assert sub_cell.retracted and sub_cell.cascaded, \
        "a child of an exhausted lease must fail closed via the cascade (cascaded=True)"
    sub_denied = k.invoke(sub_once, sub_once_grant, {"text": "child charge"})
    assert "denied" in sub_denied, \
        f"the child of an exhausted single-use lease must fail closed: {sub_denied}"
    line(f"  single-use: child delegated from it (0 own uses) fails closed via CASCADE "
         f"→ ✋ {sub_denied['denied']}")
    line("  single-use: after its one use the lease is lease_expired + leaves of_type "
         "(fails closed like a revoked grant)")

    # ── 2. TIME-LOCKED lease (expires_at) — a time-locked wallet ──────────────
    # "now" is the logical frontier (lamport). Set the deadline a few ticks ahead so
    # an invoke lands BEFORE it; then advance the frontier PAST it and watch it lapse.
    # Headroom so the wallet is comfortably live at mint + first invoke; ints only.
    deadline = k.weft.lamport + 12           # logical time, never a clock
    locked_id, locked_grant, _ = k.spawn(decima, "TimeLock", base,
                                         {"expires_at": deadline}, "time-locked wallet")
    locked = k.weave().get(locked_id)
    assert k.weft.lamport < deadline, "fixture: frontier must start before the deadline"

    at_invoke = k.weft.lamport               # the frontier the pre-invoke authorizes at
    pre = k.invoke(locked, locked_grant, {"text": "act before the deadline"})
    assert "ok" in pre, f"time-locked invoke before expiry must succeed: {pre}"
    line(f"  time-locked: frontier {at_invoke} < expires_at {deadline} → INVOKE OK "
         f"→ {pre['ok']['out']!r}")

    # Delegate a sub-grant DOWNHILL from the wallet BEFORE it lapses — a child that
    # inherits the lease (its expires_at clamped). It can act now; the cascade check
    # below proves it fails closed once the parent lease lapses (FOLD §10.2).
    child_id, child_grant, _ = k.spawn(locked, "SubWallet", locked_grant,
                                       {}, "spend under the time-locked wallet")
    child = k.weave().get(child_id)
    pre_child = k.invoke(child, child_grant, {"text": "child acts before deadline"})
    assert "ok" in pre_child, f"delegated child must invoke before expiry: {pre_child}"
    line(f"  cascade: delegated a SubWallet downhill (expires_at inherited); "
         f"child INVOKE before expiry OK → {pre_child['ok']['out']!r}")

    # The frontier just BEFORE expiry — for the time-travel check below. (The pre-invoke
    # already landed safely under the deadline, so this fold is still live.)
    pre_frontier = k.weft.count()
    assert k.weave(upto_seq=pre_frontier).frontier_lamport < deadline, \
        "fixture: the pre-expiry frontier must be below the deadline"

    # Advance the logical frontier past the deadline (each utterance appends events,
    # so lamport climbs). Pure logical time — no wall-clock anywhere.
    while k.weft.lamport < deadline:
        k.say("echo tick")
    assert k.weft.lamport >= deadline, "frontier must now be at/past the deadline"

    post = k.invoke(locked, locked_grant, {"text": "act after the deadline"})
    # Fails CLOSED past the deadline — the lapsed lease is retracted in the fold and the
    # denial names the LEASE reason (expired), exactly like a revoked grant but legible.
    assert "denied" in post and "expired" in post["denied"], \
        f"time-locked invoke past expiry must fail closed (expired): {post}"
    line(f"  time-locked: frontier {k.weft.lamport} ≥ expires_at {deadline} → "
         f"✋ {post['denied']}")

    # ── 3. CASCADE: a lapsed lease fails closed what was DELEGATED from it ─────
    # The expired wallet is a lease_expired DERIVED_AUTHORITY cascade root; the child
    # grant attenuated from it fails closed too — the SAME cascade as a revoked grant
    # (FOLD §10.2), driven here by lease expiry rather than an explicit RETRACT.
    w = k.weave()
    parent_cell, child_cell = w.get(locked_grant), w.get(child_grant)
    assert parent_cell.lease_expired and parent_cell.cascade_root, \
        "an expired time-locked lease must be a lease_expired cascade root"
    # The child fails closed once the parent lapses — via the DERIVED_AUTHORITY cascade
    # (`cascaded`) and/or, since it INHERITED the wallet's expires_at downhill, its own
    # lease expiry. Either way it is retracted and drops out of every projection.
    assert child_cell.retracted and (child_cell.cascaded or child_cell.lease_expired), \
        "the delegated child grant must fail closed (cascade and/or inherited lease)"
    assert child_grant not in {c.id for c in w.of_type("capability")}, \
        "the failed-closed child grant must drop out of of_type"
    post_child = k.invoke(child, child_grant, {"text": "child acts after deadline"})
    assert "denied" in post_child, \
        f"the delegated child must fail closed once the parent lease lapsed: {post_child}"
    line(f"  cascade: wallet lapsed → SubWallet (delegated from it) fails closed too "
         f"→ ✋ {post_child['denied']}")

    # ── 4. History intact (Law 1): asserts + the use/expiry events on the Log ──
    eids = list(k.weft.events())
    asserted = {ev.body.get("cell") for ev in eids if ev.verb == "ASSERT"}
    assert {once_grant, locked_grant} <= asserted, \
        "the lease grant asserts must remain on the Log"
    invoke_cells = [ev for ev in eids if ev.verb == "INVOKE"
                    and ev.body.get("cap") in (once_grant, locked_grant)]
    assert len(invoke_cells) >= 2, \
        "the single-use + time-locked USE events (INVOKE skeletons) must stay on the Log"
    line(f"  history intact: lease asserts + {len(invoke_cells)} use-event skeleton(s) "
         "still in weft.events()")

    # ── 5. Time-travel: at the pre-expiry frontier the wallet folds LIVE ───────
    past = k.weave(upto_seq=pre_frontier)
    past_wallet = past.get(locked_grant)
    assert past_wallet is not None and not past_wallet.retracted \
        and not past_wallet.lease_expired, \
        "at a pre-expiry frontier the time-locked lease must fold LIVE (lapse is post-frontier)"
    line(f"  time-travel: at seq ≤ {pre_frontier} the wallet folds live "
         "(the lapse is strictly after its frontier)")

    # ── 6. Determinism: two independent folds → identical state_root ───────────
    r1, r2 = k.weave().state_root(), k.weave().state_root()
    assert r1 == r2, "two folds must give an identical state_root (leases are deterministic)"
    line(f"  determinism: two folds → identical state_root ({r1[:12]}…)")

    line("  → leases are time-locked / single-use authority that fail CLOSED on "
         "expiry/exhaustion — the kernel primitive behind single-use cards + "
         "time-locked wallets, folded deterministically from the Log.")
