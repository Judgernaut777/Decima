"""OFFICE1 — Office / docs editing: editable docs/sheets/slides as Cells.

Proves the clean split OFFICE1 makes:
  - CREATE a doc + a sheet → `office_doc` Cells (local; no gate);
  - EDIT each → a NEW LWW version, with EVERY prior version still on the Weft
    (provenance + versioning on the Log);
  - COMPUTE a sheet → a deterministic INTEGER aggregation (SUM), exact + replayable;
  - PUBLISH/SHARE is an OUTWARD effect → Morta-gated: DENIED until approved, then
    published, with a PUBLISH EffectReceipt on the Weft (audited).

Runs on its OWN fresh Kernel (it forges a PUBLISH capability + registers an outbound
effect — keep it out of the shared kernel's state). Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import office, executor
from decima.kernel import Kernel


def run(_k, line):
    line("\n== OFFICE (editable docs/sheets/slides · local edit+version · Morta-gated publish) — OFFICE1 ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)    # isolated
    cap_id = office.install_rail(k)
    decima = lambda: k.weave().get(k.decima_agent_id)

    # ---- (1) create a doc and a sheet — local, no gate ----------------------
    d = office.create(k, "doc", "Q3 Plan", body={"text": "draft outline"})
    s = office.create(k, "sheet", "Q3 Budget", body={"cells": {"A1": 100, "A2": 250}})
    dc, sc = office.get(k, d), office.get(k, s)
    assert dc.content["kind"] == "doc" and sc.content["kind"] == "sheet"
    assert dc.version == 1 and sc.version == 1, (dc.version, sc.version)
    assert sc.content["body"]["cells"] == {"A1": 100, "A2": 250}, sc.content
    assert all(isinstance(v, int) for v in sc.content["body"]["cells"].values())  # int law
    line(f"  created doc {d[:8]} (v{dc.version}) + sheet {s[:8]} (v{sc.version}) — "
         f"local edit, no Morta ✓")

    # ---- (2) edit → a new LWW version; every prior version stays on the Weft -
    office.edit(k, d, {"text": "revised outline + intro"})
    office.edit(k, s, {"A3": 300, "A1": 150})          # add a cell, change one
    dc2, sc2 = office.get(k, d), office.get(k, s)
    assert dc2.version == 2, dc2.version
    assert dc2.content["body"]["text"] == "revised outline + intro", dc2.content
    assert sc2.version == 2 and sc2.content["body"]["cells"] == {"A1": 150, "A2": 250, "A3": 300}

    dhist, shist = office.history(k, d), office.history(k, s)
    assert [h["version"] for h in dhist] == [1, 2], dhist        # both versions on the Weft
    assert dhist[0]["body"]["text"] == "draft outline"          # the ORIGINAL survives
    assert [h["version"] for h in shist] == [1, 2], shist
    assert shist[0]["body"]["cells"] == {"A1": 100, "A2": 250}   # pre-edit sheet survives
    line(f"  edited → doc v{dc2.version}, sheet v{sc2.version}; history keeps "
         f"{len(dhist)}+{len(shist)} versions (provenance on the Log) ✓")

    # ---- (3) compute — a deterministic integer aggregation (SUM) ------------
    total = office.compute(k, s)
    assert total == 150 + 250 + 300 == 700, total
    assert isinstance(total, int) and not isinstance(total, bool)   # int, never float
    assert office.compute(k, s) == total                            # deterministic replay
    line(f"  compute(sheet) = {total} (int, deterministic SUM of cell values) ✓")

    # a non-int sheet value is refused at edit time (the int law fails loud)
    try:
        office.edit(k, s, {"A4": 1.5})
        raise AssertionError("a float sheet value must be refused")
    except ValueError:
        pass
    line("  float sheet value → refused at edit (ints not floats) ✓")

    # ---- (4) publish/share is Morta-gated: DENIED → approve → published -----
    to = "drive://team/Q3"
    d0 = office.publish(k, decima(), cap_id, d, to)
    assert "denied" in d0 and "approval" in d0["denied"].lower(), d0   # Morta gate
    assert d0.get("result_cell") is None                              # nothing published
    assert not any(c.content.get("effect_class") == office.PUBLISH
                   for c in k.weave().of_type(office.RESULT)
                   if c.content.get("status") == executor.SUCCEEDED)   # nothing left the box
    assert office.get(k, d).content.get("published") is False         # doc not marked out
    line(f"  pre-approval: publish → DENIED — {d0['denied']}")

    k.approve(cap_id)                                                 # human / Morta approves
    line("  (a human approves the PUBLISH capability — Morta gate)")

    p1 = office.publish(k, decima(), cap_id, d, to)
    assert p1["status"] == executor.SUCCEEDED and not p1.get("denied"), p1
    receipt = k.weave().get(p1["result_cell"])                       # audited on the Weft
    assert receipt.content["effect_class"] == office.PUBLISH
    assert receipt.content["status"] == executor.SUCCEEDED
    pub = office.get(k, d)
    assert pub.content["published"] is True and pub.content["published_to"] == to
    assert pub.version == 3, pub.version                             # publish recorded a version
    line(f"  approved: publish → receipt {p1['result_cell'][:8]} "
         f"(class={receipt.content['effect_class']}, status={receipt.content['status']}) — audited ✓")

    # ---- (5) a malformed publish is a definite no-effect, never a crash -----
    bad = office.publish(k, decima(), cap_id, d, "")                  # empty target
    assert "denied" in bad and bad["status"] == executor.FAILED, bad
    line("  empty target → FAILED receipt (definite no-effect), not a crash ✓")
    line("  → editing is local + versioned on the Weft; compute is exact-int; "
         "publish/share is Morta-gated, sandboxed, and audited.")
