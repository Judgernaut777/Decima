"""MCP REAL TRANSPORTS — a stdio subprocess speaks JSON-RPC to Decima, offline.

Check 340 proved the MCP contract over a FAKE injected transport. This check proves the
REAL `stdio_transport` END-TO-END, still with NO network: it spawns a tiny LOCAL fake MCP
server (a python script written to a tempfile) that reads newline-delimited JSON-RPC from
stdin and answers `initialize`, `tools/list`, and `tools/call` on stdout. Then:

  - `mcp.initialize(t)` runs the real MCP handshake over the subprocess pipes and returns
    the server's protocolVersion;
  - `mcp.mount(k, "local", t, trusted=True)` imports the server's tool as a gated
    capability (one manifest Cell + one capability), driving `tools/list` over the pipes;
  - invoking that capability drives a REAL `tools/call` through the subprocess and records
    the echoed result as UNTRUSTED DATA (`instruction_eligible: False`) — round-tripping
    JSON-RPC over actual OS pipes, no fake seam.

It ALSO unit-tests `http_transport`'s HTTPS-only guard: a non-https, non-localhost URL is
REFUSED at construction — before any request — so the guard needs no network to prove.

Because `install` grows Decima's envelope, a FRESH agent cell is folded before each invoke.
The subprocess is terminated in a `finally`, even on assertion failure. Contract:
run(k, line). Fail loud, offline, deterministic.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import mcp, manifest as M, executor


# A tiny LOCAL MCP server: reads newline-delimited JSON-RPC on stdin, replies on stdout.
# It answers initialize, tools/list (≥1 tool), tools/call (echoes args). No network.
_FAKE_SERVER = r'''
import sys, json

def reply(rid, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
    sys.stdout.flush()

for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    req = json.loads(raw)
    method = req.get("method")
    rid = req.get("id")
    if method == "notifications/initialized":
        continue  # notification: no id, no response
    if method == "initialize":
        reply(rid, {"protocolVersion": "2025-06-18", "capabilities": {},
                    "serverInfo": {"name": "fake-local", "version": "1"}})
    elif method == "tools/list":
        reply(rid, {"tools": [
            {"name": "echo", "title": "Echo", "description": "echo the arguments back",
             "inputSchema": {"type": "object", "properties": {"msg": {"type": "string"}}},
             "annotations": {"readOnlyHint": True, "idempotentHint": True}}]})
    elif method == "tools/call":
        args = (req.get("params") or {}).get("arguments") or {}
        text = "echo:" + json.dumps(args, sort_keys=True)
        reply(rid, {"content": [{"type": "text", "text": text}], "isError": False})
    else:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": "method not found"}}) + "\n")
        sys.stdout.flush()
'''


def _decima(kk):
    return kk.weave().get(kk.decima_agent_id)


def _cap_named(kk, name):
    return next(c for c in kk.weave().of_type("capability") if c.content.get("name") == name)


def run(k, line):
    line("\n== MCP REAL TRANSPORTS (a stdio subprocess speaks JSON-RPC to Decima, offline) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)

    workdir = tempfile.mkdtemp()
    scriptpath = os.path.join(workdir, "fake_mcp_server.py")
    with open(scriptpath, "w") as fh:
        fh.write(_FAKE_SERVER)

    # 1. HTTPS-only guard (unit, no network): a non-https non-localhost URL is refused. ──
    refused = False
    try:
        mcp.http_transport("http://evil.example.com/rpc")
    except executor.ExecError:
        refused = True
    assert refused, "http_transport must refuse a non-HTTPS, non-localhost URL"
    # And it must refuse BEFORE any request — a localhost http url is allowed to construct.
    assert mcp.http_transport("http://127.0.0.1:9/rpc") is not None, "localhost http ok"
    assert mcp.http_transport("https://api.example.com/rpc") is not None, "https ok"
    line("  http_transport HTTPS-only guard: a non-https, non-localhost URL is refused at "
         "construction (before any request) — localhost/https allowed ✓")

    t = mcp.stdio_transport(["python3", scriptpath])
    try:
        # 2. Real MCP initialize handshake over the subprocess pipes. ────────────────────
        init = mcp.initialize(t)
        assert init.get("protocolVersion") == "2025-06-18", init
        assert init.get("serverInfo", {}).get("name") == "fake-local", init
        line("  mcp.initialize drives the real MCP handshake over OS pipes → server's "
             "protocolVersion returned ✓")

        # 3. mount imports the server's tool as a gated capability (drives tools/list). ──
        cap_ids = mcp.mount(kk, "local", t, trusted=True)
        assert len(cap_ids) == 1, cap_ids
        reg = {c.content["name"] for c in M.registry(kk)}
        assert "echo" in reg, reg
        assert _cap_named(kk, "echo") is not None, "no capability wired for echo"
        echo_m = M.get(kk, "echo").content
        assert echo_m["effect_class"] == "READ" and not echo_m["caveats"]["requires_approval"]
        line("  mcp.mount over the REAL stdio transport imports the server's tool: one "
             "manifest Cell + one gated capability (trusted readOnly → READ) ✓")

        # 4. Invoke → a REAL tools/call round-trips over the subprocess; result is DATA. ─
        echo = _cap_named(kk, "echo")
        r = kk.invoke(_decima(kk), echo.id, {"msg": "loom"})   # fresh agent — envelope grew
        assert "ok" in r and r["status"] == "SUCCEEDED", r
        receipt = kk.weave().get(r["result_cell"]).content
        assert receipt["instruction_eligible"] is False and receipt["untrusted"] is True, receipt
        assert receipt["mcp_tool"] == "echo" and receipt["provider_ref"] == "mcp:local", receipt
        assert '"msg": "loom"' in receipt["out"], receipt   # the echoed args round-tripped
        line("  invoking the mounted tool drives a REAL tools/call through the subprocess; "
             "the echoed result round-trips JSON-RPC over OS pipes and is recorded as "
             "UNTRUSTED DATA (instruction_eligible: False), never obeyed ✓")
    finally:
        # Terminate the subprocess even on assertion failure.
        t.close()

    # 5. After close(), the transport is dead → an invoke maps to UNKNOWN (unobservable). ─
    assert t.proc.poll() is not None, "subprocess must be reaped after close()"
    dead = False
    try:
        mcp.list_tools(t)
    except executor.Ambiguous:
        dead = True
    assert dead, "a dead subprocess transport must surface as Ambiguous (UNKNOWN)"
    line("  after close() the subprocess is reaped; a further request maps to Ambiguous "
         "→ UNKNOWN (a dead process is unobservable, never fabricated) ✓")

    line("  → real MCP transports ship (stdio subprocess + HTTPS-guarded urllib POST) and "
         "are config-gated: a real command/url turns them on, the oracle injects a fake — "
         "pure stdlib, zero pip deps, offline and deterministic.")
