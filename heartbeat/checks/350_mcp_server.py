"""MCP SERVER — expose Decima's OWN tools over MCP, WITHOUT bypassing the gate.

The INVERSE of the MCP client (340_mcp): instead of importing a foreign server's tools as
gated Decima capabilities, this exposes Decima's OWN installed capabilities AS an MCP
server, driven entirely OFFLINE over transport-agnostic JSON-RPC dicts. The contract:

  - `handle` on `initialize` → serverInfo + a `tools` capability (this IS an MCP server);
  - `handle` on `tools/list` → every INSTALLED tool, with annotations that describe the
    TRUE gate honestly (a READ tool advertises readOnlyHint; a FINANCIAL/requires_approval
    tool advertises destructiveHint — no looser advertisement than the real gate);
  - `handle` on `tools/call` of the READ tool → isError:false + the output text;
  - `handle` on `tools/call` of the FINANCIAL+approval tool → isError:true + a reason
    saying "approval" — the Morta gate is enforced THROUGH the MCP wire; after a human
    `k.approve(cap)`s on Decima's side, the SAME tools/call → isError:false (the consumer
    could not skip the gate, only learn it said no, then get approval);
  - an UNKNOWN tool → JSON-RPC error -32602; an UNKNOWN method → -32601.

Because `install` grows Decima's envelope, a FRESH agent cell is folded before each call
that invokes. Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import manifest as M
from decima import mcp_server as MS


def _decima(kk):
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== MCP SERVER (expose Decima's own tools over MCP, gate NOT bypassed, offline) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)

    # Install a READ capability and a FINANCIAL + requires_approval capability. ──────────
    read_man = M.capability_manifest(
        "lookup", description="read a value", archetype="COMPUTE", effect_class="READ",
        caveats={"idempotent": True}, tags=["demo"])
    _, read_cap = M.install(kk, read_man, lambda _impl, args: {"out": f"value={args.get('key','?')}"})
    fin_man = M.capability_manifest(
        "wire.money", description="move money", archetype="EFFECT", effect_class="FINANCIAL",
        caveats={"requires_approval": True})
    _, fin_cap = M.install(kk, fin_man, lambda _impl, args: {"out": "wired 100"})

    # 1. initialize → serverInfo + tools capability. ────────────────────────────────────
    init = MS.handle(kk, _decima(kk), {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init["id"] == 1 and "result" in init, init
    res = init["result"]
    assert res["serverInfo"]["name"] == "decima", res
    assert "tools" in res["capabilities"], res
    line("  initialize → serverInfo + capabilities.tools (Decima announces itself as an "
         "MCP server) ✓")

    # 2. tools/list → both tools present, annotations reflect the TRUE gate. ─────────────
    listed = MS.handle(kk, _decima(kk), {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = {t["name"]: t for t in listed["result"]["tools"]}
    assert {"lookup", "wire.money"} <= set(tools), tools
    rd = tools["lookup"]["annotations"]
    fn = tools["wire.money"]["annotations"]
    assert rd["readOnlyHint"] is True and rd["destructiveHint"] is False, rd
    assert rd["idempotentHint"] is True, rd
    assert fn["readOnlyHint"] is False and fn["destructiveHint"] is True, fn
    line("  tools/list → both tools; annotations honest (READ→readOnlyHint; "
         "FINANCIAL+approval→destructiveHint — never looser than the real gate) ✓")

    # 3. tools/call of the READ tool → isError False + the result text. ─────────────────
    rc = MS.handle(kk, _decima(kk), {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                     "params": {"name": "lookup", "arguments": {"key": "k9"}}})
    call = rc["result"]
    assert call["isError"] is False, call
    assert "value=k9" in call["content"][0]["text"], call
    line("  tools/call READ → isError:false + output text (a gated invoke that authorize "
         "allowed) ✓")

    # 4. tools/call of the FINANCIAL tool → isError True + "approval" (gate through MCP). ─
    dc = MS.handle(kk, _decima(kk), {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                     "params": {"name": "wire.money", "arguments": {"amount": 100}}})
    denied = dc["result"]
    assert denied["isError"] is True, denied
    assert "approval" in denied["content"][0]["text"].lower(), denied
    line("  tools/call FINANCIAL → isError:true + 'approval' — Morta enforced THROUGH the "
         "MCP wire; the consumer cannot skip the gate ✓")

    # 5. After a human approve, the SAME tools/call → isError False. ────────────────────
    kk.approve(fin_cap)
    ac = MS.handle(kk, _decima(kk), {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                     "params": {"name": "wire.money", "arguments": {"amount": 100}}})
    approved = ac["result"]
    assert approved["isError"] is False, approved
    assert "wired 100" in approved["content"][0]["text"], approved
    line("  after k.approve(cap) the SAME tools/call → isError:false (human-in-the-loop "
         "resolved on Decima's side, then the tool runs) ✓")

    # 6. Unknown tool → JSON-RPC error -32602. ─────────────────────────────────────────
    ut = MS.handle(kk, _decima(kk), {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                                     "params": {"name": "nope", "arguments": {}}})
    assert "error" in ut and ut["error"]["code"] == -32602, ut
    # 7. Unknown method → JSON-RPC method-not-found -32601. ─────────────────────────────
    um = MS.handle(kk, _decima(kk), {"jsonrpc": "2.0", "id": 7, "method": "tools/frobnicate"})
    assert "error" in um and um["error"]["code"] == -32601, um
    line("  unknown tool → JSON-RPC -32602; unknown method → -32601 ✓")

    line("  → Decima's own tools are exposable AS an MCP server, yet every tools/call "
         "still runs the authorize + Morta gate (a Morta-gated tool returns isError until "
         "human approval) and annotations describe the true gate — pure stdlib, no bypass.")
