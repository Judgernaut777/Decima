"""Photos / gallery (PHOTOS1) — a library of content-addressed images with tags
and albums, where SHARING a photo outward is a Morta-gated, audited effect.

A *photo* is a Cell (Law 3): a content-addressed `ref` (the blob-id of the image —
NO real image bytes here, just the stub ref/hash), an INTEGER `taken_at` logical
time (Law: ints, not floats), and a set of `tags`. The image itself lives behind
its ref exactly like a `file` blob (FILES1): identity is the content-address, so
re-adding the same ref is one identity, and provenance falls out of the hash.

What this composes from primitives the kernel already has:

  • a `photo` Cell — content-addressed by its `ref`, idempotent re-adds, LWW versions
    (re-tagging asserts a new CONTENT version of the same cell id on the Weft);
  • an `album` Cell + typed membership EDGES (`album —contains→ photo`): album
    membership is graph structure folded onto both endpoints, not a side list;
  • tag/library queries (`by_tag`, `library`) folded straight off the Weave — tags
    and metadata that arrive from OUTSIDE are DATA, never instructions;
  • a "photos.share" executor effect — sending a photo OUT of the box (to another
    person/service) is an outward, hard-to-take-back effect, so it is **Morta-gated**
    (`requires_approval`): denied until a human/policy approves the capability, then
    audited as a full EffectReceipt on the Weft. The handler is a deterministic stub
    (no real network); an empty photo/recipient raises ExecError → a FAILED receipt.

LAW boundary (CAPABILITY_MAP B1): the library + tags live in the box (DATA on the
Weft). External streaming/sharing is the part that *leaves* the box — and anything
that leaves is an outward effect → Morta-gated + audited, exactly the payments
pattern (PAY1) but for an image instead of money.

Pure composition: registers its effect through the public `kernel.integrate_tool`
(→ `executor.register`) and writes cells/edges with the public `model.*` helpers —
it edits no kernel or other-module file.
"""
from decima import executor, model
from decima.hashing import content_id, nfc

PHOTO = "photo"                    # Cell type — one image (content-addressed ref)
ALBUM = "album"                    # Cell type — a named album
SHARE_EFFECT = "photos.share"      # the registered outbound effect name
CONTAINS = "contains"             # edge rel: album —contains→ photo
SHARING = "SHARING"               # outward effect_class (audit signal)


def photo_id(ref: str) -> str:
    """Content-address a photo by its blob ref, so the same image lands on ONE cell
    id (stable identity; LWW tag/metadata versions accrete on it)."""
    return content_id({"photo": nfc(str(ref))})


def _album_id(name: str) -> str:
    return content_id({"album": nfc(str(name))})


def _norm_tags(tags) -> list:
    """Tags from outside are DATA: normalize to a sorted, deduped list of strings.
    Order is canonical so the same set content-addresses identically."""
    if not tags:
        return []
    return sorted({nfc(str(t)) for t in tags})


def add_photo(k, ref, *, taken_at=None, tags=None) -> str:
    """Assert a `photo` Cell for a content-addressed `ref` and return its cell id.

    `ref` is the blob-id/content-address of the image (no real bytes — a stub ref,
    the storage sibling of a `file` content_hash). `taken_at` is an INTEGER logical
    time (Law: ints, not floats) — a non-int (or a float) is refused loudly.
    Idempotent by `ref`: re-adding the same image is ONE identity, and accreted
    tags/metadata are PRESERVED — re-adding never clobbers an existing photo's tags
    or taken_at; omitted args keep the head's values, supplied tags union in.
    Tags/metadata are DATA on the Weft, never instructions."""
    if taken_at is not None and (isinstance(taken_at, bool) or not isinstance(taken_at, int)):
        raise ValueError(f"taken_at must be an int logical time, got {taken_at!r}")
    ref = nfc(str(ref))
    pid = photo_id(ref)
    prior = k.weave().get(pid)
    prior_tags = list(prior.content.get("tags", [])) if (prior is not None and prior.type == PHOTO) else []
    prior_taken = prior.content.get("taken_at") if (prior is not None and prior.type == PHOTO) else None
    model.assert_content(k.weft, k.root.id, pid, PHOTO, {
        "ref": ref,
        "taken_at": taken_at if taken_at is not None else prior_taken,
        "tags": _norm_tags(prior_tags + list(tags or [])),
    })
    return pid


def create_album(k, name: str) -> str:
    """Assert an `album` Cell and return its id. Idempotent by name."""
    aid = _album_id(name)
    model.assert_content(k.weft, k.root.id, aid, ALBUM, {"name": nfc(str(name))})
    return aid


def add_to_album(k, album: str, photo: str) -> None:
    """Add a photo to an album as a typed membership EDGE
    (`album —contains→ photo`) folded onto both endpoints. Idempotent: the fold
    dedups identical (rel, src, dst) edges, so re-adding is a no-op."""
    model.assert_edge(k.weft, k.root.id, album, CONTAINS, photo)


def tag(k, photo: str, tags) -> str:
    """Add `tags` to a photo: assert a NEW CONTENT version of the same photo cell id
    (LWW), unioning the new tags with the existing set. Returns the photo id. Tags
    are DATA — adding a tag never makes a photo an instruction."""
    cell = k.weave().get(photo) or k.weave().get(photo_id(photo))
    if cell is None or cell.type != PHOTO:
        raise ValueError(f"not a photo cell: {photo!r}")
    merged = _norm_tags(list(cell.content.get("tags", [])) + list(tags or []))
    model.assert_content(k.weft, k.root.id, cell.id, PHOTO, {
        **cell.content, "tags": merged,
    })
    return cell.id


def get(k, ref_or_id: str):
    """The current `photo` cell, by ref or by id, or None."""
    w = k.weave()
    c = w.get(ref_or_id) or w.get(photo_id(ref_or_id))
    return c if (c is not None and c.type == PHOTO and not c.retracted) else None


def library(k) -> list:
    """Every (non-retracted) `photo` cell in the library — the query."""
    return k.weave().of_type(PHOTO)


def by_tag(k, tag: str) -> list:
    """Every `photo` cell whose tag set contains `tag` (the folded current version)."""
    t = nfc(str(tag))
    return [c for c in k.weave().of_type(PHOTO) if t in (c.content.get("tags") or [])]


def album_photos(k, album_cell) -> list:
    """The `photo` cells an album contains, by following its `contains` edges.
    `album_cell` may be a Cell or an album id."""
    w = k.weave()
    aid = getattr(album_cell, "id", album_cell)
    out = []
    for e in w.edges_from(aid, CONTAINS):
        c = w.get(e["dst"])
        if c is not None and c.type == PHOTO:
            out.append(c)
    return out


def _share_handler(impl, args: dict) -> dict:
    """The outbound share rail itself — a deterministic stub standing in for sending
    an image to another person/service. A real handler hands the blob to a transport
    over a network-pinned sandbox; here it confirms the send deterministically and
    echoes the photo ref + recipient for audit. A missing photo/recipient raises
    ExecError → a FAILED receipt: a definite no-effect, nothing left the box."""
    ref = nfc(str(args.get("ref", "")))
    to = nfc(str(args.get("to", "")))
    if not ref:
        raise executor.ExecError("photos.share requires a photo ref")
    if not to:
        raise executor.ExecError("photos.share requires a recipient")
    return {"out": f"shared {ref} to {to}", "ref": ref, "to": to}


def install_gallery(k, *, name: str = SHARE_EFFECT) -> str:
    """Register the `photos.share` effect and forge a SHARING capability granted to
    Decima: Morta `requires_approval` (sharing is an outward effect — denied until
    approved) and a sandbox profile allowing ONLY this effect with network on (to the
    transport). Returns the capability id."""
    caveats = {
        "effect_class": SHARING,
        "requires_approval": True,          # Morta gate — sharing leaves the box
        # SB1 sandbox: only photos.share may run under the cap; network on (to the
        # transport). The durable form pins egress to the share host.
        "sandbox": {"effects": [name], "network": True},
    }
    return k.integrate_tool(name, _share_handler, caveats=caveats)


def share(k, agent_cell, cap_id, photo, to: str) -> dict:
    """Share a photo OUT of the box via the Morta-gated capability/effect. Returns
    {status, result_cell, denied?, ref, to}.

    Flow: (invoke) Morta-gated + sandboxed via the cap; the kernel emits the
    EffectReceipt. Pre-approval the invoke is DENIED (nothing leaves the box); after
    `k.approve(cap_id)` it SUCCEEDS and the share is audited as a SHARING receipt on
    the Weft. A denial leaves no outward effect — a definite no-effect."""
    cell = photo if hasattr(photo, "id") else k.weave().get(photo) or k.weave().get(photo_id(photo))
    if cell is None or cell.type != PHOTO:
        raise ValueError(f"not a photo cell: {photo!r}")
    ref = cell.content.get("ref")
    res = k.invoke(agent_cell, cap_id, {"ref": ref, "to": nfc(str(to))})
    out = {"status": res.get("status"), "result_cell": res.get("result_cell"),
           "ref": ref, "to": nfc(str(to))}
    if "denied" in res:
        out["denied"] = res["denied"]
    return out
