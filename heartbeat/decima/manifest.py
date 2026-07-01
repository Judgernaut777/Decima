"""Capability manifests + registry — the modular plug-in substrate.

Decima proved the breadth of capability *categories* by hand-wrapping ~25 real
engines. This makes integration MODULAR: a capability is described by a declarative
**manifest** (a Cell — homoiconic, Law 3), so plugging a tool in is DATA, not code —
validatable, versioned, discoverable, and shareable. Inspired by the ONEX contract
model (declarative YAML declares inputs/outputs/capabilities/lifecycle) and made
MCP-compatible (a manifest maps 1:1 to an MCP Tool: name/description/inputSchema/
outputSchema/annotations), so an MCP server's tools import straight in.

A manifest names its **archetype** — the ONEX four-node taxonomy Decima already
embodies:
  - EFFECT       — acts on the outside world (invoke/executor); gated + Morta.
  - COMPUTE      — a pure transform (no outward effect).
  - REDUCER      — folds state (a Weave reducer).
  - ORCHESTRATOR — coordinates other capabilities (dispatch/delegation).

Laws upheld: a manifest GRANTS NOTHING — `install` still routes through
`kernel.integrate_tool` → `capability.authorize` gates every invoke. Imported
annotations are UNTRUSTED (MCP says so too): they may only ever TIGHTEN the gate,
never loosen it — a foreign tool is EFFECT + `requires_approval` by default, and only
a TRUSTED read-only tool is auto-allowed. Ints not floats in signed content.

Public `model`/`kernel`/`hashing` API only — no core edit.
"""
from decima.model import assert_content
from decima.hashing import content_id, nfc

MANIFEST = "manifest"
ARCHETYPES = ("EFFECT", "COMPUTE", "REDUCER", "ORCHESTRATOR")


def manifest_id(name: str, version: int) -> str:
    return content_id({"manifest": nfc(name), "version": int(version)}, kind="cell")


def capability_manifest(name: str, *, description: str = "", archetype: str = "EFFECT",
                        effect_class: str = "READ", input_schema: dict | None = None,
                        output_schema: dict | None = None, caveats: dict | None = None,
                        annotations: dict | None = None, source: str = "builtin",
                        version: int = 1, title: str | None = None,
                        tags=None) -> dict:
    """Build + validate a capability manifest (a plain dict → a Cell). Fail loud on a
    bad archetype, non-string effect_class, non-int version, or non-dict schema. The
    `effect_class` is folded into `caveats` (where the kernel reads it), so the manifest
    is the single source of truth for how the capability is gated."""
    name = nfc(name)
    if archetype not in ARCHETYPES:
        raise ValueError(f"archetype must be one of {ARCHETYPES}, got {archetype!r}")
    if not isinstance(effect_class, str) or not effect_class:
        raise ValueError("effect_class must be a non-empty string")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError("version must be an int (no floats/bools in signed content)")
    if input_schema is not None and not isinstance(input_schema, dict):
        raise ValueError("input_schema must be a JSON-Schema dict")
    cav = dict(caveats or {})
    cav.setdefault("effect_class", effect_class)          # the kernel reads it here
    return {
        "name": name,
        "title": nfc(title or name),
        "description": nfc(description),
        "archetype": archetype,
        "effect_class": effect_class,
        "input_schema": input_schema or {"type": "object"},
        "output_schema": output_schema,
        "caveats": cav,
        "annotations": dict(annotations or {}),           # kept as untrusted provenance
        "source": nfc(source),
        "version": int(version),
        "tags": [nfc(str(t)) for t in (tags or [])],
    }


def register(k, manifest: dict, *, author: str | None = None) -> str:
    """Record a manifest as a Cell on the Weft (validates by rebuilding it). Returns
    the manifest cell id. Registration confers NO authority — it is a description."""
    m = capability_manifest(
        manifest["name"], description=manifest.get("description", ""),
        archetype=manifest.get("archetype", "EFFECT"),
        effect_class=manifest.get("effect_class", "READ"),
        input_schema=manifest.get("input_schema"), output_schema=manifest.get("output_schema"),
        caveats=manifest.get("caveats"), annotations=manifest.get("annotations"),
        source=manifest.get("source", "builtin"), version=manifest.get("version", 1),
        title=manifest.get("title"), tags=manifest.get("tags"))
    author = author or k.decima_agent_id
    cid = manifest_id(m["name"], m["version"])
    assert_content(k.weft, author, cid, MANIFEST, m)
    return cid


def install(k, manifest: dict, handler, *, author: str | None = None) -> tuple[str, str]:
    """Declarative integrate: record the manifest AND wire its handler as a capability
    with the caveats the manifest declares (`kernel.integrate_tool`). One call turns a
    described tool into a live, gated capability. Returns (manifest_cell_id, cap_id).
    `authorize` still gates every invoke — the manifest only shapes the caveats."""
    mid = register(k, manifest, author=author)
    m = k.weave().get(mid).content
    cap_id = k.integrate_tool(m["name"], handler, caveats=dict(m["caveats"]))
    return mid, cap_id


def registry(k) -> list:
    """The live manifest catalog — the latest (highest version) non-retracted manifest
    per name, folded from the Weft. The searchable surface a discovery layer walks."""
    best: dict[str, object] = {}
    for c in k.weave().of_type(MANIFEST):
        if c.retracted:
            continue
        name = c.content["name"]
        cur = best.get(name)
        if cur is None or c.content["version"] >= cur.content["version"]:
            best[name] = c
    return list(best.values())


def find(k, *, query: str | None = None, archetype: str | None = None,
         effect_class: str | None = None, source: str | None = None) -> list:
    """Search the registry — substring `query` over name/title/description/tags, plus
    optional exact filters. The substrate for tool-DISCOVERY: given a goal, find an
    existing capability before forging a new one. Deterministic (registry order)."""
    q = nfc(query).lower() if query else None
    out = []
    for c in registry(k):
        m = c.content
        if archetype and m["archetype"] != archetype:
            continue
        if effect_class and m["effect_class"] != effect_class:
            continue
        if source and m["source"] != source:
            continue
        if q:
            hay = " ".join([m["name"], m["title"], m["description"], " ".join(m["tags"])]).lower()
            if q not in hay:
                continue
        out.append(c)
    return out


def get(k, name: str):
    """The live manifest cell for `name`, or None."""
    for c in registry(k):
        if c.content["name"] == nfc(name):
            return c
    return None


# ── MCP interop: import an MCP Tool as a manifest (untrusted-tighten-only) ────────
def from_mcp_tool(tool: dict, *, source: str, trusted: bool = False) -> dict:
    """Map an MCP Tool (name/title/description/inputSchema/outputSchema/annotations)
    to a Decima manifest. SAFETY (MCP itself says annotations are untrusted unless the
    server is trusted): a foreign tool defaults to EFFECT + `requires_approval` + a
    network-scoped sandbox; imported annotations may only make it STRICTER. Only a
    TRUSTED, read-only, non-destructive tool is auto-allowed (READ, no approval). A
    `destructiveHint` ALWAYS forces approval. `idempotentHint` is honored only from a
    trusted source. The tool BYTES are never obeyed — this maps metadata to a gate."""
    ann = tool.get("annotations") or {}
    effect_class = "EFFECT"
    requires_approval = True                              # Morta by default for a foreign tool
    if ann.get("destructiveHint"):
        effect_class = "WRITE"                            # tighten (any source)
        requires_approval = True
    elif trusted and ann.get("readOnlyHint"):
        effect_class = "READ"                             # loosen ONLY if trusted + read-only
        requires_approval = False
    caveats = {
        "effect_class": effect_class,
        "requires_approval": requires_approval,
        "sandbox": {"network": True},                    # egress pinned to the MCP server
        "idempotent": bool(trusted and ann.get("idempotentHint")),
    }
    return capability_manifest(
        tool["name"], title=tool.get("title"),
        description=tool.get("description", ""),
        archetype="EFFECT", effect_class=effect_class,
        input_schema=tool.get("inputSchema"), output_schema=tool.get("outputSchema"),
        caveats=caveats, annotations=ann, source=source, version=1,
        tags=["mcp"])
