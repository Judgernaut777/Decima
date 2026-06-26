"""Terminals + session multiplexing (A3) — built on the SANDBOXED `shell` effect.

Decima operates the machine (A4) the same way it does everything else: through an
ocap-gated, sandboxed, allowlisted effect, audited on the Weft. This module adds NO
new authority and NO new shell commands. It is a thin MULTIPLEXER over the kernel's
existing public surface:

  - a `terminal_session` is a named Cell — many can be open at once, each with its
    OWN isolated scrollback (the multiplexing);
  - `run(...)` executes a command by INVOKING the agent's existing `shell`
    capability via `kernel.invoke` — the SAME allowlisted, sandboxed `shell` handler
    (executor._SHELL_ALLOWLIST). A command not on that allowlist is refused FAIL
    CLOSED by the executor; nothing runs and nothing is appended;
  - the command's OUTPUT is appended to that session's scrollback as DATA — a
    `terminal_entry` Cell marked `instruction_eligible=False`. Command output is
    DATA: it may be read back, but it is NEVER obeyed.

LAWS honored, none re-implemented:
  - execution is the existing sandboxed/allowlisted `shell` effect (no shell
    interpolation, no arbitrary commands) — we compose `kernel.invoke`, never the
    subprocess;
  - a non-allowlisted command is refused (the executor raises, `invoke` returns a
    FAILED/denied receipt) — fail closed;
  - sessions are isolated — a `scrollback` edge binds an entry to exactly one
    session, so one session's history can never leak into another's;
  - output is DATA (never obeyed) — entries are born instruction-ineligible;
  - no ambient authority — `run` needs the acting agent to actually HOLD `shell`;
  - everything is audited on the Weft (the session cell, the INVOKE + EffectReceipt
    the kernel writes, and the scrollback entry).

Determinism: a scrollback entry carries an explicit per-session `seq` (the count of
entries already in that session at append time), so the history folds in a stable
order independent of arrival order, like the rest of the Weave.
"""
from decima.agent import held_capabilities
from decima.hashing import content_id
from decima.model import assert_content, assert_edge

SESSION_TYPE = "terminal_session"
ENTRY_TYPE = "terminal_entry"
_SCROLLBACK_REL = "scrollback"      # session --scrollback--> entry
_SHELL_EFFECT = "shell"             # the EXISTING allowlisted, sandboxed effect
_TERMINAL_BUDGET = 10_000          # headroom for an interactive terminal's many runs


def _shell_cap(k, agent):
    """The capability THIS agent will invoke to reach the sandboxed `shell` effect.

    We bind to the `shell` EFFECT (executor._SHELL_ALLOWLIST), never a new command:
    any held, non-quarantined grant whose effect is `shell` qualifies, so an agent
    that already holds `shell` uses exactly that grant (no ambient authority — an
    agent holding nothing gets None and is refused). For the orchestrator we ensure
    a budgeted terminal grant exists (a long-lived terminal issues many runs), minted
    + granted through the kernel's own public capability path — still the same
    allowlisted, sandboxed handler, with NO new shell commands.
    """
    held = [c for c in held_capabilities(k.weave(), agent)
            if c.content.get("effect") == _SHELL_EFFECT]
    if not held:
        return None
    # Prefer a grant whose budget is not exhausted for this agent (a terminal runs
    # many commands); fall back to any held shell grant.
    spent = k.spent.get(agent.id, 0.0)
    live = [c for c in held
            if c.content.get("caveats", {}).get("budget", float("inf")) > spent]
    if live:
        return live[0]
    if agent.id == k.decima_agent_id:
        cap_id = k._assert_cap("terminal.shell", _SHELL_EFFECT,
                               caveats={"budget": _TERMINAL_BUDGET})
        k.grant(cap_id, k.decima_agent_id)
        return k.weave().get(cap_id)
    return held[0]


def open_session(k, name: str) -> str:
    """Open a named terminal session (multiplexing). Returns the session cell id.

    Many sessions can be open at once; each is content-addressed by name so opening
    the same name twice refers to the same session (its scrollback persists). The
    session itself holds no authority — `run` gates on the acting agent's `shell`
    grant — and carries no command output (that lives in isolated scrollback cells).
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("terminal session needs a non-empty name")
    sid = content_id({"terminal_session": name})
    author = k.decima_agent_id
    assert_content(k.weft, author, sid, SESSION_TYPE, {"name": name})
    return sid


def sessions(k) -> list[str]:
    """The ids of the open terminal sessions (every live `terminal_session` cell)."""
    return [c.id for c in k.weave().of_type(SESSION_TYPE)]


def _scrollback_cells(weave, session: str):
    """The entry cells bound to `session`, in deterministic per-session `seq` order.

    Isolation is structural: an entry belongs to a session iff a `scrollback` edge
    runs from that session to it — so another session's entries are simply never in
    this list, regardless of fold/arrival order.
    """
    entries = []
    for e in weave.edges_from(session, _SCROLLBACK_REL):
        cell = weave.get(e["dst"])
        if cell is not None and cell.type == ENTRY_TYPE and not cell.retracted:
            entries.append(cell)
    entries.sort(key=lambda c: c.content.get("seq", 0))
    return entries


def run(k, agent, session: str, command: str) -> dict:
    """Run `command` in `session` via the SANDBOXED, allowlisted `shell` effect.

    `agent` is the acting agent cell (it must HOLD `shell` — no ambient authority).
    `command` is an allowlist KEY (e.g. "date"/"uname"/"whoami"), not a shell string:
    it is passed as `{"cmd": command}` to the existing `shell` capability, whose
    handler refuses anything off `executor._SHELL_ALLOWLIST` fail-closed. We add NO
    commands and do NOT bypass the allowlist.

    On success the output is appended to the session's scrollback as DATA (an entry
    cell, instruction_eligible=False, linked by a `scrollback` edge). On a refusal
    (non-allowlisted command, or the agent lacking `shell`) NOTHING is appended and
    the refusal is returned — auditable on the Weft via the FAILED receipt the kernel
    wrote. Returns a dict: {"refused": reason} or {"out", "entry", "receipt", "seq"}.
    """
    w = k.weave()
    if w.get(session) is None or w.get(session).type != SESSION_TYPE:
        raise ValueError(f"no such terminal session: {session!r}")

    shell = _shell_cap(k, agent)
    if shell is None:
        # No ambient authority: an agent that does not hold `shell` cannot run.
        return {"refused": "no shell capability held (no ambient authority)"}
    # _shell_cap may have minted/granted a terminal shell grant, so re-resolve the
    # acting agent cell from the fresh Weave (its envelope now holds that grant).
    w = k.weave()
    agent = w.get(agent.id)

    # Execute through the kernel's gated path: authorize -> INVOKE -> sandboxed
    # allowlisted `shell` handler -> EffectReceipt. The executor refuses a
    # non-allowlisted command (raises ExecError -> FAILED receipt -> denied).
    res = k.invoke(agent, shell.id, {"cmd": command})
    if "denied" in res:
        return {"refused": res["denied"], "status": res.get("status"),
                "receipt": res.get("result_cell")}

    receipt = res["result_cell"]
    out = res["ok"].get("out", "")

    # Append the OUTPUT as DATA to THIS session's isolated scrollback. The entry is
    # born instruction-ineligible: command output may be recalled but never obeyed.
    seq = len(_scrollback_cells(w, session))
    author = k.decima_agent_id
    eid = content_id({"terminal_entry": session, "command": command, "seq": seq})
    assert_content(k.weft, author, eid, ENTRY_TYPE, {
        "session": session,
        "command": command,
        "out": out,
        "seq": seq,
        "instruction_eligible": False,   # OUTPUT IS DATA — never an instruction
        "receipt": receipt,
    })
    # Bind the entry to its session (isolation) and ground it in the EffectReceipt.
    assert_edge(k.weft, author, session, _SCROLLBACK_REL, eid)
    assert_edge(k.weft, author, eid, "produced_by", receipt)
    return {"out": out, "entry": eid, "receipt": receipt, "seq": seq}


def scrollback(k, session: str) -> list[dict]:
    """The session's command/output history, oldest-first.

    Each item: {"command", "out", "instruction_eligible" (False), "entry",
    "receipt", "seq"}. Reconstructed by folding the `scrollback` edges, so it
    time-travels and is arrival-order independent like all Weave state.
    """
    items = []
    for cell in _scrollback_cells(k.weave(), session):
        c = cell.content
        items.append({"command": c.get("command"), "out": c.get("out", ""),
                      "instruction_eligible": c.get("instruction_eligible", False),
                      "entry": cell.id, "receipt": c.get("receipt"),
                      "seq": c.get("seq", 0)})
    return items
