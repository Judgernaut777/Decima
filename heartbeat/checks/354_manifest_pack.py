"""Manifest packs — SHAREABLE/DISTRIBUTABLE plug-in bundles (export/import).

Capabilities are declarative manifests (see 338); this makes a SET of them portable.
A pack is a deterministic, content-committed JSON artifact: export the registry to a
pack (with a digest), ship it, and import it into another Decima — validated and
fail-closed. This check proves:
  - `export_pack` folds the registry into a pack with a `digest` committing to the
    manifest set; `to_json`/`from_json` round-trip BYTE-STABLY;
  - `verify_pack` is True for the intact pack; TAMPERING a manifest field (without
    re-signing the digest) makes `verify_pack` False AND `import_pack` register
    NOTHING (fail closed — a bad pack grants nothing);
  - importing a clean pack into a FRESH kernel makes the manifests appear in that
    kernel's registry;
  - an untrusted EFFECT manifest is TIGHTENED on import (`requires_approval=True`),
    while `trust_source=True` preserves its caveats (import can only tighten);
  - importing a manifest does NOT create a capability — the registry has it, but
    `of_type("capability")` does not (register ≠ install).

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import manifest as M
from decima import manifest_pack as P


def _fresh():
    return Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)


def run(k, line):
    line("\n== MANIFEST PACK (shareable / distributable plug-in bundles) ==")

    # 1. Register a couple manifests, then export a pack with a digest. ─────────────────
    src = _fresh()
    M.register(src, M.capability_manifest("greet", description="say hi",
                                          archetype="COMPUTE", effect_class="READ",
                                          tags=["demo"]))
    M.register(src, M.capability_manifest("wire.money", archetype="EFFECT",
                                          effect_class="FINANCIAL"))
    pack = P.export_pack(src, name="starter")
    assert pack["pack"] == "starter" and pack["version"] == 1, pack
    assert isinstance(pack["digest"], str) and pack["digest"], "pack must carry a digest"
    names = {m["name"] for m in pack["manifests"]}
    assert names == {"greet", "wire.money"}, names
    # Export is deterministic — same registry → identical pack + digest.
    assert P.export_pack(src, name="starter") == pack, "export must be deterministic"
    line("  export_pack folds the registry into a portable pack with a content digest ✓")

    # 2. to_json / from_json round-trips BYTE-STABLY. ──────────────────────────────────
    text = P.to_json(pack)
    back = P.from_json(text)
    assert back == pack, "from_json(to_json(x)) must round-trip"
    assert P.to_json(back) == text, "serialization must be byte-stable"
    for bad in ("{not json", "[]", "123", '{"pack":"x"}'):
        try:
            P.from_json(bad); raise AssertionError("malformed pack must be rejected")
        except ValueError:
            pass
    line("  to_json/from_json round-trips byte-stably; malformed packs rejected ✓")

    # 3. verify_pack: True intact; TAMPER → False AND import registers NOTHING. ─────────
    assert P.verify_pack(pack) is True, "intact pack must verify"
    tampered = P.from_json(P.to_json(pack))
    tampered["manifests"][0]["effect_class"] = "WRITE"   # change a field, keep the digest
    assert P.verify_pack(tampered) is False, "a tampered manifest must fail integrity"
    victim = _fresh()
    res = P.import_pack(victim, tampered)
    assert res["count"] == 0 and res["registered"] == [], res
    assert M.registry(victim) == [], "fail-closed: a bad pack registers NOTHING"
    line("  verify_pack True intact; tampered field → False AND import fails closed "
         "(nothing registered) ✓")

    # 4. Import a clean pack into a FRESH kernel → manifests appear in its registry. ────
    dst = _fresh()
    res = P.import_pack(dst, pack)          # untrusted by default
    assert res["count"] == 2, res
    got = {c.content["name"] for c in M.registry(dst)}
    assert got == {"greet", "wire.money"}, got
    line("  import_pack into a fresh kernel: the manifests appear in its registry ✓")

    # 5. Untrusted EFFECT is TIGHTENED; trust_source=True preserves caveats. ───────────
    eff = M.get(dst, "wire.money").content
    assert eff["caveats"].get("requires_approval") is True, \
        "an untrusted EFFECT manifest must be tightened to requires_approval"
    comp = M.get(dst, "greet").content
    assert "requires_approval" not in comp["caveats"], "a COMPUTE manifest is not gated"
    trusting = _fresh()
    P.import_pack(trusting, pack, trust_source=True)
    teff = M.get(trusting, "wire.money").content
    assert teff["caveats"].get("requires_approval") is None, \
        "trust_source=True must preserve the manifest's own caveats (no forced gate)"
    line("  untrusted EFFECT import tightened (requires_approval); trust_source "
         "preserves caveats (import can only TIGHTEN) ✓")

    # 6. register ≠ install — importing grants NO capability. ───────────────────────────
    cap_names = {c.content.get("name") for c in dst.weave().of_type("capability")}
    assert cap_names.isdisjoint({"greet", "wire.money"}), \
        "import must NOT create a capability for imported manifests (register ≠ install)"
    line("  importing a manifest creates NO capability — register ≠ install; a handler "
         "must still be wired and authorize still gates every invoke ✓")

    line("  → capabilities are SHAREABLE: export to a content-committed pack, import "
         "fail-closed with untrusted-tighten-only — distribution without loosening a law.")
