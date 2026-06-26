"""D5 — the autonomy ladder (per-capability autonomy levels). This check proves:

  - set_autonomy records a per-(agent, capability) rung Cell on the Weft;
  - a rung-1 (read-only) capability REFUSES any write/effect (but still permits READ);
  - a rung-2 capability PROPOSES — nothing executes;
  - at rung-3 a FINANCIAL action REQUIRES_APPROVAL while a REVERSIBLE one EXECUTES
    (different steps at different rungs by reversibility/stakes — the D5 insight);
  - promotion is EARNED — it happens only once the WV1 track-record threshold is met, and
    NOT before;
  - demotion is INSTANT (no evidence required — the Morta reflex);
  - a manual pin is honored above evidence (the owner holds the rung).

Contract: run(k, line). Fail loud.
"""
from decima import autonomy as au
from decima import wager as wv


def run(k, line):
    line("\n== AUTONOMY LADDER (per-capability rungs, earned up / instant down) — D5 ==")

    agent = k.decima_agent_id
    cap_read = "cap:reports.read"        # capability ids are opaque strings to the ladder
    cap_send = "cap:email.send"
    cap_money = "cap:payments.pay"

    # 1. set_autonomy records a rung Cell on the Weft.
    lid = au.set_autonomy(k, agent, cap_read, au.RUNG_READ_ONLY, reason="new capability")
    cell = k.weave().get(lid)
    assert cell is not None and cell.type == au.AUTONOMY and cell.content["level"] == 1, cell
    edge = k.weave().edges_from(lid, "autonomy_of")
    assert edge and edge[0]["dst"] == cap_read, edge
    line(f"  set_autonomy({cap_read}, rung 1) → {au.AUTONOMY} Cell {lid[:8]} "
         f"autonomy_of→{cap_read} (recorded ✓)")

    # 2. Rung 1 (read-only): REFUSES a write/effect, but a READ still executes.
    refused = au.decide(k, agent, cap_read, effect_class=au.IRREVERSIBLE)
    allowed = au.decide(k, agent, cap_read, effect_class=au.READ)
    assert refused["verdict"] == au.REFUSE, refused
    assert allowed["verdict"] == au.EXECUTE, allowed
    line(f"  rung 1 · IRREVERSIBLE → {refused['verdict'].upper()}; "
         f"READ → {allowed['verdict'].upper()}  ({refused['reason']})")

    # 3. Rung 2 (draft & suggest): PROPOSE — nothing executes.
    au.set_autonomy(k, agent, cap_send, au.RUNG_PROPOSE)
    prop = au.decide(k, agent, cap_send, effect_class=au.REVERSIBLE)
    assert prop["verdict"] == au.PROPOSE, prop
    assert "decision" in prop and k.weave().get(prop["decision"]) is not None  # recorded
    line(f"  rung 2 · REVERSIBLE → {prop['verdict'].upper()} (nothing executes; "
         f"decision {prop['decision'][:8]} on the Weft)")

    # 4. Rung 3 (supervised + gates): per-effect_class — REVERSIBLE executes, FINANCIAL pauses.
    au.set_autonomy(k, agent, cap_money, au.RUNG_SUPERVISED)
    rev = au.decide(k, agent, cap_money, effect_class=au.REVERSIBLE)
    fin = au.decide(k, agent, cap_money, effect_class=au.FINANCIAL)
    irr = au.decide(k, agent, cap_money, effect_class=au.IRREVERSIBLE)
    assert rev["verdict"] == au.EXECUTE, rev
    assert fin["verdict"] == au.REQUIRE_APPROVAL, fin
    assert irr["verdict"] == au.REQUIRE_APPROVAL, irr
    line(f"  rung 3 · REVERSIBLE → {rev['verdict'].upper()}; "
         f"FINANCIAL → {fin['verdict'].upper()}; IRREVERSIBLE → {irr['verdict'].upper()} "
         "(steps at different rungs by stakes)")

    # 5. Promotion is EARNED — and not before. With no track record, promote refuses.
    before = au.promote(k, agent, cap_send)
    assert before["promoted"] is False, before
    assert au.level_of(k, agent, cap_send) == au.RUNG_PROPOSE, "rung must not move without evidence"
    line(f"  promote(rung 2) with no track record → REFUSED "
         f"({before['reason'].split('—')[0].strip()})")

    # Build a strong track record: resolved wagers that mostly HIT, clearing the threshold.
    for i in range(5):
        w = wv.wager(k, f"autonomy track-record bet {i}", prediction=100, confidence=900_000)
        wv.verdict(k, w, observed=100, tolerance=5)            # hits
    tr = au.track_record(k)
    assert tr["hit_rate"] is not None and tr["hit_rate"] >= au.PROMOTE_HIT_RATE, tr
    after = au.promote(k, agent, cap_send)
    assert after["promoted"] is True and after["to"] == au.RUNG_SUPERVISED, after
    line(f"  track record {tr['hit_rate']/wv.FULL:.0%} over {tr['resolved']} wagers ≥ "
         f"{au.PROMOTE_HIT_RATE/wv.FULL:.0%} → promote rung 2 → {after['to']} (EARNED ✓)")

    # 6. Demotion is INSTANT — no evidence required (the Morta reflex).
    dem = au.demote(k, agent, cap_send, reason="anomaly detected")
    assert dem["demoted"] and dem["to"] == au.RUNG_READ_ONLY and dem["from"] == au.RUNG_SUPERVISED, dem
    assert au.level_of(k, agent, cap_send) == au.RUNG_READ_ONLY
    line(f"  demote → rung {dem['from']} ⇒ {dem['to']} INSTANTLY (no evidence needed): "
         f"{dem['reason']}")

    # 7. A manual pin is honored above evidence — the owner holds the rung.
    au.pin(k, agent, cap_send, au.RUNG_MONITORED, reason="owner trusts this lane")
    assert au.level_of(k, agent, cap_send) == au.RUNG_MONITORED and au.is_pinned(k, agent, cap_send)
    pinned_try = au.promote(k, agent, cap_send)               # even with a great track record
    assert pinned_try["promoted"] is False and "pinned" in pinned_try["reason"], pinned_try
    assert au.level_of(k, agent, cap_send) == au.RUNG_MONITORED, "a pinned rung must not move"
    line(f"  pin(rung 4) honored; evidence-driven promote → REFUSED (pin holds the rung ✓)")

    line("  → autonomy is a per-(agent, capability) rung, gated per effect_class; "
         "earned up by track record, demoted instantly, pinnable by the owner.")
