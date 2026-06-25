"""TIMELINE1 — the user-facing activity feed over the signed Weft.

Proves (distinct from AUDIT1's compliance lens):
  - timeline(k) renders recent events as ordered, human-facing activity entries,
    each with author + verb + the cell/effect touched + provenance (the event id);
  - entries are in causal (seq) order and `last=N` keeps the N most recent;
  - digest(k) groups those entries by verb / principal / cell-type with counts that
    reconcile to the timeline's total;
  - filtering by principal and by cell type both work (and compose with the digest);
  - reading is tamper-evident — a clean Weft yields verifiable=True and no error.

Runs on its OWN fresh Kernel (it forges a FINANCIAL rail and moves a little "money"
to create a varied INVOKE/receipt feed) so it stays out of the shared kernel's
state. Read-only over the Weft; never appends; never edits a core file.
Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import timeline, payments, executor
from decima.weft import INVOKE
from decima.kernel import Kernel


def run(_k, line):
    line("\n== ACTIVITY TIMELINE / DIGEST (user-facing feed over the signed Weft) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated
    decima = lambda: k.weave().get(k.decima_agent_id)

    # ---- create a varied, multi-author, multi-verb feed ---------------------
    cap_id = payments.install_rail(k, cap=100)   # forges a FINANCIAL capability
    k.approve(cap_id)
    r = payments.pay(k, decima(), cap_id, amount=40, payee="acme",
                     idempotency_key="ord-1")     # INVOKE + receipt (executor authors)
    assert r["status"] == executor.SUCCEEDED, r

    # ---- (1) timeline: ordered, human-facing entries with provenance --------
    tl = timeline.timeline(k)
    assert tl["count"] >= 3, tl
    seqs = [e["seq"] for e in tl["entries"]]
    assert seqs == sorted(seqs), ("timeline must be in causal seq order", seqs)
    for e in tl["entries"]:                       # every entry carries provenance
        assert e["author"] and e["verb"] and e["provenance"], e
        assert e["author_name"] and e["description"], e
        assert e["verb"] in ("ASSERT", "RETRACT", "INVOKE", "ATTEST"), e
    for ln in timeline.summary(tl):
        line("  " + ln)

    # ---- (2) last=N keeps the N most recent (newest last) -------------------
    tail = timeline.timeline(k, last=2)
    assert tail["count"] == 2, tail
    assert [e["seq"] for e in tail["entries"]] == seqs[-2:], (tail, seqs)
    line(f"  last=2 → most recent {tail['count']} entry(ies) "
         f"(seq {tail['entries'][0]['seq']}..{tail['entries'][-1]['seq']}) ✓")

    # ---- (3) digest: grouped counts that reconcile to the timeline total ----
    dg = timeline.digest(k)
    assert dg["total"] == tl["count"], (dg, tl["count"])
    assert sum(dg["by_verb"].values()) == dg["total"], dg
    assert sum(dg["by_principal"].values()) == dg["total"], dg
    assert sum(dg["by_cell_type"].values()) == dg["total"], dg
    # an outward effect happened, so the INVOKE verb is present and counted
    invoke_word = timeline._VERB_WORD[INVOKE]
    assert dg["by_verb"].get(invoke_word, 0) >= 1, dg
    for ln in timeline.summary(dg):
        line("  " + ln)

    # ---- (4) filter by PRINCIPAL: only that author's events -----------------
    by_exec = timeline.timeline(k, principal=k.executor.id)
    assert by_exec["count"] >= 1, by_exec
    assert all(e["author"] == k.executor.id for e in by_exec["entries"]), by_exec
    # the executor authored the receipt(s); the digest agrees
    dexec = timeline.digest(k, principal=k.executor.id)
    assert dexec["total"] == by_exec["count"], (dexec, by_exec["count"])
    assert list(dexec["by_principal"]) == [by_exec["entries"][0]["author_name"]], dexec
    line(f"  filter principal=executor → {by_exec['count']} signed event(s) "
         f"(all by one author) ✓")

    # ---- (5) filter by CELL TYPE: only events touching that type ------------
    by_cap = timeline.timeline(k, cell_type="capability")
    assert by_cap["count"] >= 1, by_cap
    assert all(e["cell_type"] == "capability" for e in by_cap["entries"]), by_cap
    # filtering narrows the feed: a type subset never exceeds the whole
    assert by_cap["count"] <= tl["count"], (by_cap["count"], tl["count"])
    line(f"  filter cell_type=capability → {by_cap['count']} entry(ies) "
         f"(all touch a capability cell) ✓")

    # ---- (6) tamper-evidence: a clean Weft reads back verifiable ------------
    assert tl["verifiable"] and tl["error"] is None, tl
    assert dg["verifiable"] and dg["error"] is None, dg
    assert by_exec["verifiable"] and by_cap["verifiable"], (by_exec, by_cap)
    line("  tamper-evidence: events() recomputed id + verified sig on read — "
         "feed VERIFIABLE ✓")
