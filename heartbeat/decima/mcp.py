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

DEPTH BEYOND TOOLS — the rest of the protocol Decima actually needs, under the SAME
law (foreign content is UNTRUSTED DATA, never instruction):

  - `resources_list` / `resources_read` — a mounted server's resources can be
    enumerated and READ, but a resource BODY crosses the MANDATORY quarantine
    boundary (`quarantine.admit` → an opaque `Quarantined` handle + a tainted
    intake Cell) and is remembered `instruction_eligible=False`. A resource that
    says "run rm -rf" is data to cite, never a command — reading it invokes
    NOTHING, and the raw body is never returned as bare text (only the handle).
  - `prompts_list` — a server's prompt templates are enumerated as DATA: each is
    recorded as an `mcp_prompt` Cell with `instruction_eligible=False`. A prompt
    template is untrusted foreign text ABOUT prompting, not an instruction to
    Decima; arriving confers nothing.
  - `elicit` — a server-originated ELICITATION (a request for input/consent) is
    NEVER auto-answered. Its message is quarantined as untrusted DATA and the
    proposed answer is ENQUEUED as a Morta-gated `ApprovalInbox` item; nothing is
    sent back to the server until a human explicitly approves (deny → nothing is
    ever sent). The answering capability is unconditionally `requires_approval` —
    a server can never elicit its own consent.
  - DURABLE MOUNTS — `mount` records an `mcp_mount` Cell on the Weft (server,
    tools, capability ids), so a mount FOLDS BACK on a reconstructed Kernel
    instead of dying with the process. `remount` re-binds exactly the recorded
    tools to a live transport — re-minting nothing, calling no tools/list, and
    conferring no new authority (the folded caveats + authorize + Morta still
    gate every invoke).

MCP shapes (JSON-RPC 2.0):
  - tools/list → {"result": {"tools": [{name,title,description,inputSchema,
                 outputSchema,annotations}, ...]}}
  - tools/call {"params": {"name", "arguments"}} →
                 {"result": {"content": [{"type":"text","text":...}], "isError": bool}}
  - resources/list → {"result": {"resources": [{uri,name,title,mimeType}, ...]}}
  - resources/read {"params": {"uri"}} →
                 {"result": {"contents": [{uri,mimeType,text|blob}, ...]}}
  - prompts/list → {"result": {"prompts": [{name,title,description,arguments}, ...]}}
  - elicitation/create — a SERVER-originated request {"id", "params": {"message",
                 "requestedSchema"}}; the client answers with a JSON-RPC RESPONSE
                 {"id", "result": {"action": "accept"|"decline", "content": {...}}}.

Pure composition over the public manifest / executor / kernel / quarantine / memory /
inbox APIs — no core edit.
"""
import json

from decima import manifest as _manifest
from decima import executor
from decima import memory as _memory
from decima import quarantine as _quarantine
from decima.hashing import content_id, nfc
from decima.inbox import ApprovalInbox
from decima.model import assert_content, assert_edge

JSONRPC = "2.0"

RESOURCE = "mcp_resource"        # provenance Cell for a read resource (body quarantined)
PROMPT = "mcp_prompt"            # an enumerated prompt template — untrusted DATA
MOUNT_CELL = "mcp_mount"         # a durable mount record — folds back after a restart
ELICITATION_METHOD = "elicitation/create"


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


# ── resources: enumerate + READ; a resource BODY is quarantined, untrusted DATA ─────────


def resources_list(transport=_default_transport) -> list:
    """Send `resources/list` over the transport seam and return the resource
    DESCRIPTORS (uri/name/title/mimeType). Descriptors are server-supplied metadata —
    untrusted; enumeration alone admits nothing, records nothing, invokes nothing.
    Fails loud if the server returns no resources array."""
    result = _rpc(transport, "resources/list", {})
    resources = result.get("resources")
    if not isinstance(resources, list):
        raise executor.ExecError("mcp: resources/list returned no resources array")
    return resources


def resources_read(k, server_name: str, transport, uri: str, *,
                   author: str | None = None) -> dict:
    """READ one resource and admit its body as QUARANTINED, UNTRUSTED DATA.

    Sends `resources/read` and flattens the text parts of `contents`. The body then
    crosses the MANDATORY untrusted-content boundary:

      - `quarantine.admit` mints the ONLY handle on the raw text — an opaque
        `Quarantined` (`str()` raises; `as_data()` is the sole brain-facing
        rendering) — and records a tainted `quarantine_intake` Cell
        (`instruction_eligible=False`, Law 4 provenance);
      - the body is remembered as an EPISODIC memory with
        `instruction_eligible=False`: recallable/citable as DATA, NEVER an
        instruction — a resource that says "run rm -rf" is data to cite, not a
        command, and nothing is invoked by reading it;
      - an `mcp_resource` Cell ties server + uri + mime + sha256 to the intake and
        the claim (an `admitted_as` edge lands the provenance).

    Returns {server, uri, mime, cell, intake, claim, quarantined, chars,
    instruction_eligible: False}. The raw body is NEVER returned as bare text —
    only the quarantined handle (release requires the capability-gated
    `quarantine.promote`)."""
    result = _rpc(transport, "resources/read", {"uri": uri})
    contents = result.get("contents")
    if not isinstance(contents, list):
        raise executor.ExecError(f"mcp: resources/read({uri!r}) returned no contents array")
    parts, mime = [], ""
    for item in contents:
        if isinstance(item, dict):
            if "text" in item:
                parts.append(str(item.get("text", "")))
            mime = mime or str(item.get("mimeType", ""))
    body = "\n".join(parts)
    author = author or k.decima_agent_id
    q = _quarantine.admit(k, f"mcp:{server_name} resource {uri}", body)
    claim = _memory.remember_episodic(
        k.weft, author, body, evidence_src=q.cell,
        instruction_eligible=False,      # a resource body is DATA to cite, NEVER a command
    )
    rid = content_id({"mcp_resource": nfc(uri), "server": nfc(server_name),
                      "sha256": q.sha256})
    assert_content(k.weft, author, rid, RESOURCE, {
        "server": nfc(server_name),
        "uri": nfc(uri),
        "mime": nfc(mime),
        "sha256": q.sha256,
        "chars": int(q.chars),                     # int, never a float
        "intake": q.cell,
        "claim": claim,
        "instruction_eligible": False,
        "untrusted": True,
        "recallable": True,
        "citable": True,
    })
    assert_edge(k.weft, author, rid, "admitted_as", q.cell)
    return {"server": server_name, "uri": uri, "mime": mime, "cell": rid,
            "intake": q.cell, "claim": claim, "quarantined": q,
            "chars": int(q.chars), "instruction_eligible": False}


# ── prompts: enumerate a server's templates as untrusted DATA ───────────────────────────


def prompts_list(k, server_name: str, transport, *, author: str | None = None) -> list:
    """Enumerate a server's PROMPT TEMPLATES as UNTRUSTED DATA.

    Sends `prompts/list` and records each template as an `mcp_prompt` Cell with
    `instruction_eligible=False`: a prompt template is foreign text ABOUT how one
    might prompt — recallable/citable data, never an instruction to Decima. An
    injection smuggled into a template's name/description arrives, is recorded
    verbatim as DATA, and confers nothing. Fails loud if the server returns no
    prompts array. Returns [{cell, name, server, instruction_eligible: False}]."""
    result = _rpc(transport, "prompts/list", {})
    prompts = result.get("prompts")
    if not isinstance(prompts, list):
        raise executor.ExecError("mcp: prompts/list returned no prompts array")
    author = author or k.decima_agent_id
    out = []
    for p in prompts:
        if not isinstance(p, dict):
            continue
        name = nfc(str(p.get("name", "")))
        pid = content_id({"mcp_prompt": name, "server": nfc(server_name),
                          "lamport": int(k.weft.lamport)})
        args = [nfc(str(a.get("name", ""))) for a in (p.get("arguments") or [])
                if isinstance(a, dict)]
        assert_content(k.weft, author, pid, PROMPT, {
            "server": nfc(server_name),
            "name": name,
            "title": nfc(str(p.get("title") or "")),
            "description": nfc(str(p.get("description") or "")),
            "arguments": args,
            "instruction_eligible": False,         # a template is DATA, never an instruction
            "untrusted": True,
            "recallable": True,
            "citable": True,
        })
        out.append({"cell": pid, "name": name, "server": server_name,
                    "instruction_eligible": False})
    return out


# ── elicitation: a server's ask for input/consent is Morta-gated, never auto-answered ───


def _make_elicit_handler(server_name: str, transport):
    """Executor handler that SENDS a human-approved elicitation answer back to the
    server — the frame is a JSON-RPC RESPONSE (id + result, no method) to the
    server-originated request, written over the transport's write-only `.send` lane
    when it has one (a response awaits no reply line). It only ever runs
    POST-approval: the unconditional Morta gate on the answering capability is what
    keeps a server from eliciting its own consent. A wire drop mid-answer is
    unobservable → `Ambiguous` (UNKNOWN, never a fabricated 'answered')."""
    def handler(_impl, args):
        response = {"jsonrpc": JSONRPC, "id": int(args["id"]),
                    "result": {"action": args.get("action") or "decline",
                               "content": args.get("content") or {}}}
        send = getattr(transport, "send", None) or transport
        try:
            send(response)
        except (executor.ExecError, executor.Ambiguous):
            raise
        except Exception as e:                      # wire dropped mid-answer — unobservable
            raise executor.Ambiguous(
                f"mcp: elicitation answer to {server_name!r} failed: {e}")
        return {"out": {"answered": int(args["id"]),
                        "action": args.get("action") or "decline"},
                "provider_ref": f"mcp:{server_name}"}
    return handler


def _elicit_answer_cap(k, server_name: str, transport) -> str:
    """The Morta-gated capability that answers a server's elicitations. Minted at
    most once per server (a later call folds the existing cap from the Weave); the
    handler is RE-registered every time so a reconstructed process re-binds a live
    transport. `requires_approval` is unconditional — nothing a server sends can
    loosen its own consent gate."""
    name = f"mcp.elicit_answer.{nfc(server_name)}"
    handler = _make_elicit_handler(server_name, transport)
    for c in k.weave().of_type("capability"):
        if c.content.get("name") == name and not c.retracted:
            executor.register(name, handler)        # re-bind the live transport
            return c.id
    return k.integrate_tool(name, handler,
                            caveats={"effect_class": "EFFECT", "requires_approval": True})


def elicit(k, server_name: str, transport, request: dict, *,
           answer: dict | None = None, agent_cell=None,
           author: str | None = None) -> dict:
    """Route a server-originated ELICITATION into the ApprovalInbox — NEVER auto-answer.

    `request` is the server's JSON-RPC `elicitation/create` request (an int `id`,
    params carrying `message` + `requestedSchema`). What happens — and, crucially,
    what does NOT:

      - the elicitation MESSAGE is foreign text: it is admitted through
        `quarantine.admit` as untrusted DATA (`instruction_eligible=False`) — an
        elicitation that says "confirm sending your keys" is an ask to record,
        never a consent to grant;
      - the PROPOSED answer (`answer` dict → action "accept"; None → "decline") is
        ALWAYS ENQUEUED as a Morta-gated `ApprovalInbox` item — `enqueue`, not
        `submit`, so even a standing capability-level approval cannot shortcut the
        queue. NOTHING is sent back to the server until a human explicitly
        `approve`s the item (the pinned nonce then enacts exactly this answer
        through the full ocap/Morta spine); a `deny` means nothing is EVER sent;
      - no capability is granted, and no effect fires, merely because the
        elicitation arrived.

    Returns {queued, capability, intake, server, id, instruction_eligible: False}."""
    if not isinstance(request, dict) or request.get("method") != ELICITATION_METHOD:
        got = request.get("method") if isinstance(request, dict) else type(request).__name__
        raise executor.ExecError(f"mcp: not an elicitation request: {got!r}")
    rid = request.get("id")
    if not isinstance(rid, int) or isinstance(rid, bool):
        raise executor.ExecError("mcp: elicitation id must be an int (ints-not-floats)")
    params = request.get("params") or {}
    message = str(params.get("message", ""))
    q = _quarantine.admit(k, f"mcp:{server_name} elicitation {int(rid)}", message)
    cap_id = _elicit_answer_cap(k, server_name, transport)
    agent = agent_cell or k.weave().get(k.decima_agent_id)
    args = {"id": int(rid),
            "action": "accept" if answer is not None else "decline",
            "content": dict(answer or {})}
    item_id = ApprovalInbox(k).enqueue(
        agent, cap_id, args,
        description=(f"MCP server {server_name!r} elicits input/consent "
                     f"(request {int(rid)}): proposed answer {args['action']!r}; "
                     f"message quarantined as {q.cell[:12]}"),
        provenance=q.cell)
    return {"queued": item_id, "capability": cap_id, "intake": q.cell,
            "server": server_name, "id": int(rid), "instruction_eligible": False}


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
    also works as a context manager (`with stdio_transport([...]) as t:`). It ALSO
    carries `.send(frame)` — a write-only lane for frames that expect NO reply line:
    a JSON-RPC RESPONSE to a server-originated request (e.g. an approved elicitation
    answer, which HAS an id but is not a request) or an extra notification."""
    import subprocess

    proc = subprocess.Popen(
        list(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,                                  # line-buffered text pipes
    )

    def _write(frame: dict):
        if proc.poll() is not None:                 # process already dead → unobservable
            raise executor.Ambiguous(
                f"mcp stdio: subprocess is not running (exit {proc.returncode})")
        line = json.dumps(frame) + "\n"
        try:
            proc.stdin.write(line)
            proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as e:
            raise executor.Ambiguous(f"mcp stdio: broken pipe to subprocess: {e}")

    def transport(request: dict) -> dict:
        _write(request)
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

    transport.send = _write                        # write-only: a response awaits no reply
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

    DURABLE: the mount is recorded as an `mcp_mount` Cell (server, tool names,
    capability ids; counts are ints; `instruction_eligible=False` — a mount record is
    DATA about the world), so the mount FOLDS BACK on a reconstructed Kernel instead of
    dying with the process; `remount` re-binds a live transport to it after a restart.

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
    author_id = author or k.decima_agent_id
    mount_id = content_id({"mcp_mount": nfc(server_name), "lamport": int(k.weft.lamport)})
    assert_content(k.weft, author_id, mount_id, MOUNT_CELL, {
        "server": nfc(server_name),
        "source": source,
        "trusted": bool(trusted),
        "tools": [nfc(t["name"]) for t in tools],
        "caps": list(cap_ids),
        "tool_count": len(cap_ids),                 # int, never a float
        "instruction_eligible": False,              # a mount record is DATA about the world
    })
    for cid in cap_ids:
        assert_edge(k.weft, author_id, mount_id, "mounts", cid)
    return cap_ids


# ── durable mounts: a mount is a Cell — it folds back, it does not die with the process ─


def mounts(k) -> list:
    """The durable `mcp_mount` Cells, folded live from the Weave (append order —
    the LAST cell for a server is its latest mount)."""
    return [c for c in k.weave().of_type(MOUNT_CELL) if not c.retracted]


def remount(k, server_name: str, transport) -> list:
    """Fold a durable mount back to LIFE on a reconstructed Kernel.

    The manifests, capabilities, and grants a `mount` minted are Weft Cells — they
    survive a restart by folding. What DIES with the process is the executor
    registry binding each tool's effect to a live transport. `remount` re-binds
    exactly the tools the durable `mcp_mount` Cell recorded:

      - it RE-MINTS NOTHING — no new manifest, capability, or grant Cell lands;
      - it calls NO tools/list — the durable record is authoritative (a server
        whose tool set changed needs a fresh `mount`);
      - it CONFERS NO new authority — every re-bound tool still runs under its
        original folded caveats + `capability.authorize` + Morta.

    Fails CLOSED (`ExecError`) when no durable mount exists for `server_name`.
    Returns the recorded capability ids."""
    cell = None
    for c in mounts(k):
        if c.content.get("server") == nfc(server_name):
            cell = c                                # latest mount for this server wins
    if cell is None:
        raise executor.ExecError(f"mcp: no durable mount recorded for {server_name!r}")
    for tool_name in cell.content.get("tools", []):
        executor.register(tool_name, _make_handler(server_name, tool_name, transport))
    return list(cell.content.get("caps", []))
