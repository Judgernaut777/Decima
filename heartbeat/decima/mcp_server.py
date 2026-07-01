"""MCP (Model Context Protocol) SERVER — expose Decima's OWN tools, still gated.

The INVERSE of the MCP client (`decima/mcp.py`). Where the client imports a foreign
server's tools as gated Decima capabilities, this module exposes Decima's OWN installed
capabilities AS an MCP server, so any other agent / harness can drive them over the
standard MCP JSON-RPC wire. A capability's declarative manifest maps 1:1 back to an MCP
Tool (`to_mcp_tool` mirrors the shape of `manifest.from_mcp_tool` in reverse).

THE LAW THIS PRESERVES: exposing tools over MCP does NOT bypass the gate. Every
`tools/call` routes through `kernel.invoke(agent_cell, cap_id, arguments)`, so authorize
(ocap) AND Morta (requires_approval) run exactly as for any native invoke. A
Morta-gated tool returns an MCP `isError` result whose text says the operation "requires
approval" — the human-in-the-loop is surfaced to the CONSUMER, who must obtain approval
on Decima's side (`k.approve(cap)`) before the tool will run. The consumer never gets to
skip the gate; it only learns the gate said no.

ANNOTATIONS DESCRIBE THE TRUE GATE — no lying. `readOnlyHint` is set iff the capability's
`effect_class` is READ; `destructiveHint` is set iff the capability requires approval or
carries an effectful (WRITE/FINANCIAL/…) effect_class; `idempotentHint` mirrors the
manifest's `caveats.idempotent`. The MCP annotations are therefore an HONEST projection of
how the kernel will actually gate the call, never a looser advertisement.

`handle(k, agent_cell, request)` is a transport-AGNOSTIC JSON-RPC 2.0 request handler: it
takes a request dict and returns a response dict, so a caller may drive it over stdio, an
HTTP POST, or an in-process seam — the transport is the caller's concern.

MCP shapes (JSON-RPC 2.0):
  - initialize → {"result": {"protocolVersion", "serverInfo", "capabilities": {"tools": {}}}}
  - tools/list → {"result": {"tools": [{name,title,description,inputSchema,
                 outputSchema,annotations}, ...]}}
  - tools/call {"params": {"name", "arguments"}} →
                 {"result": {"content": [{"type":"text","text":...}], "isError": bool}}

Pure composition over the public manifest / kernel APIs — no core edit, zero pip deps.
"""
from decima import manifest as _manifest

JSONRPC = "2.0"
PROTOCOL_VERSION = "2025-06-18"                       # MCP revision this server speaks

# JSON-RPC 2.0 standard error codes we surface.
INVALID_PARAMS = -32602                               # unknown tool name
METHOD_NOT_FOUND = -32601                             # unknown JSON-RPC method

# effect_class values that MUST advertise destructiveHint (they touch the world). This is
# the honest side of the gate: anything not a pure READ is potentially destructive.
_DESTRUCTIVE_EFFECT_CLASSES = frozenset({
    "WRITE", "FINANCIAL", "DELETE", "ADMIN", "EFFECT", "NETWORK", "EGRESS", "PAYMENT",
})


def _caveats(manifest_content: dict) -> dict:
    cav = manifest_content.get("caveats")
    return cav if isinstance(cav, dict) else {}


def _effect_class(manifest_content: dict) -> str:
    cav = _caveats(manifest_content)
    return cav.get("effect_class") or manifest_content.get("effect_class") or "READ"


def to_mcp_tool(manifest_content: dict) -> dict:
    """INVERSE of `manifest.from_mcp_tool`: map a Decima manifest → an MCP Tool dict.

    The `annotations` REFLECT THE REAL GATE HONESTLY (never a looser advertisement):
      - readOnlyHint    = (effect_class == "READ");
      - destructiveHint = requires_approval OR effect_class is effectful (WRITE/FINANCIAL/…);
      - idempotentHint  = caveats.get("idempotent", False).
    """
    cav = _caveats(manifest_content)
    effect_class = _effect_class(manifest_content)
    requires_approval = bool(cav.get("requires_approval", False))

    read_only = effect_class == "READ"
    destructive = bool(requires_approval or effect_class in _DESTRUCTIVE_EFFECT_CLASSES)
    idempotent = bool(cav.get("idempotent", False))

    tool = {
        "name": manifest_content["name"],
        "title": manifest_content.get("title") or manifest_content["name"],
        "description": manifest_content.get("description", ""),
        "inputSchema": manifest_content.get("input_schema") or {"type": "object"},
        "annotations": {
            "readOnlyHint": read_only,
            "destructiveHint": destructive,
            "idempotentHint": idempotent,
        },
    }
    output_schema = manifest_content.get("output_schema")
    if output_schema is not None:
        tool["outputSchema"] = output_schema
    return tool


def list_tools(k, *, installed_only: bool = True) -> dict:
    """Build an MCP `tools/list` result from `manifest.registry(k)`.

    By default only manifests whose capability is actually INSTALLED (a live gated
    capability exists by that name) are exposed — you cannot call what is not wired. Pass
    `installed_only=False` to advertise every registered manifest (discovery surface)."""
    installed = {c.content.get("name") for c in k.weave().of_type("capability")}
    tools = []
    for c in _manifest.registry(k):
        m = c.content
        if installed_only and m["name"] not in installed:
            continue
        tools.append(to_mcp_tool(m))
    return {"tools": tools}


def _resolve_cap(k, name: str):
    """Resolve an installed capability CELL by tool name (latest wins), or None."""
    match = None
    for c in k.weave().of_type("capability"):
        if c.content.get("name") == name:
            match = c
    return match


def _text_content(text: str) -> list:
    return [{"type": "text", "text": str(text)}]


def _ok_text(invoke_result: dict) -> str:
    """Flatten a kernel SUCCESS result to display text (the tool output)."""
    ok = invoke_result.get("ok") or {}
    out = ok.get("out")
    if out is not None:
        return str(out)
    return str(ok)


def _deny_text(invoke_result: dict) -> str:
    """The human-readable gate reason (denial / requires-approval / proposal)."""
    if "denied" in invoke_result:
        return str(invoke_result["denied"])
    if "proposed" in invoke_result:
        return f"proposal recorded (autonomy): {invoke_result.get('autonomy')}"
    return f"tool call did not succeed: {invoke_result}"


def _call_result(invoke_result: dict) -> dict:
    """Map a `kernel.invoke` result → an MCP tools/call result. A gate denial (incl.
    requires-approval) → isError:true with the reason (human-in-the-loop surfaced to the
    caller); a success → isError:false with the output text."""
    if "ok" in invoke_result:
        return {"content": _text_content(_ok_text(invoke_result)), "isError": False}
    return {"content": _text_content(_deny_text(invoke_result)), "isError": True}


def _response(rid, result: dict) -> dict:
    return {"jsonrpc": JSONRPC, "id": rid, "result": result}


def _error(rid, code: int, message: str) -> dict:
    return {"jsonrpc": JSONRPC, "id": rid, "error": {"code": int(code), "message": message}}


def handle(k, agent_cell, request: dict) -> dict:
    """A JSON-RPC 2.0 request dispatcher exposing Decima's tools over MCP.

    Transport-agnostic: takes a request dict, returns a response dict. Supported methods:
      - `initialize`  → serverInfo + capabilities.tools (announces this is an MCP server);
      - `tools/list`  → the installed tools (via `list_tools`);
      - `tools/call` {name, arguments} → resolve the capability by name and route through
        `k.invoke(agent_cell, cap_id, arguments)`, then map the kernel result to an MCP
        tools/call result. An UNKNOWN tool → JSON-RPC error -32602.
    An UNKNOWN method → JSON-RPC method-not-found (-32601).

    THE GATE IS NOT BYPASSED: `tools/call` runs the FULL authorize + Morta gate via
    `k.invoke`. A Morta-gated (requires_approval) tool returns isError with a reason
    saying it "requires approval" — the consumer must obtain approval on Decima's side
    (`k.approve(cap)`) before the same call will succeed. The annotations returned by
    `tools/list` describe this true gate, never a looser one."""
    rid = request.get("id")
    method = request.get("method")

    if method == "initialize":
        return _response(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": {"name": "decima", "version": "1"},
            "capabilities": {"tools": {"listChanged": False}},
        })

    if method == "tools/list":
        return _response(rid, list_tools(k))

    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        cap = _resolve_cap(k, name)
        if cap is None:
            return _error(rid, INVALID_PARAMS, f"unknown tool: {name!r}")
        # THE GATE: every exposed call routes through the kernel's authorize + Morta.
        invoke_result = k.invoke(agent_cell, cap.id, arguments)
        return _response(rid, _call_result(invoke_result))

    return _error(rid, METHOD_NOT_FOUND, f"method not found: {method!r}")
