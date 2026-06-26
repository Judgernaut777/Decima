"""TRACING1 — observability: one trace = one INVOKE causal chain over the Weft DAG.

Proves (distinct from AUDIT1's compliance lens and TIMELINE1's activity lens):
  - a real INVOKE is driven through a PUBLIC effect (integrate_tool + invoke, via the
    messaging rail), then trace() reconstructs its causal SPAN TREE: the INVOKE as the
    root span, its EffectReceipt as a child (linked by both the parents DAG and the
    receipt's explicit `content.of` back-reference), and the downstream asserts that
    followed — each span attributed to its signing author + authorizing capability;
  - spans() flattens the tree in causal (seq) order, root first, parents before children;
  - root_cause() walks BACK from a downstream event to the originating cause, and the
    INVOKE is on that chain (the receipt's cause IS the INVOKE);
  - the trace is READ-ONLY (the Weft event count is unchanged) + DETERMINISTIC (recompute
    yields the identical tree) + TAMPER-EVIDENT (a clean Weft reads back verifiable=True);
  - structured_log() renders recent events as leveled, structured records.

Runs on its OWN fresh Kernel (it forges a COMMS rail and sends a message to create a real
INVOKE → receipt → downstream chain) so it stays out of the shared kernel's state. Read-only
over the Weft; never appends; never edits a core file. Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import tracing, messaging, executor
from decima.weft import INVOKE
from decima.kernel import Kernel


def run(_k, line):
    line("\n== TRACING / OBSERVABILITY (one trace = one INVOKE causal chain over the Weft) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated
    decima = lambda: k.weave().get(k.decima_agent_id)

    # ---- drive a REAL INVOKE through a public effect (integrate_tool + invoke) ----
    cap_id = messaging.install_rail(k)               # registers message.send, grants Decima
    k.approve(cap_id)                                # Morta gate — approve, then send
    res = messaging.send(k, decima(), cap_id, "alice@example.com", "ping")
    assert res["status"] == executor.SUCCEEDED, res  # the effect ran → receipt landed
    receipt = res["result_cell"]

    # find the INVOKE event the kernel signed for this effect
    invoke_event = next(ev.id for ev in k.weft.events() if ev.verb == INVOKE)
    events_before = k.weft.count()

    # ---- (1) trace: the causal span tree rooted at the INVOKE ---------------
    tr = tracing.trace(k, invoke_event)
    assert tr["found"] and tr["verb"] == INVOKE, tr
    assert tr["count"] >= 2, ("trace must reach INVOKE + its receipt (+ downstream)", tr)
    root = tr["spans"][0]
    assert root["event"] == invoke_event and root["depth"] == 0 and root["parent"] is None, root
    assert root["verb"] == INVOKE and root["authorized_by"] == cap_id, root  # provenance of power
    # the receipt is a downstream span, attributed to the EXECUTOR principal
    rcpt_span = next(s for s in tr["spans"] if s["event"] == _receipt_event(k, receipt))
    assert rcpt_span["depth"] >= 1 and rcpt_span["parent"] is not None, rcpt_span
    assert rcpt_span["author"] == k.executor.id, rcpt_span
    # every span carries attribution (author + author_name) + provenance (event id)
    for s in tr["spans"]:
        assert s["event"] and s["author"] and s["author_name"], s
        assert s["verb"] in ("ASSERT", "RETRACT", "INVOKE", "ATTEST"), s
    for ln in tracing.summary(tr):
        line("  " + ln)

    # ---- (2) spans: flat ordered, root first, parents before children -------
    flat = tracing.spans(k, tr)
    assert flat == tr["spans"] and flat[0]["event"] == invoke_event, flat
    seqs = [s["seq"] for s in flat]
    assert seqs == sorted(seqs), ("spans in causal seq order", seqs)
    # a child never precedes its parent in the flat order (a valid span tree)
    pos = {s["event"]: i for i, s in enumerate(flat)}
    for s in flat:
        if s["parent"] is not None:
            assert pos[s["parent"]] < pos[s["event"]], ("parent precedes child", s)
    line(f"  spans flat: {len(flat)} span(s), max depth "
         f"{max(s['depth'] for s in flat)} (parents precede children) ✓")

    # ---- (3) root_cause: walk back from the receipt to the originating cause -
    rc = tracing.root_cause(k, _receipt_event(k, receipt))
    assert rc["found"] and rc["depth"] >= 2, rc
    chain_events = [c["event"] for c in rc["chain"]]
    assert invoke_event in chain_events, ("the INVOKE is on the receipt's causal chain", rc)
    # the receipt's IMMEDIATE cause is the INVOKE (content.of back-reference honored)
    inv_pos = chain_events.index(invoke_event)
    assert chain_events[inv_pos + 1] == _receipt_event(k, receipt), rc
    # the chain is ordered cause-first (ascending seq)
    assert [c["seq"] for c in rc["chain"]] == sorted(c["seq"] for c in rc["chain"]), rc
    for ln in tracing.summary(rc):
        line("  " + ln)

    # ---- (4) read-only: tracing appended NOTHING to the Weft ----------------
    assert k.weft.count() == events_before, ("tracing is read-only", events_before, k.weft.count())
    line(f"  read-only: Weft event count unchanged at {events_before} (no append) ✓")

    # ---- (5) deterministic: recompute yields the identical tree -------------
    tr2 = tracing.trace(k, invoke_event)
    assert tr2["spans"] == tr["spans"] and tr2["count"] == tr["count"], "trace not deterministic"
    rc2 = tracing.root_cause(k, _receipt_event(k, receipt))
    assert rc2["chain"] == rc["chain"], "root_cause not deterministic"
    line("  deterministic: recompute of trace + root_cause is byte-identical ✓")

    # ---- (6) structured_log: leveled, structured records --------------------
    log = tracing.structured_log(k, last=4)
    assert log["count"] == 4, log
    assert any(r["verb"] == INVOKE and r["level"] == "NOTICE" for r in
               tracing.structured_log(k)["records"]), "INVOKE leveled NOTICE"
    for r in log["records"]:
        assert r["level"] and r["event"] and r["author_name"], r
    for ln in tracing.summary(log):
        line("  " + ln)

    # ---- (7) tamper-evidence: a clean Weft reads back verifiable ------------
    assert tr["verifiable"] and tr["error"] is None, tr
    assert rc["verifiable"] and log["verifiable"], (rc, log)
    line("  tamper-evidence: events() recomputed id + verified sig on read — "
         "trace VERIFIABLE ✓")


def _receipt_event(k, receipt_cell):
    """The Weft event id that ASSERTed the result/receipt cell (its provenance handle)."""
    cell = k.weave().get(receipt_cell)
    return cell.provenance[-1]
