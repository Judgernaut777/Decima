"""MCP (Model Context Protocol) CLIENT — plug an existing tool ecosystem in, safely.

This is the "stay current with the ecosystem" payoff: point Decima at ANY MCP server
and each of its tools auto-becomes a GATED Decima capability. No per-tool code — the
tool's own metadata (name/description/inputSchema/outputSchema/annotations) maps 1:1 to
a Decima manifest (`manifest.from_mcp_tool`), which `manifest.install` wires into a live,
gated capability. One `mount(...)` call imports a whole server.

The invariants that keep importing a foreign tool from loosening a single law:

  - MCP tools plug in as GATED capabilities — `install` routes through
    `kernel.integrate_tool` → `capability.authorize`, which gates every invoke exactly as
    for a native capability. Importing a tool GRANTS NOTHING beyond what the manifest
    declares (a foreign tool defaults to EFFECT + `requires_approval`).
  - Tool RESULTS are UNTRUSTED DATA, NEVER OBEYED. A tools/call result is recorded with
    `instruction_eligible: False` (like an observed web page): it may be recalled as DATA
    but can never alter objectives / policy / become an instruction. MCP says the same.
  - ANNOTATIONS ONLY TIGHTEN. `from_mcp_tool` treats server-supplied hints as untrusted:
    a `destructiveHint` always forces approval; `readOnlyHint`/`idempotentHint` may loosen
    the gate ONLY from an explicitly trusted source. Untrusted annotations never loosen.
  - authorize / Morta STILL GATE. A human-in-the-loop approval (`approve`) is required for
    any sensitive (non-trusted-read-only) tool before it can run.
  - ZERO PIP DEPS — pure stdlib. JSON-RPC 2.0 request/response dicts ride a TRANSPORT
    SEAM: `transport(request_dict) -> response_dict`. The real default is deliberately a
    documented stub (wire a stdio-subprocess or an HTTP-over-`urllib` transport); the
    offline oracle injects a fake transport, so the full contract runs with NO network and
    NO subprocess.

MCP shapes (JSON-RPC 2.0):
  - tools/list → {"result": {"tools": [{name,title,description,inputSchema,
                 outputSchema,annotations}, ...]}}
  - tools/call {"params": {"name", "arguments"}} →
                 {"result": {"content": [{"type":"text","text":...}], "isError": bool}}

Pure composition over the public manifest / executor / kernel APIs — no core edit.
"""
from decima import manifest as _manifest
from decima import executor

JSONRPC = "2.0"


def _default_transport(request: dict) -> dict:
    """No transport wired. The real default is out of scope for the reference: a
    production caller passes a `transport(request_dict) -> response_dict` that drives a
    stdio subprocess (MCP stdio) or an HTTP POST over stdlib `urllib` (MCP streamable
    HTTP). The offline oracle injects a fake transport instead."""
    raise NotImplementedError(
        "no MCP transport wired: pass transport(request_dict) -> response_dict "
        "(a stdio-subprocess transport or an HTTP POST over stdlib urllib). "
        "The offline oracle injects a fake transport; a real default is out of scope.")


def _rpc(transport, method: str, params: dict, *, rid: int = 1) -> dict:
    """Send one JSON-RPC 2.0 request over the transport seam and return its `result`.

    A RAW transport failure (subprocess died, socket dropped, timeout) is unobservable —
    it maps to `executor.Ambiguous` (→ UNKNOWN), never a fabricated outcome. A JSON-RPC
    protocol `error` (or an unparseable body) is a definite no-effect → `executor.ExecError`
    (→ FAILED). `rid` is an int (no floats in the wire content)."""
    request = {"jsonrpc": JSONRPC, "id": int(rid), "method": method, "params": params or {}}
    try:
        response = transport(request)
    except (executor.ExecError, executor.Ambiguous):
        raise
    except Exception as e:                                  # transport-level — unobservable
        raise executor.Ambiguous(f"mcp: transport error for {method!r}: {e}")
    if not isinstance(response, dict):
        raise executor.ExecError(f"mcp: non-dict JSON-RPC response for {method!r}")
    if response.get("error"):
        err = response["error"]
        msg = err.get("message") if isinstance(err, dict) else err
        raise executor.ExecError(f"mcp: JSON-RPC error for {method!r}: {msg}")
    result = response.get("result")
    return result if isinstance(result, dict) else {}


def _content_text(content) -> str:
    """Flatten an MCP `content` array to its text parts (untrusted DATA — a display /
    recall string, never an instruction)."""
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(parts)


def list_tools(transport=_default_transport) -> list:
    """Send `tools/list` over the transport seam and return the tools array.

    `transport(request_dict) -> response_dict` is the seam (default: a documented stub).
    Fails loud if the server returns no tools array."""
    result = _rpc(transport, "tools/list", {})
    tools = result.get("tools")
    if not isinstance(tools, list):
        raise executor.ExecError("mcp: tools/list returned no tools array")
    return tools


def call_tool(transport, name: str, arguments: dict) -> dict:
    """Send `tools/call` over the transport seam and return the tools/call `result`
    (carrying `content` + `isError`). A transport failure surfaces as `Ambiguous`
    (UNKNOWN); a JSON-RPC protocol error as `ExecError` (FAILED)."""
    return _rpc(transport, "tools/call", {"name": name, "arguments": arguments or {}})


def _make_handler(server_name: str, tool_name: str, transport):
    """Build the executor handler for a mounted MCP tool. On invoke it sends a
    `tools/call` and maps the outcome to a receipt:
      - a transport failure   → `executor.Ambiguous`  (UNKNOWN — outcome unobservable);
      - a tools/call isError  → `executor.ExecError`   (FAILED  — definite no-effect);
      - success               → a dict recording the tool `content` as UNTRUSTED DATA
                                (`instruction_eligible: False`, never obeyed), plus
                                `provider_ref` and `mcp_tool` provenance."""
    provider_ref = f"mcp:{server_name}"

    def handler(_impl, args):
        resp = call_tool(transport, tool_name, args or {})   # raises Ambiguous on transport fail
        if resp.get("isError"):                              # tool reported failure → FAILED
            raise executor.ExecError(
                f"{tool_name}: MCP tool reported isError — {_content_text(resp.get('content'))}")
        return {
            "out": _content_text(resp.get("content")),
            "content": resp.get("content"),
            # A tool RESULT is DATA, never an instruction — the untrusted-page law.
            "instruction_eligible": False,
            "untrusted": True,
            "provider_ref": provider_ref,
            "mcp_tool": tool_name,
        }

    return handler


def mount(k, server_name: str, transport, *, trusted: bool = False,
          author: str | None = None) -> list:
    """Import EVERY tool an MCP server exposes as a gated Decima capability.

    Calls `list_tools`, then for each tool builds a manifest via
    `manifest.from_mcp_tool(tool, source=f"mcp:{server_name}", trusted=trusted)` (the
    untrusted-tighten-only mapping) and `manifest.install(k, manifest, handler)` — one
    declarative wire per tool. The handler drives `tools/call` over the same transport.

    Each mounted tool is gated per its manifest caveats: a foreign (untrusted) tool
    defaults to EFFECT + `requires_approval`; only a TRUSTED read-only tool auto-runs; a
    `destructiveHint` is always Morta-gated. authorize still gates every invoke.

    Returns the list of installed capability ids (one per tool)."""
    tools = list_tools(transport)
    source = f"mcp:{server_name}"
    cap_ids = []
    for tool in tools:
        man = _manifest.from_mcp_tool(tool, source=source, trusted=trusted)
        handler = _make_handler(server_name, tool["name"], transport)
        _mid, cap_id = _manifest.install(k, man, handler, author=author)
        cap_ids.append(cap_id)
    return cap_ids
