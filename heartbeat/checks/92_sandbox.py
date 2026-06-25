"""SB1 — sandboxed principals: profiles bound what a held capability may TOUCH.

ocap already decided this principal MAY invoke the effect; the sandbox profile
(`caveats["sandbox"]`, specs/SANDBOX.md) bounds the handler's footprint — network,
filesystem, and an optional effect allowlist — enforced at the executor boundary BEFORE
the handler runs. This check proves:
  - an in-profile effect runs; an out-of-profile one is REFUSED before dispatch (the
    handler is never called) and recorded as a FAILED receipt (audited, `error.code=sandbox`);
  - network-denied blocks a network effect; an fs access outside scope is blocked; an
    effect not in the allowlist is blocked;
  - a None/absent profile is unrestricted (back-compatible reference default).

Contract: run(k, line). Fail loud.
"""
from decima import executor
from decima.weft import ASSERT
from decima.capability import capability_content
from decima.hashing import content_id


def run(k, line):
    line("\n== SANDBOX (a profile bounds what a held capability may touch) — SB1 ==")
    ran = {"net": 0, "file": 0}   # proves whether a handler actually executed

    def _netcall(impl, args):
        ran["net"] += 1
        return {"out": f"fetched {args.get('url', '')}"}

    def _readfile(impl, args):
        ran["file"] += 1
        return {"out": f"read {args.get('path', '')}"}

    executor.register("netcall", _netcall)
    executor.register("readfile", _readfile)

    def make_cap(name, effect, impl, sandbox):
        cid = content_id({"sbcap": name})
        content = capability_content(name=name, effect=effect, impl=impl,
                                     caveats={"sandbox": sandbox})
        k.weft.append(k.root.id, ASSERT, {"cell": cid, "type": "capability", "content": content})
        k.grant(cid, k.decima_agent_id)
        return cid

    def dec():                       # fresh Decima cell (post-grant envelope)
        return k.weave().get(k.decima_agent_id)

    # 1. network ALLOWED → runs.
    c_open = make_cap("fetch-open", "netcall", {"requires": ["network"]}, {"network": True})
    r = k.invoke(dec(), c_open, {"url": "decima.dev"})
    assert "ok" in r and ran["net"] == 1, r
    line(f"  network-allowed netcall → ran, out={r['ok']['out']!r}")

    # 2. network DENIED → refused pre-dispatch; handler never ran; FAILED receipt audited.
    c_closed = make_cap("fetch-closed", "netcall", {"requires": ["network"]}, {"network": False})
    before = ran["net"]
    r = k.invoke(dec(), c_closed, {"url": "decima.dev"})
    rc = k.weave().get(r["result_cell"])
    assert "denied" in r and "network" in r["denied"], r
    assert ran["net"] == before, "handler ran despite sandbox denial!"
    assert rc.content["status"] == "FAILED" and rc.content["error"]["code"] == "sandbox", rc.content
    line(f"  network-denied netcall → ✋ {r['denied']}; handler ran={ran['net'] != before}; "
         f"receipt status={rc.content['status']} (audited)")

    # 3. fs scope: in-scope read runs; out-of-scope read is refused.
    c_fs = make_cap("read-data", "readfile", {"requires": ["fs_read"]}, {"fs_read": ["/data"]})
    ok = k.invoke(dec(), c_fs, {"path": "/data/notes.txt"})
    assert "ok" in ok and ran["file"] == 1, ok
    bad = k.invoke(dec(), c_fs, {"path": "/etc/passwd"})
    assert "denied" in bad and "scope" in bad["denied"] and ran["file"] == 1, (bad, ran)
    line(f"  fs_read scope ['/data']: /data/notes.txt → ran; /etc/passwd → ✋ {bad['denied']}")

    # 4. effects allowlist: a netcall cap restricted to {echo} is refused.
    c_allow = make_cap("echo-only", "netcall", {"requires": ["network"]}, {"effects": ["echo"]})
    r = k.invoke(dec(), c_allow, {"url": "decima.dev"})
    assert "denied" in r and "allowlist" in r["denied"], r
    line(f"  effects allowlist ['echo'] on a netcall cap → ✋ {r['denied']}")

    # 5. no profile → unrestricted (reference default, back-compatible).
    c_none = make_cap("fetch-noprofile", "netcall", {"requires": ["network"]}, None)
    r = k.invoke(dec(), c_none, {"url": "x"})
    assert "ok" in r, r
    line("  no profile → unrestricted (reference default; production is default-deny)")
    line("  → ocap says MAY-invoke; the sandbox bounds MAY-touch — refused pre-dispatch, "
         "audited as a FAILED receipt. Real OS/WASM enforcement is the seam (SANDBOX.md).")
