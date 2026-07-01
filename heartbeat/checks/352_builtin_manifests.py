"""Built-in engine manifests — the out-of-box catalog is uniform + DISCOVERABLE.

Decima ships ~25 hand-wrapped real engines, each with its own gated install path. This
lane registers a DESCRIPTIVE manifest per engine (`builtin_manifests.register_builtins`)
so the built-in set shows up in the manifest registry and — the load-bearing part —
`discovery.search` finds the RIGHT real engine for a natural-language goal. This check
proves:

  - `register_builtins(k)` registers one manifest per bundled engine (count == the
    catalog size, and a healthy floor of >= 20 engines), all source="builtin";
  - the manifest registry then contains every bundled engine by name;
  - discovery.search ranks the correct engine for real goals — charge a card → stripe,
    send a text → comms, verify identity → kyc, weather forecast → weather, file an
    insurance claim → insurance_claim (top-1 where the goal is unambiguous);
  - archetype / effect_class / caveats are set correctly (stripe = EFFECT + FINANCIAL +
    requires_approval; weather = COMPUTE + READ, no approval);
  - registration is idempotent (content-addressed): re-registering does not grow the
    registry.

Contract: run(k, line). Fail loud. Owns a fresh, offline Kernel.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import manifest as M
from decima import discovery as D
from decima import builtin_manifests as B


def run(k, line):
    line("\n== BUILT-IN ENGINE MANIFESTS (out-of-box catalog: uniform + discoverable) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)

    # 1. register_builtins registers one manifest per bundled engine. ─────────────────
    ids = B.register_builtins(kk)
    assert len(ids) == len(B.BUILTINS), \
        f"one manifest per builtin: got {len(ids)}, expected {len(B.BUILTINS)}"
    assert len(B.BUILTINS) >= 20, f"expected >= 20 bundled engines, got {len(B.BUILTINS)}"
    assert all(isinstance(i, str) and i for i in ids), "each manifest id must be a str cell id"
    line(f"  register_builtins → {len(ids)} manifests (one per bundled engine, >= 20) ✓")

    # 2. the registry contains every bundled engine by name, all source=builtin. ──────
    reg = M.registry(kk)
    reg_names = {c.content["name"] for c in reg}
    spec_names = {s["name"] for s in B.BUILTINS}
    missing = spec_names - reg_names
    assert not missing, f"registry missing bundled engines: {sorted(missing)}"
    builtins = M.find(kk, source="builtin")
    assert len(builtins) >= len(B.BUILTINS), "every manifest must carry source=builtin"
    line(f"  registry holds all {len(spec_names)} bundled engines (source=builtin) ✓")

    # 3. discovery.search ranks the RIGHT engine for real natural-language goals. ─────
    def top1(goal):
        ranked = D.search(kk, goal, top_k=3)
        return ranked, ranked[0]["name"]

    def assert_first(goal, expected):
        ranked, first = top1(goal)
        assert first == expected, \
            f"search({goal!r}) → {[(r['name'], r['score']) for r in ranked]}; want {expected} #1"
        assert ranked[0]["score"] > 0, f"search({goal!r}) top score must be > 0"
        return ranked

    cases = [
        ("charge a customer's credit card", "stripe_rail"),
        ("send a text message", "comms"),
        ("verify someone's identity", "kyc"),
        ("get the weather forecast", "weather_engine"),
        ("file an insurance claim", "insurance_claim"),
    ]
    for goal, expected in cases:
        r = assert_first(goal, expected)
        line(f"  search({goal!r}) → {r[0]['name']} (score={r[0]['score']}) #1 ✓")

    # A couple more goals that must land in the top-3 (less crisp phrasings). ─────────
    def assert_top3(goal, expected):
        ranked = D.search(kk, goal, top_k=3)
        names = [r["name"] for r in ranked]
        assert expected in names, f"search({goal!r}) → {names}; want {expected} in top-3"

    assert_top3("geocode a street address into coordinates", "maps_engine")
    assert_top3("translate text into another language", "translate_engine")
    line("  looser goals (geocode address, translate text) land the right engine top-3 ✓")

    # 4. archetype / effect_class / caveats are set correctly. ────────────────────────
    stripe = M.get(kk, "stripe_rail").content
    assert stripe["archetype"] == "EFFECT", stripe["archetype"]
    assert stripe["effect_class"] == "FINANCIAL", stripe["effect_class"]
    assert stripe["caveats"].get("requires_approval") is True, stripe["caveats"]
    assert stripe["caveats"]["effect_class"] == "FINANCIAL", stripe["caveats"]
    assert stripe["source"] == "builtin", stripe["source"]
    line("  stripe_rail = EFFECT + FINANCIAL + requires_approval (Morta-gated) ✓")

    weather = M.get(kk, "weather_engine").content
    assert weather["archetype"] == "COMPUTE", weather["archetype"]
    assert weather["effect_class"] == "READ", weather["effect_class"]
    assert not weather["caveats"].get("requires_approval"), weather["caveats"]
    line("  weather_engine = COMPUTE + READ (read a fact; auto-allowed, no approval) ✓")

    # 5. registration is idempotent — content-addressed, no registry growth. ──────────
    ids2 = B.register_builtins(kk)
    assert ids2 == ids, "re-registering identical manifests must return identical cell ids"
    assert len({c.content["name"] for c in M.registry(kk)}) == len(spec_names), \
        "re-registration must not grow the registry (content-addressed)"
    line("  register_builtins idempotent (content-addressed; registry stable) ✓")

    line("  → the ~25 bundled engines are now a uniform, DISCOVERABLE catalog: discovery "
         "finds a real engine for a real goal before Nona ever forges a new one.")
