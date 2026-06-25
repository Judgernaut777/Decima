"""WATCH1 — watchers / reactive triggers (native LOOM: condition → action).

A watcher is Decima's OWN trusted automation (the owner registered it): a condition over the
Weave (a cell type + a declarative predicate) + an action (a disposition brief). This check
proves:
  - register a watcher on a HIGH-severity `finding` — with no matching cell it does NOT fire;
  - create a matching cell → check_watchers fires EXACTLY ONE trigger, with a `triggered_by`
    edge to the match (audited), routed through disposition.dispose (still gated);
  - a NON-matching cell (low severity, or a finding that doesn't contain the watched text)
    never fires;
  - firing is IDEMPOTENT (re-running the check does not re-fire on the same match);
  - the predicate is a SAFE declarative match — an unknown operator is REFUSED, not eval'd.

Contract: run(k, line). Fail loud.
"""
from decima import watch
from decima.model import assert_content
from decima.hashing import content_id


def run(k, line):
    line("\n== WATCHERS / REACTIVE TRIGGERS (condition → gated action) — WATCH1 ==")
    w = lambda: k.weave()
    auth = k.decima_agent_id

    def add_finding(tag, severity, excerpt):
        cid = content_id({"watch_finding": tag})
        assert_content(k.weft, auth, cid, "finding", {
            "detection": "demo", "severity": severity, "excerpt": excerpt, "source": tag})
        return cid

    # 1. Register a watcher: fire on a HIGH-severity finding whose excerpt contains "breach".
    wid = watch.register_watcher(
        k, "high-sev-breach",
        on_type="finding",
        predicate={"severity": {"op": ">=", "value": "high"},
                   "excerpt": {"op": "~", "value": "breach"}},
        action={"source": "watcher", "text": "high-severity breach finding — open triage",
                "kind": "request"})
    assert w().get(wid).content["status"] == "armed"
    line(f"  registered watcher {wid[:8]} (on finding: severity>=high AND excerpt~'breach') ✓")

    # 2. No matching cell yet → does NOT fire.
    assert watch.check_watchers(k) == [], "watcher fired with no matching cell"
    line("  no matching finding → 0 triggers fired ✓")

    # 3. A NON-matching cell: low severity (predicate fails) → still no fire.
    add_finding("low-noise", "low", "a breach was mentioned in passing")
    # ...and a high-sev finding that does NOT contain the watched text.
    add_finding("high-other", "high", "disk space running low")
    assert watch.check_watchers(k) == [], "watcher fired on a non-matching cell"
    line("  low-sev + off-topic high-sev findings → still 0 triggers (predicate is AND) ✓")

    # 4. A MATCHING cell → fires EXACTLY ONE trigger, with an audited edge to the match.
    match_id = add_finding("real-breach", "critical", "data breach: secrets exfiltrated")
    fired = watch.check_watchers(k)
    assert len(fired) == 1, f"expected 1 trigger, got {len(fired)}"
    trig = w().get(fired[0])
    assert trig.type == "trigger" and trig.content["matched"] == match_id
    # provenance edge trigger → triggered_by → match (audited on the Weft).
    prov = w().edges_from(trig.id, "triggered_by")
    assert prov and prov[0]["dst"] == match_id, "trigger missing triggered_by edge to match"
    # the action was ROUTED THROUGH disposition (trusted request → a task), not bypassing it.
    d = w().get(trig.content["disposition"])
    assert trig.content["disposed_action"] == "task" and d.content["action"] == "task"
    line(f"  matching finding ({match_id[:8]}) → 1 trigger {trig.id[:8]}; "
         f"edge triggered_by→{match_id[:8]}; routed via disposition → {trig.content['disposed_action']} ✓")

    # 5. IDEMPOTENT: re-running the check does not re-fire on the same match.
    assert watch.check_watchers(k) == [], "watcher re-fired on an already-fired match"
    line("  re-check on the same Weave → 0 new triggers (idempotent, no self-DoS) ✓")

    # 6. A fresh non-matching cell after firing never fires either.
    add_finding("benign-2", "medium", "breach drill scheduled")  # medium < high → no
    assert watch.check_watchers(k) == [], "watcher fired on a sub-threshold cell"
    line("  later sub-threshold finding → still 0 triggers ✓")

    # 7. SAFE predicate: an unknown operator is REFUSED at registration (never eval'd from data).
    try:
        watch.register_watcher(k, "evil", on_type="finding",
                               predicate={"excerpt": {"op": "__import__", "value": "os"}},
                               action={"text": "x"})
        raise AssertionError("unknown operator was NOT refused")
    except ValueError as e:
        assert "operator" in str(e)
    line("  predicate with an unknown operator → REFUSED (declarative, never eval'd) ✓")
    line("  → watchers: owner-registered automation; condition over the Weave fires a gated "
         "disposition + an audited trigger; safe declarative predicates; idempotent firing.")
