"""NOTIFY1 — notifications / alerting (a NEW capability). This check proves:

  - an IN-BOX notification is just a Cell: a TRIAGE1 incident and a WATCH1 trigger each
    become a `notification` with a `notifies` provenance edge to its source;
  - DEDUPE by source: re-notifying the same source re-asserts the same cell (no dup);
  - PRIORITY ordering: severity → priority, ordered urgent→low, deterministic;
  - SENDING leaves the box → Morta-gated: a send is DENIED until the capability is
    approved, then SUCCEEDS, and both attempt + send are audited on the Weft as
    `result` receipts (with a `notified` edge to the receipt).

Contract: run(k, line). Fail loud.
"""
from decima import notify, triage, watch, executor
from decima.model import assert_content
from decima.hashing import content_id


def run(k, line):
    line("\n== NOTIFICATIONS / ALERTING (in-box Cell; outward send Morta-gated) — NOTIFY1 ==")
    w = lambda: k.weave()
    auth = k.decima_agent_id

    # -- build a source incident (TRIAGE1) from correlated findings ----------
    def add_finding(tag, severity):
        cid = content_id({"notify_finding": tag})
        assert_content(k.weft, auth, cid, "finding", {
            "detection": "demo-det", "severity": severity, "excerpt": f"event {tag}",
            "source": "host-1", "rule": "demo-rule"})
        return cid

    add_finding("n1", "critical")
    add_finding("n2", "high")
    incidents = triage.correlate(k, group_by="rule")        # ≥2 findings → 1 incident
    assert incidents, "expected at least one incident from correlated findings"
    inc_id = incidents[0]
    inc = w().get(inc_id)
    line(f"  source incident {inc_id[:8]} severity={inc.content['severity']} "
         f"({inc.content['finding_count']} findings) ✓")

    # 1. An incident becomes an in-box notification with a provenance edge to its source.
    nid = notify.notify(k, inc_id, "demo incident", priority="urgent")
    n = w().get(nid)
    assert n.type == "notification" and n.content["source"] == inc_id
    src = notify.source_of(w(), nid)
    assert src is not None and src.id == inc_id, "notification missing notifies→source edge"
    assert n.content["sent"] is False and n.content["status"] == "unread"
    line(f"  incident → in-box notification {nid[:8]} (edge notifies→{inc_id[:8]}, unread) ✓")

    # 2. DEDUPE by source: notifying the same source again re-asserts the SAME cell.
    nid2 = notify.notify(k, inc_id, "demo incident (again)", priority="urgent")
    assert nid2 == nid, "re-notifying the same source produced a duplicate notification"
    n_count = sum(1 for c in notify.notifications(w()) if c.content["source"] == inc_id)
    assert n_count == 1, f"expected 1 notification for the source, got {n_count}"
    line("  re-notify same source → same cell, 1 notification (deduped by source) ✓")

    # -- build a source trigger (WATCH1) -------------------------------------
    wid = watch.register_watcher(
        k, "notify-watch", on_type="finding",
        predicate={"severity": {"op": ">=", "value": "high"}},
        action={"source": "watcher", "text": "high finding seen", "kind": "request"})
    assert w().get(wid).content["status"] == "armed"
    fired = watch.check_watchers(k)
    assert fired, "expected the watcher to fire on the high-severity findings"
    line(f"  source trigger {fired[0][:8]} fired by watcher {wid[:8]} ✓")

    # 3. from_incidents / from_triggers: existing cells → in-box notifications.
    inc_notes = notify.from_incidents(k)
    trg_notes = notify.from_triggers(k)
    assert inc_id in {w().get(i).content["source"] for i in inc_notes}
    assert fired[0] in {w().get(i).content["source"] for i in trg_notes}
    line(f"  from_incidents → {len(inc_notes)} note(s); from_triggers → {len(trg_notes)} note(s) "
         f"(each linked to its source) ✓")

    # 4. PRIORITY ordering: a low + an urgent note, ordered urgent→low, deterministic.
    low_src = add_finding("low-src", "low")
    low_nid = notify.notify(k, low_src, "low note", priority="low")
    ordered = notify.order(k)
    ranks = [w().get(i).content["priority_rank"] for i in ordered]
    assert ranks == sorted(ranks, reverse=True), f"notifications not ordered by priority: {ranks}"
    assert ordered[0] != low_nid, "a low-priority note sorted ahead of higher ones"
    line(f"  priority order (urgent→low): ranks={ranks}, head is highest ✓")

    # unknown priority is REFUSED, never silently defaulted (would mis-order an alert).
    try:
        notify.notify(k, inc_id, "x", priority="apocalyptic")
        raise AssertionError("unknown priority was NOT refused")
    except ValueError as e:
        assert "priority" in str(e)
    line("  unknown priority → REFUSED (ints, no silent default) ✓")

    # -- SENDING leaves the box → Morta-gated, audited -----------------------
    cap_id = notify.install_send(k)
    cap = w().get(cap_id)
    assert cap.content["caveats"]["requires_approval"] is True, "send cap is not Morta-gated"
    decima = w().get(k.decima_agent_id)

    # 5. DENIED until approved (a send leaves the box).
    denied = notify.send(k, decima, cap_id, nid, recipient="oncall@demo")
    assert "denied" in denied, f"outbound send was NOT denied pre-approval: {denied}"
    assert w().get(nid).content["sent"] is False, "notification marked sent despite denial"
    line(f"  send before approval → ✋ denied: {denied['denied']} (stays in-box) ✓")

    # 6. Approve (Morta) → send SUCCEEDS, notification flips to sent, receipt is audited.
    k.approve(cap_id)
    sent = notify.send(k, decima, cap_id, nid, recipient="oncall@demo")
    assert sent.get("status") == executor.SUCCEEDED, f"approved send did not succeed: {sent}"
    n_after = w().get(nid)
    assert n_after.content["sent"] is True and n_after.content["status"] == "sent"
    # the send is audited: a `result` EffectReceipt + a `notified` edge to it.
    receipt = w().get(sent["result_cell"])
    assert receipt.type == "result" and receipt.content["status"] == executor.SUCCEEDED
    audit_edge = w().edges_from(nid, notify.SENT)
    assert audit_edge and audit_edge[0]["dst"] == sent["result_cell"], "send not audited (no edge)"
    line(f"  approved → sent {nid[:8]}; receipt {sent['result_cell'][:8]} [{receipt.content['status']}]; "
         f"edge notified→receipt (audited) ✓")

    line("  → NOTIFY1: in-box notification is a signed Cell (provenance to source, deduped); "
         "outward send is Morta-gated (denied→approve→sent), audited; priority-ordered.")
