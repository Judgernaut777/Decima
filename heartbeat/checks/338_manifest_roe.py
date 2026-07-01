"""Capability MANIFEST + registry + ROE — the modular plug-in substrate.

Foundation for making Decima modular: a capability is described by a declarative
manifest (a Cell), installed declaratively into a gated capability, discoverable in a
registry, and importable from an MCP tool with an untrusted-tighten-only mapping;
Rules of Engagement are a data-defined policy for when the human decides. This check
proves:
  - a manifest builds/validates (bad archetype / non-int version / non-string
    effect_class are rejected) and records a `manifest` Cell;
  - `install` declaratively wires a capability that `authorize` gates (a READ tool
    runs; an EFFECT+requires_approval manifest is denied until Morta approval);
  - the registry finds capabilities by query/archetype/effect_class (discovery
    substrate), keeping the latest version;
  - `from_mcp_tool` maps annotations SAFELY — a foreign tool defaults to
    EFFECT+approval, a destructiveHint tightens, and readOnly/idempotent loosen ONLY
    from a trusted source (untrusted annotations never loosen the gate);
  - an ROE evaluates proceed/approve/refuse first-match-wins with a conservative
    default, and grants no authority (it is policy, not a gate bypass).

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import manifest as M
from decima import roe as R


def _decima(kk):
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== CAPABILITY MANIFEST + REGISTRY + ROE (modular plug-in substrate) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)

    # 1. Build + validate + register. ─────────────────────────────────────────────────
    m = M.capability_manifest("greet", description="say hello", archetype="COMPUTE",
                              effect_class="READ", tags=["demo", "text"])
    mid = M.register(kk, m)
    cell = kk.weave().get(mid)
    assert cell is not None and cell.type == M.MANIFEST and cell.content["name"] == "greet", cell
    for bad in (lambda: M.capability_manifest("x", archetype="NOPE"),
                lambda: M.capability_manifest("x", version=1.5),
                lambda: M.capability_manifest("x", effect_class="")):
        try:
            bad(); raise AssertionError("invalid manifest must be rejected")
        except ValueError:
            pass
    line("  manifest builds + validates (bad archetype/float-version/empty effect_class "
         "rejected) and records a Cell ✓")

    # 2. Declarative install → a gated capability. ─────────────────────────────────────
    _, cap = M.install(kk, m, lambda _impl, args: {"out": f"hello {args.get('who','world')}"})
    r = kk.invoke(_decima(kk), cap, {"who": "decima"})
    assert "ok" in r and "hello decima" in str(r["ok"].get("out")), r
    # An EFFECT+requires_approval manifest installs a Morta-gated capability.
    gated = M.capability_manifest("wire.money", archetype="EFFECT", effect_class="FINANCIAL",
                                  caveats={"requires_approval": True})
    _, gcap = M.install(kk, gated, lambda _impl, args: {"out": "moved"})
    denied = kk.invoke(_decima(kk), gcap, {"amount": 1})
    assert "denied" in denied and "approval" in denied["denied"], denied
    kk.approve(gcap)
    assert "ok" in kk.invoke(_decima(kk), gcap, {"amount": 1}), "approved effect should run"
    line("  install → declaratively wired capability; READ runs, EFFECT+approval "
         "denied until Morta approval (manifest shapes caveats; authorize still gates) ✓")

    # 3. Registry + discovery search. ──────────────────────────────────────────────────
    assert {c.content["name"] for c in M.registry(kk)} >= {"greet", "wire.money"}
    assert [c.content["name"] for c in M.find(kk, query="hello")] == ["greet"]
    assert [c.content["name"] for c in M.find(kk, effect_class="FINANCIAL")] == ["wire.money"]
    # Latest version wins in the registry.
    M.register(kk, M.capability_manifest("greet", description="v2", version=2))
    assert M.get(kk, "greet").content["version"] == 2, "registry keeps the latest version"
    line("  registry/find discovers by query + effect_class; latest version wins ✓")

    # 4. MCP import — untrusted-tighten-only. ──────────────────────────────────────────
    ro_untrusted = M.from_mcp_tool({"name": "reader", "description": "read",
                                    "annotations": {"readOnlyHint": True}}, source="mcp:x")
    assert ro_untrusted["effect_class"] == "EFFECT" and ro_untrusted["caveats"]["requires_approval"], \
        "an untrusted readOnly tool must NOT be auto-allowed"
    ro_trusted = M.from_mcp_tool({"name": "reader", "annotations": {"readOnlyHint": True,
                                  "idempotentHint": True}}, source="mcp:x", trusted=True)
    assert ro_trusted["effect_class"] == "READ" and not ro_trusted["caveats"]["requires_approval"] \
        and ro_trusted["caveats"]["idempotent"], "a trusted readOnly tool may be auto-allowed"
    destructive = M.from_mcp_tool({"name": "rm", "annotations": {"readOnlyHint": True,
                                   "destructiveHint": True}}, source="mcp:x", trusted=True)
    assert destructive["effect_class"] == "WRITE" and destructive["caveats"]["requires_approval"], \
        "a destructiveHint must always tighten to approval"
    line("  MCP import: untrusted annotations only TIGHTEN (foreign=EFFECT+approval; "
         "trusted read-only loosens; destructive always gated) ✓")

    # 5. ROE — declarative when-the-human-decides. ─────────────────────────────────────
    pol = R.roe_policy("engagement-1", [
        {"match": {"effect_class": "FINANCIAL"}, "verdict": "approve", "reason": "money moves need a human"},
        {"match": {"effect_class": "READ"}, "verdict": "proceed", "reason": "reads are safe"},
        {"match": {"capability": "exploit"}, "verdict": "refuse", "reason": "out of scope"},
    ], default="approve")
    rid = R.register(kk, pol)
    assert R.evaluate(kk, rid, {"effect_class": "READ"})["verdict"] == "proceed"
    assert R.evaluate(kk, rid, {"effect_class": "FINANCIAL"})["verdict"] == "approve"
    assert R.evaluate(kk, rid, {"capability": "exploit"})["verdict"] == "refuse"
    d = R.evaluate(kk, rid, {"effect_class": "WEATHER"})       # no rule matches
    assert d["verdict"] == "approve" and d["rule"] is None, d
    line("  ROE evaluates proceed/approve/refuse (first-match-wins) with a conservative "
         "default — policy for when the human decides, composing with Morta ✓")

    line("  → plug-in substrate: capabilities are declarative manifests (installable, "
         "discoverable, MCP-importable-safely) governed by data-defined ROE — modularity "
         "without loosening a single law.")
