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
    SEAM: `transport(request_dict) -> response_dict`. REAL transports ship here and are
    CONFIG-GATED — only used when a caller supplies a real command/url:
      - `stdio_transport(command)`  — spawns the MCP server subprocess (stdlib
        `subprocess.Popen`, text pipes) and speaks newline-delimited JSON-RPC over
        stdin/stdout (the common MCP stdio framing); `.close()` reaps the process.
      - `http_transport(url, headers=None, wire_transport=…)` — POSTs JSON-RPC over a
        WIRE-GATED transport (`live_wire.gated_transport`; HTTPS-only, except
        localhost/127.0.0.1 for dev); without one the request fails closed.
      - `initialize(transport)` — the MCP `initialize` handshake (+ the
        `notifications/initialized` notice); `mount(..., init=True)` runs it first.
    The module-level `_default_transport` stays a documented stub (a caller must choose
    stdio vs http vs fake). The offline oracle injects a FAKE transport, so the full
    contract still runs with NO network and NO subprocess; a real command/url is what
    turns the real transports on.

MCP shapes (JSON-RPC 2.0):
  - tools/list → {"result": {"tools": [{name,title,description,inputSchema,
                 outputSchema,annotations}, ...]}}
  - tools/call {"params": {"name", "arguments"}} →
                 {"result": {"content": [{"type":"text","text":...}], "isError": bool}}

Pure composition over the public manifest / executor / kernel APIs — no core edit.
"""
import json

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


# ── REAL transports (config-gated: only used when a caller supplies a command/url) ──────
#
# A transport is a callable `(request_dict) -> response_dict` (JSON-RPC 2.0). A request
# with no `"id"` is a JSON-RPC NOTIFICATION: it is written but NO response is awaited
# (returns {}). A raw transport failure (dead subprocess, dropped socket, timeout) surfaces
# as `executor.Ambiguous` (→ UNKNOWN, outcome unobservable); an unparseable body as
# `executor.ExecError` (→ FAILED, a definite protocol no-effect). `_rpc` passes these
# through unchanged, so mounted tools inherit the same honest status mapping.


def stdio_transport(command: list[str], *, close_timeout: int = 5):
    """Spawn an MCP server SUBPROCESS and return a transport over its stdin/stdout.

    Uses stdlib `subprocess.Popen` with text pipes and writes one JSON-RPC request per
    line, reading one JSON response line back (newline-delimited JSON-RPC — the common MCP
    stdio framing). A broken pipe / dead process maps to `executor.Ambiguous` (UNKNOWN).

    The returned callable carries `.close()` (and `.proc`) so the process is reaped; it
    also works as a context manager (`with stdio_transport([...]) as t:`)."""
    import subprocess

    proc = subprocess.Popen(
        list(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,                                  # line-buffered text pipes
    )

    def transport(request: dict) -> dict:
        if proc.poll() is not None:                 # process already dead → unobservable
            raise executor.Ambiguous(
                f"mcp stdio: subprocess is not running (exit {proc.returncode})")
        line = json.dumps(request) + "\n"
        try:
            proc.stdin.write(line)
            proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as e:
            raise executor.Ambiguous(f"mcp stdio: broken pipe to subprocess: {e}")
        if "id" not in request:                     # JSON-RPC notification — no reply
            return {}
        out = proc.stdout.readline()
        if out == "":                               # EOF: server closed stdout / died
            raise executor.Ambiguous("mcp stdio: subprocess closed stdout (no response)")
        try:
            return json.loads(out)
        except (json.JSONDecodeError, ValueError) as e:
            raise executor.ExecError(f"mcp stdio: non-JSON response line: {e}")

    def close():
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream and not stream.closed:
                    stream.close()
            except Exception:
                pass
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=close_timeout)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    transport.close = close
    transport.proc = proc
    transport.__enter__ = lambda: transport
    transport.__exit__ = lambda *exc: (close(), False)[1]
    return transport


def http_transport(url: str, headers: dict | None = None, *, timeout: int = 30,
                   wire_transport=None):
    """Return a transport that POSTs the JSON-RPC request as JSON over the WIRE GATE.

    HTTPS-ONLY GUARD: a non-`https` URL is REFUSED at construction time (before any
    request) unless its host is `localhost`/`127.0.0.1`/`::1` (dev).

    PHASE 2 (GO LIVE): the bare-urlopen socket is GONE — the armed wire guard
    (decima/wire.py) refused it anyway. `wire_transport` is the engine-shaped gated
    transport (`transport(url, headers, body) -> (status, parsed_json)`) built via
    `live_wire.gated_transport(k, agent_cell, cap_id)` — a granted, Morta-approved
    egress capability. With none injected, the FIRST request fails CLOSED
    (`live_wire.NoGatedTransport`, the sanctioned path named) before any socket. An
    egress denial (`wire.EgressDenied`) surfaces loud; any other transport failure
    maps to `executor.Ambiguous` (UNKNOWN); a non-JSON-object body to
    `executor.ExecError` (FAILED). `timeout` rides the gated transport itself (see
    `live_wire`). Never invoked by the offline oracle (fakes are injected at the
    transport seam)."""
    import urllib.parse

    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    is_local = host in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme != "https" and not is_local:
        raise executor.ExecError(
            f"mcp http: refusing non-HTTPS URL {url!r} — HTTPS is required except for "
            "localhost/127.0.0.1 in dev")

    base_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        base_headers.update(headers)

    def transport(request: dict) -> dict:
        if wire_transport is None:                  # fail closed BEFORE any socket
            from decima import live_wire
            raise live_wire.NoGatedTransport("mcp.http_transport")
        from decima import wire
        body = json.dumps(request).encode("utf-8")
        try:
            status, payload = wire_transport(url, dict(base_headers), body)
        except (executor.ExecError, executor.Ambiguous, wire.EgressDenied):
            raise                                   # a policy refusal is LOUD, not UNKNOWN
        except Exception as e:                      # network/socket/timeout — unobservable
            raise executor.Ambiguous(f"mcp http: request to {url!r} failed: {e}")
        if not isinstance(payload, dict):
            raise executor.ExecError(
                f"mcp http: non-JSON-object response (status {status}): {payload!r}")
        return payload

    return transport


def initialize(transport, *, client_name: str = "decima",
               protocol_version: str = "2025-06-18") -> dict:
    """Run the MCP `initialize` handshake over the transport seam.

    Sends `initialize` (params: protocolVersion, capabilities, clientInfo), returns the
    server's `result`, then fires the `notifications/initialized` notification (best-effort
    — a notification has no id and expects no reply). `mount(..., init=True)` calls this
    first; a fake transport that doesn't implement initialize just makes mount skip it."""
    params = {
        "protocolVersion": protocol_version,
        "capabilities": {},
        "clientInfo": {"name": client_name, "version": "1"},
    }
    result = _rpc(transport, "initialize", params)
    notice = {"jsonrpc": JSONRPC, "method": "notifications/initialized", "params": {}}
    try:
        transport(notice)                           # notification — no id, no response
    except Exception:
        pass                                        # best-effort: never fail the handshake
    return result


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
          author: str | None = None, init: bool = True) -> list:
    """Import EVERY tool an MCP server exposes as a gated Decima capability.

    Calls `list_tools`, then for each tool builds a manifest via
    `manifest.from_mcp_tool(tool, source=f"mcp:{server_name}", trusted=trusted)` (the
    untrusted-tighten-only mapping) and `manifest.install(k, manifest, handler)` — one
    declarative wire per tool. The handler drives `tools/call` over the same transport.

    Each mounted tool is gated per its manifest caveats: a foreign (untrusted) tool
    defaults to EFFECT + `requires_approval`; only a TRUSTED read-only tool auto-runs; a
    `destructiveHint` is always Morta-gated. authorize still gates every invoke.

    With `init=True` (default) the MCP `initialize` handshake runs first; it is TOLERANT —
    a fake transport (or a server without initialize) just makes mount skip the handshake
    and proceed to `tools/list`.

    Returns the list of installed capability ids (one per tool)."""
    if init:
        try:
            initialize(transport)
        except Exception:
            pass                                    # tolerant: fake transport / no initialize
    tools = list_tools(transport)
    source = f"mcp:{server_name}"
    cap_ids = []
    for tool in tools:
        man = _manifest.from_mcp_tool(tool, source=source, trusted=trusted)
        handler = _make_handler(server_name, tool["name"], transport)
        _mid, cap_id = _manifest.install(k, man, handler, author=author)
        cap_ids.append(cap_id)
    return cap_ids
