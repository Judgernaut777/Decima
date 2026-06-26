"""PHOTOS1 — a photo library / gallery: content-addressed photo refs (stub blobs,
no real bytes), album membership as EDGES on the Weft, tag + by_tag queries, and an
outward SHARE that is Morta-gated (denied → approve → shared) and audited as an
EffectReceipt. Sharing leaves the box → it is the gated, audited part; the library
and tags are DATA on the Weft.

Runs on its OWN fresh Kernel (it registers an effect and forges a capability), so it
stays out of the shared kernel's state. Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import photos, executor, audit
from decima.kernel import Kernel


def run(_k, line):
    line("\n== PHOTOS / GALLERY (content-addressed refs · albums · tags · Morta-gated share) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated
    cap_id = photos.install_gallery(k)
    decima = lambda: k.weave().get(k.decima_agent_id)

    # ---- add photos: content-addressed refs, INTEGER taken_at on the Weft --
    p1 = photos.add_photo(k, "blob:beach", taken_at=100, tags=["beach", "summer"])
    p2 = photos.add_photo(k, "blob:peak", taken_at=200, tags=["mountain"])
    p3 = photos.add_photo(k, "blob:dog", taken_at=300, tags=["summer"])
    for pid in (p1, p2, p3):
        c = k.weave().get(pid)
        assert c is not None and c.type == "photo", pid
        assert c.content["ref"].startswith("blob:"), c.content          # a content-addressed ref
        assert isinstance(c.content["taken_at"], int) and not isinstance(c.content["taken_at"], bool), \
            f"taken_at must be an int: {c.content!r}"
    assert photos.add_photo(k, "blob:beach", taken_at=100) == p1, "add_photo idempotent by ref"
    try:
        photos.add_photo(k, "blob:bad", taken_at=3.5)                    # float time → refused
        raise AssertionError("float taken_at must be refused")
    except ValueError:
        pass
    line("  added 3 photos (content-addressed refs; int taken_at); float time refused; idempotent ✓")

    # ---- build an album: membership is typed EDGES on the Weft -------------
    alb = photos.create_album(k, "Trip")
    photos.add_to_album(k, alb, p1)
    photos.add_to_album(k, alb, p2)
    photos.add_to_album(k, alb, p2)                                      # dup → folded to one edge
    in_album = {c.id for c in photos.album_photos(k, alb)}
    assert in_album == {p1, p2}, in_album                                # exactly the two added
    assert p3 not in in_album, "the dog photo is not in the Trip album"
    line(f"  album 'Trip' contains {len(in_album)} photos via edges (dup deduped) ✓")

    # ---- tag + by_tag query returns the right SET --------------------------
    photos.tag(k, p2, ["summer"])                                        # mountain photo now also summer
    summer = {c.id for c in photos.by_tag(k, "summer")}
    assert summer == {p1, p2, p3}, summer                               # all three carry 'summer'
    beach = {c.id for c in photos.by_tag(k, "beach")}
    assert beach == {p1}, beach                                         # only the beach photo
    assert "summer" in k.weave().get(p2).content["tags"], "tag persisted as a new LWW version"
    lib = {c.id for c in photos.library(k)}
    assert lib == {p1, p2, p3}, lib                                     # the whole library
    line(f"  tag+by_tag: 'summer'={len(summer)} 'beach'={len(beach)}; library()={len(lib)} ✓")

    # ---- SHARE is Morta-gated: DENIED until approved -----------------------
    r0 = photos.share(k, decima(), cap_id, p1, to="alice@example.com")
    assert "denied" in r0 and "approval" in r0["denied"].lower(), r0    # outward effect blocked
    line(f"  pre-approval: share(beach→alice) DENIED — {r0['denied']}")

    k.approve(cap_id)                                                   # a human/Morta approves
    line("  (a human approves the SHARING capability — Morta gate)")

    # ---- after approval the share SUCCEEDS and is audited on the Weft ------
    r1 = photos.share(k, decima(), cap_id, p1, to="alice@example.com")
    assert r1["status"] == executor.SUCCEEDED and not r1.get("denied"), r1
    receipt = k.weave().get(r1["result_cell"])
    assert receipt.content["effect_class"] == photos.SHARING            # outward SHARING class
    assert receipt.content["status"] == executor.SUCCEEDED
    assert receipt.content["ref"] == k.weave().get(p1).content["ref"], receipt.content
    assert receipt.content["to"] == "alice@example.com", receipt.content
    line(f"  approved: share → receipt {r1['result_cell'][:8]} "
         f"(class={receipt.content['effect_class']}, out={receipt.content['out']!r}) ✓")

    # ---- the share is on the signed Weft (audited, verifiable) -------------
    trail = audit.audit_trail(k, r1["result_cell"])
    assert trail["verifiable"] and trail["count"] >= 1, trail
    line(f"  audit: {trail['count']} verified event(s) on the share receipt "
         f"(verifiable={trail['verifiable']}) ✓")
