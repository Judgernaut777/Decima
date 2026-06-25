"""PATTERN1 — the nine agentic architecture patterns as a recorded, selectable decision.

There is no single best multi-agent shape; the guide picks one by task features (two
axes — predictability × context-sharing — plus parallelism / quality / regulatory).
This check proves:
  - all NINE patterns are registered with the guide's metadata;
  - select() DETERMINISTICALLY picks the right pattern for several distinct task
    shapes (unpredictable→orchestrator-worker, fixed-stages→pipeline,
    quality-critical→evaluator-optimizer, simple→single-agent, domain-dispatch→router);
  - a REGULATORY task is NEVER assigned a swarm (or mesh) — auditability needs central control;
  - a MANUAL override is honored AND recorded with who/why (and what it overrode);
  - the choice is deterministic (recompute matches) and lives on the Weft.

Contract: run(k, line). Fail loud.
"""
from decima import patterns as P


def run(k, line):
    line("\n== AGENTIC ARCHITECTURE PATTERNS (deterministic selector + override) — PATTERN1 ==")
    sel = P.make_selector()

    # 1. All NINE patterns registered, each with the two-axis metadata.
    assert set(sel.patterns) == set(P.PATTERN_ORDER), sel.patterns.keys()
    assert len(sel.patterns) == 9, len(sel.patterns)
    for name in P.PATTERN_ORDER:
        pat = sel.get(name)
        assert pat.predictability in (P.PREDEFINED, P.EMERGENT), pat
        assert pat.context_sharing in (P.ISOLATED, P.SHARED), pat
        assert isinstance(pat.parallel, bool) and isinstance(pat.cost, int), pat
        assert pat.when_use and pat.when_avoid, pat
    line(f"  registered {len(sel.patterns)} patterns: " + ", ".join(P.PATTERN_ORDER[:5]) + " …")

    # 2. Deterministic selection for several distinct task SHAPES.
    cases = [
        ("unpredictable",  P.Task("research sweep", emergent_subtasks=True), P.ORCHESTRATOR_WORKER),
        ("fixed-stages",   P.Task("etl run", fixed_stages=True),             P.PIPELINE),
        ("quality-crit",   P.Task("legal brief", quality_critical=True),     P.EVALUATOR_OPTIMIZER),
        ("simple",         P.Task("echo a date"),                            P.SINGLE_AGENT),
        ("domain-dispatch",P.Task("triage a request", domain_dispatch=True), P.ROUTER),
    ]
    for label, task, expected in cases:
        ch = sel.select(task)
        assert ch.pattern == expected, (label, ch.pattern, "expected", expected)
        line(f"  {label:<16}→ {ch.pattern}")

    # 3. A REGULATORY task is NEVER a swarm/mesh (decentralized hand-off defeats audit).
    reg = P.Task("kyc compliance review", regulatory=True)
    rch = sel.select(reg)
    assert rch.pattern not in (P.SWARM, P.NETWORK_MESH), rch.pattern
    assert rch.pattern == P.HIERARCHICAL, rch.pattern   # gated tree = the auditable shape
    # …and across EVERY regulatory variant, swarm/mesh is never chosen.
    for flags in ({}, {"complex": True}, {"parallel": True},
                  {"predictability": P.EMERGENT, "context_sharing": P.SHARED}):
        assert sel.select(P.Task("reg", regulatory=True, **flags)).pattern \
            not in (P.SWARM, P.NETWORK_MESH), flags
    line(f"  regulatory       → {rch.pattern} (swarm/mesh NEVER chosen — auditability ✓)")

    # 4. Deterministic: recompute matches, and select_k records the same on the Weft.
    again = sel.select(reg)
    assert (again.pattern, again.reason) == (rch.pattern, rch.reason), (again, rch)
    ch4, cid = sel.select_k(k, reg)
    cell = k.weave().get(cid)
    assert cell is not None and cell.type == P.PATTERN_CHOICE, cell
    assert cell.content["pattern"] == rch.pattern == sel.select(reg).pattern
    assert cell.content["features"]["regulatory"] is True, cell.content
    prov = k.weave().edges_from(cid, P.CHOSE)
    assert prov and prov[0]["dst"] == P._pattern_tag(rch.pattern), prov
    line(f"  recorded choice on Weft: {P.PATTERN_CHOICE} {cid[:8]} "
         f"(reason cited, recompute matches ✓)")

    # 5. A MANUAL override — honored AND recorded with who/why + what it overrode.
    ov, ocid = sel.override(k, reg, P.SUPERVISOR,
                            who="alice", why="we have a dedicated compliance supervisor")
    assert ov.pattern == P.SUPERVISOR and ov.manual, ov
    assert ov.overridden_from == rch.pattern, ov          # what the selector wanted
    ocell = k.weave().get(ocid)
    assert ocell.content["manual"] is True, ocell.content
    assert ocell.content["who"] == "alice" and ocell.content["why"], ocell.content
    assert ocell.content["overridden_from"] == P.HIERARCHICAL, ocell.content
    line(f"  manual override → {ov.pattern} by {ocell.content['who']} "
         f"(was {ov.overridden_from}; who/why recorded ✓)")

    # 6. The audit trail: both choices for this task are on the Weft.
    trail = P.choices_on(k, "kyc compliance review")
    assert len(trail) == 2, len(trail)   # the auto-select + the override
    line(f"  → 9 patterns, deterministic selection, honored+recorded override, "
         f"{len(trail)} choices auditable on the Weft. The architecture is now a "
         f"first-class, recorded decision.")
