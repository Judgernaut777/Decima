"""A3 — Built-in terminals + session multiplexing, on the SANDBOXED `shell` effect.

Proves, composing executor(shell)/kernel/capability PUBLIC APIs only (NO new shell
commands, NO allowlist bypass):

  - open TWO named sessions (multiplexing) — each its own isolated scrollback;
  - run an ALLOWLISTED command (sandboxed, audited via the kernel's INVOKE +
    EffectReceipt) → its OUTPUT is appended to that session's scrollback as DATA;
  - a NON-allowlisted command is REFUSED (fail closed) — nothing runs, nothing is
    appended;
  - the two sessions' scrollbacks are ISOLATED (one never sees the other's history);
  - command OUTPUT is DATA — entries are instruction-ineligible, never obeyed.

Contract: run(k, line). Fail loud.
"""
from decima import terminal


def run(k, line):
    line("\n== TERMINALS + SESSION MULTIPLEXING (sandboxed allowlisted shell) — A3 ==")
    agent = k.weave().get(k.decima_agent_id)

    # ── multiplexing: two named sessions, each its own isolated scrollback ────
    s1 = terminal.open_session(k, "ops-1")
    s2 = terminal.open_session(k, "ops-2")
    assert s1 != s2, "two named sessions must be distinct"
    open_ids = set(terminal.sessions(k))
    assert {s1, s2} <= open_ids, f"both sessions must be open, got {open_ids}"
    assert terminal.scrollback(k, s1) == [] and terminal.scrollback(k, s2) == [], \
        "fresh sessions must start with empty scrollback"
    line(f"  opened 2 sessions: ops-1 {s1[:8]}, ops-2 {s2[:8]} — isolated scrollbacks ✓")

    # ── an ALLOWLISTED command runs (sandboxed + audited), output appended as DATA ──
    # "whoami"/"date"/"uname" are the existing executor._SHELL_ALLOWLIST keys; we add none.
    r1 = terminal.run(k, agent, s1, "whoami")
    assert "refused" not in r1, f"allowlisted command must run, got {r1}"
    assert r1["out"], "allowlisted command produced no output"
    # Audited: the run produced a real EffectReceipt on the Weft.
    receipt = k.weave().get(r1["receipt"])
    assert receipt is not None and receipt.type == "result", "run not audited as a receipt"
    assert receipt.content["status"] == "SUCCEEDED", receipt.content
    # Must run through the SANDBOXED, allowlisted `shell` EFFECT — the same handler,
    # never a new command. (The cap NAME may be a budgeted terminal grant; its effect
    # is what binds it to executor._SHELL_ALLOWLIST.)
    ran_via = next(c for c in k.weave().of_type("capability")
                   if c.content.get("name") == receipt.content["cap"])
    assert ran_via.content["effect"] == "shell", \
        f"must run through the shell effect, got {ran_via.content['effect']}"
    line(f"  ops-1 run 'whoami' → '{r1['out']}' (receipt {r1['receipt'][:8]} "
         f"{receipt.content['status']}, effect=shell, sandboxed) ✓")

    # The OUTPUT is in ops-1's scrollback, and it is DATA (never an instruction).
    sb1 = terminal.scrollback(k, s1)
    assert len(sb1) == 1 and sb1[0]["command"] == "whoami", sb1
    assert sb1[0]["out"] == r1["out"], "scrollback output does not match the receipt"
    assert sb1[0]["instruction_eligible"] is False, "command output must be DATA, not obeyed"
    entry = k.weave().get(r1["entry"])
    assert entry.content["instruction_eligible"] is False, "entry cell must be instruction-ineligible"
    line(f"  output appended to ops-1 scrollback as DATA "
         f"(instruction_eligible={sb1[0]['instruction_eligible']} — never obeyed) ✓")

    # ── a NON-allowlisted command is REFUSED (fail closed) — nothing appended ──
    before = len(terminal.scrollback(k, s1))
    bad = terminal.run(k, agent, s1, "rm -rf /")
    assert "refused" in bad, f"non-allowlisted command must be refused, got {bad}"
    assert bad.get("status") == "FAILED", f"refusal must be a FAILED receipt, got {bad}"
    assert "allowlist" in bad["refused"].lower(), bad["refused"]
    assert len(terminal.scrollback(k, s1)) == before, \
        "a refused command must NOT append to the scrollback (fail closed)"
    line(f"  ops-1 run 'rm -rf /' → ✋ refused (off allowlist, FAILED) — nothing appended ✓")

    # ── isolation: a run in ops-2 does NOT appear in ops-1's scrollback ───────
    r2 = terminal.run(k, agent, s2, "uname")
    assert "refused" not in r2, f"allowlisted command must run in ops-2, got {r2}"
    sb1b, sb2 = terminal.scrollback(k, s1), terminal.scrollback(k, s2)
    assert len(sb2) == 1 and sb2[0]["command"] == "uname", sb2
    assert [e["command"] for e in sb1b] == ["whoami"], \
        f"ops-1 scrollback leaked ops-2's command: {sb1b}"
    assert all(e["entry"] != sb2[0]["entry"] for e in sb1b), "scrollback entries crossed sessions"
    line(f"  ops-2 run 'uname' → '{r2['out']}'; ops-1 still only ['whoami'] — "
         f"scrollbacks ISOLATED ✓")

    # ── no ambient authority: an agent that does not hold `shell` cannot run ──
    # Mint a bare sandbox agent with an EMPTY envelope (held nothing) and try to run.
    from decima.hashing import content_id
    from decima.weft import ASSERT
    imp = k.keyring.mint("term-intruder", "agent")
    iid = content_id({"agent": "term-intruder"})
    k.weft.append(k.root.id, ASSERT, {"cell": iid, "type": "agent",
        "content": {"principal": imp.id, "objective": "x", "envelope": [], "sandbox": True}})
    intruder = k.weave().get(iid)
    denied = terminal.run(k, intruder, s1, "whoami")
    assert "refused" in denied, f"an agent without shell must be refused, got {denied}"
    assert len(terminal.scrollback(k, s1)) == 1, "a denied run must not append output"
    line(f"  intruder (envelope []) run 'whoami' → ✋ refused (no ambient authority) ✓")

    line("  → A3: many named sessions multiplex over the SAME allowlisted, sandboxed "
         "`shell` effect; output is isolated, audited DATA; off-allowlist fails closed.")
