"""Manifest packs — make plug-in capabilities SHAREABLE/DISTRIBUTABLE.

A single Decima instance describes its capabilities as declarative manifests
(see `manifest.py`). This module lets a set of those manifests be EXPORTED to a
portable, self-describing artifact (a "manifest pack" — plain JSON) and IMPORTED
elsewhere, so one instance can distribute a plug-in bundle and another can receive
it. The pack is deterministic and content-committed: a `digest` (via
`hashing.content_id`) fixes the exact manifest set, so tampering is detectable.

Import is FAIL-CLOSED: `verify_pack` must pass (digest matches AND every manifest
re-validates by rebuilding through `manifest.capability_manifest`) or NOTHING is
registered — a bad digest or a single malformed/tampered manifest rejects the whole
pack. Imported manifests are UNTRUSTED: any EFFECT-archetype manifest from an
untrusted pack is TIGHTENED with `requires_approval=True` (mirroring the MCP
untrusted-tighten-only rule) unless the source is explicitly trusted — an import can
only ever make a capability stricter, never auto-trust it.

And importing a manifest GRANTS NOTHING: register ≠ install. The imported pack
adds descriptions to the registry, but a handler must still be wired (via
`manifest.install`/`kernel.integrate_tool`) and `authorize` still gates every
invoke. Pure stdlib; composes public manifest/hashing/kernel APIs only.
"""
from decima.hashing import content_id, nfc
from decima import manifest as M

PACK_VERSION = 1

# The canonical field set of a manifest (as produced by `capability_manifest`),
# used to rebuild/re-validate a manifest on import without trusting its shape.
_FIELDS = ("name", "title", "description", "archetype", "effect_class",
           "input_schema", "output_schema", "caveats", "annotations",
           "source", "version", "tags")


def _rebuild(mc: dict) -> dict:
    """Re-validate a manifest content dict by rebuilding it through the canonical
    builder — a malformed manifest (bad archetype / non-int version / …) raises."""
    return M.capability_manifest(
        mc["name"], description=mc.get("description", ""),
        archetype=mc.get("archetype", "EFFECT"),
        effect_class=mc.get("effect_class", "READ"),
        input_schema=mc.get("input_schema"), output_schema=mc.get("output_schema"),
        caveats=mc.get("caveats"), annotations=mc.get("annotations"),
        source=mc.get("source", "builtin"), version=mc.get("version", 1),
        title=mc.get("title"), tags=mc.get("tags"))


def _digest(name: str, manifests: list) -> str:
    """The content-address committing to a pack's name + manifest set. Deterministic
    (sorted-key JSON, ints not floats), so the same manifests always yield the same
    digest — the integrity anchor a receiver recomputes."""
    return content_id({"pack": nfc(name), "version": int(PACK_VERSION),
                       "manifests": manifests}, kind="cell")


def export_pack(k, *, names=None, source=None, name="pack") -> dict:
    """Fold the live registry into a portable pack (optionally filtered by a set of
    `names` or by `source`). Returns a deterministic, JSON-serializable dict:
    {"pack", "version", "manifests": [<manifest content>...], "digest"}. The digest
    commits to the manifest set. Manifests are sorted (by name, version) so the
    artifact is reproducible regardless of registry order."""
    want = set(names) if names is not None else None
    manifests = []
    for c in M.registry(k):
        m = c.content
        if want is not None and m["name"] not in want:
            continue
        if source is not None and m["source"] != source:
            continue
        manifests.append({f: m.get(f) for f in _FIELDS})
    manifests.sort(key=lambda m: (m["name"], int(m["version"])))
    return {"pack": nfc(name), "version": int(PACK_VERSION),
            "manifests": manifests, "digest": _digest(name, manifests)}


def to_json(pack: dict) -> str:
    """Serialize a pack to sorted-key JSON — byte-stable (same pack → same text)."""
    import json
    return json.dumps(pack, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def from_json(text: str) -> dict:
    """Parse a pack from JSON. Validates it is a dict carrying a `manifests` list;
    anything malformed → ValueError (fail loud on a bad artifact)."""
    import json
    try:
        pack = json.loads(text)
    except (ValueError, TypeError) as e:
        raise ValueError(f"malformed pack JSON: {e}") from e
    if not isinstance(pack, dict):
        raise ValueError("pack must be a JSON object")
    if not isinstance(pack.get("manifests"), list):
        raise ValueError("pack must carry a 'manifests' list")
    return pack


def verify_pack(pack: dict) -> bool:
    """Integrity + validity check. True only if (a) the recomputed digest matches the
    committed one — so a field tampered without re-signing fails — AND (b) every
    manifest re-validates by rebuilding through `capability_manifest` and matches its
    stored form (a malformed or inconsistent manifest fails)."""
    if not isinstance(pack, dict):
        return False
    manifests = pack.get("manifests")
    if not isinstance(manifests, list):
        return False
    if _digest(pack.get("pack", "pack"), manifests) != pack.get("digest"):
        return False
    for mc in manifests:
        if not isinstance(mc, dict):
            return False
        try:
            rebuilt = _rebuild(mc)
        except (ValueError, KeyError, TypeError):
            return False
        if {f: mc.get(f) for f in _FIELDS} != rebuilt:
            return False
    return True


def import_pack(k, pack: dict, *, trust_source=False) -> dict:
    """Validate then register a pack's manifests into `k`. FAIL-CLOSED: if
    `verify_pack` fails (bad digest / malformed manifest) NOTHING is registered.

    SAFETY: an imported pack is UNTRUSTED — an EFFECT-archetype manifest is TIGHTENED
    with `requires_approval=True` unless `trust_source=True` (import may only make a
    capability stricter, never auto-trust it). Returns {"registered": [ids],
    "count": n}. Registration GRANTS NOTHING: the manifest is a description — a
    handler must still be installed and `authorize` still gates every invoke."""
    if not verify_pack(pack):
        return {"registered": [], "count": 0}
    registered = []
    for mc in pack["manifests"]:
        m = dict(mc)
        if not trust_source and m.get("archetype") == "EFFECT":
            cav = dict(m.get("caveats") or {})
            cav["requires_approval"] = True          # untrusted EFFECT: tighten only
            m["caveats"] = cav
        registered.append(M.register(k, m))
    return {"registered": registered, "count": len(registered)}
