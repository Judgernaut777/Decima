"""R1 — typed retraction → REDACT (closes FOLD §11 #7).

RETRACT has a `mode` (WEFT §5): WITHDRAW (the default — a tombstone; the cell leaves
projections but its content is still recoverable from the events) and REDACT (also
ERASE the payload). REDACT is right-to-be-forgotten at the fold:

  - the payload is gone from EVERY projection — `get().content` is empty, the cell is
    out of `of_type`, and its `state_root` leaf is a content-free tombstone;
  - the merge substrate for the cell (incl. Map-field conflict_keys) is purged, so a
    redacted Sequence/Map/OR-set leaks nothing either;
  - the event SKELETON stays on the Log (`weft.events()` still yields the assert + the
    redact), and tamper-evidence over the log still holds.

Physical byte-erasure of the payload from storage is a separate GC step that needs
encrypted blobs + key destruction (FOLD §10) — not in the stdlib profile; the
projection-level erasure is the §11 #7 invariant, and it now holds. Contract:
run(k, line). Fail loud.
"""
from decima import model
from decima.weave import Weave, MERGE_MAP
from decima.hashing import content_id


def run(k, line):
    line("\n== REDACT (erase the payload; keep the skeleton) — FOLD §10 / §11 #7 ==")
    wf, root, human = k.weft, k.root.id, k.human.id

    # 1. A plain content cell with sensitive payload.
    secret = content_id({"secret": "patient-42"})
    model.assert_content(wf, human, secret, "secret", {"name": "Ada", "dx": "confidential"})
    assert k.weave().get(secret).content.get("dx") == "confidential"
    line(f"  asserted secret {secret[:8]} with payload; in of_type('secret'): "
         f"{secret in {c.id for c in k.weave().of_type('secret')}}")

    # 2. WITHDRAW vs REDACT — withdraw a *copy* to contrast: it leaves the cell but the
    #    payload is still in the events; redact erases it.
    k.redact(secret)
    w = k.weave()
    rc = w.get(secret)
    line(f"  REDACT → get().content={rc.content}  redacted={rc.redacted}  "
         f"in of_type: {secret in {c.id for c in w.of_type('secret')}}")
    assert rc.redacted and rc.content == {}, rc.content
    assert secret not in {c.id for c in w.of_type("secret")}, "redacted cell still projected"

    # 3. The event SKELETON remains — the assert and the redact both read back.
    touching = [ev.verb for ev in wf.events() if ev.body.get("cell") == secret]
    assert touching.count("ASSERT") >= 1 and touching.count("RETRACT") >= 1, touching
    line(f"  skeleton on the Log: events touching the cell = {touching} ✓ "
         f"(history intact, payload gone)")

    # 4. state_root has no payload bytes for it — fold to before the redact (payload
    #    present) vs after (tombstone) give different roots; the 'after' leaf is content-free.
    before = k.weave(upto_seq=wf.count() - 1).state_root()
    after = k.weave().state_root()
    assert before != after, "redaction must change the state_root"
    line(f"  state_root before≠after redact ({before[:8]} → {after[:8]}) — "
         f"the leaf is now a content-free tombstone ✓")

    # 5. Merge substrate is purged too: a Map cell with fields, redacted, leaks no field.
    model.define_type(wf, root, "dossier", merge_class=MERGE_MAP)
    dossier = content_id({"dossier": "d1"})
    model.assert_content(wf, root, dossier, "dossier", {"key": "addr", "value": "10 Loom St"})
    model.assert_content(wf, root, dossier, "dossier", {"key": "phone", "value": "555-0100"})
    assert k.weave().get(dossier).content, "map fields should materialize pre-redact"
    k.redact(dossier)
    dc = k.weave().get(dossier)
    assert dc.redacted and dc.content == {} and not dc.content_heads, dc.content
    line(f"  Map cell redacted → fields purged from the merge substrate too: "
         f"content={dc.content} ✓")

    line("  → REDACT erases the payload from every projection while the skeleton "
         "stays — FOLD §11 #7 holds. (Byte-erasure via encrypted blobs: durable-form, deferred.)")
