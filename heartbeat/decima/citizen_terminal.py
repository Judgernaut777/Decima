"""CITIZEN TERMINAL — compose A3 (terminal.py: multiplexed sessions over the
SANDBOXED, allowlisted `shell` effect) with TERMINALS-AS-CITIZENS (citizens.py:
attenuated sub-principal admission), so a terminal session runs as a CITIZEN —
an attenuated sub-principal — never as the realm orchestrator.

Both subsystems are proven on their own (checks/272_terminal.py,
checks/440_citizens.py); this module is pure COMPOSITION over their public
surfaces, no core edit:

  - `open_citizen_terminal` admits a citizen (`citizens.admit_citizen`) holding a
    DOWNHILL-attenuated shell grant, derived from the SAME base capability
    `terminal._shell_cap` would hand the orchestrator itself — the citizen's
    authority is a narrowing of exactly the orchestrator's own terminal-shell
    grant, never a fresh unrelated one. The Morta floor for the `shell` effect
    class (`requires_approval`) is merged in at birth (citizens._narrowed_grant)
    even though the boot-time base grant lacks it — a citizen's shell access is
    MORE gated than the orchestrator's, never less.
  - `citizen_run` drives the shell effect through `citizens.citizen_invoke` — the
    citizen's OWN attenuated grant, its OWN principal signs the INVOKE, its OWN
    narrowed-target-scope gate runs, its OWN audit Cell (`citizen_action`) is
    written, and its output crosses the trust boundary through the citizen
    disposition router (untrusted DATA). It is NOT `terminal.run` reused
    directly (that composes `kernel.invoke` against the ACTING AGENT it is
    handed — calling it with the citizen's own agent cell would happen to work
    structurally, since a citizen cell shares the ordinary agent envelope
    shape, but it would skip citizen_invoke's OWN gate — the narrowed target
    check — and its audit/disposition wiring entirely). Instead `citizen_run`
    invokes the citizen's shell grant through `citizen_invoke` and then performs
    the SAME scrollback bookkeeping `terminal.run` performs on success (a
    `terminal_entry` Cell, instruction_eligible=False, bound to the session by a
    `scrollback` edge) — so `terminal.scrollback(k, session)` reads identically
    regardless of which principal actually ran the command, while the Weft
    additionally carries the citizen's own `citizen_action` audit Cell and
    untrusted-output disposition that only citizen_invoke produces.

FAIL CLOSED: a citizen admitted without a shell grant (no `from_cap`, or a
`from_cap` whose effect isn't `shell`) holds nothing to invoke through — nothing
runs, nothing is appended (default-deny, Law 2, no ambient authority). A citizen
invoking outside its narrowed target scope is denied by citizen_invoke's own
gate. Authority only ever flows downhill — re-attenuating a citizen's shell
grant to a wider budget/target is rejected by `citizens.re_attenuate`
(structural proof, not a silent clamp).

Ints-not-floats: every recorded numeric here is an int. Command OUTPUT stays
DATA: the appended scrollback entry is instruction_eligible=False, exactly as
`terminal.run` mints it, and a successful citizen_invoke additionally ingests
the same output as untrusted DATA via the citizen disposition router.
"""
from decima import citizens, terminal
from decima.hashing import content_id
from decima.model import assert_content, assert_edge

# The citizen's terminal-shell grant enters narrower than the orchestrator's own
# terminal budget (terminal._TERMINAL_BUDGET) — a citizen terminal gets less
# headroom than the realm orchestrator, never more or equal-by-accident.
_CITIZEN_SHELL_BUDGET = 50


def _parent_shell_cap_id(k) -> str:
    """The parent shell capability id a citizen terminal attenuates FROM: the
    SAME capability `terminal._shell_cap` resolves for the orchestrator itself
    (the base `shell` grant, or the budgeted `terminal.shell` grant it mints on
    demand) — never a capability this module invents."""
    decima = k.weave().get(k.decima_agent_id)
    cap = terminal._shell_cap(k, decima)
    if cap is None:
        raise ValueError(
            "no shell capability held by the orchestrator to attenuate a citizen "
            "terminal grant from")
    return cap.id


def open_citizen_terminal(k, citizen_name: str, session_name: str, *,
                          from_cap: str | None = None) -> dict:
    """Admit `citizen_name` as a CITIZEN holding a DOWNHILL-attenuated shell
    grant, and open a terminal session for it to run through. `from_cap=None`
    (the default) derives the parent automatically — the same base capability
    `terminal._shell_cap` would hand the orchestrator; pass an explicit
    `from_cap` to attenuate from something narrower still. The grant is scoped
    to the `shell` effect ONLY (an effect-allowlist, the sandbox caveat the
    executor enforces) with a shrunk budget, and is born carrying the `shell`
    effect class's Morta floor (`requires_approval`) — a human must still
    approve before this citizen's shell grant will run anything, exactly like
    any other freshly narrowed shell citizen (checks/440_citizens).

    Returns {"citizen", "principal", "session", "grant", "admission"}. The
    session itself holds no authority (terminal.py's own invariant) — all
    gating lives on the citizen's attenuated grant.
    """
    parent_id = from_cap if from_cap is not None else _parent_shell_cap_id(k)
    adm = citizens.admit_citizen(
        k, citizen_name, from_cap=parent_id,
        narrow={"effects": [terminal._SHELL_EFFECT], "budget": _CITIZEN_SHELL_BUDGET})
    session = terminal.open_session(k, session_name)
    return {"citizen": adm["citizen"], "principal": adm["principal"],
            "session": session, "grant": adm["grant"], "admission": adm["admission"]}


def _citizen_shell_grant(w, cz):
    """The citizen's OWN held grant on the `shell` effect (or None) — resolved
    strictly within the citizen's envelope, never against the realm's latest
    capability of any name (fail closed, no ambient authority)."""
    for gid in cz.content.get("envelope", []):
        g = w.get(gid)
        if (g is not None and g.type == "capability" and not g.retracted
                and g.content.get("effect") == terminal._SHELL_EFFECT):
            return g
    return None


def citizen_run(k, citizen_id: str, session: str, command: str) -> dict:
    """Run `command` in `session` AS the citizen — driven through
    `citizens.citizen_invoke`, so the shell effect's authorization, the signed
    INVOKE, and the audit Cell are ALL attributed to the citizen's OWN
    principal, never the orchestrator's. Reaches the exact same sandboxed,
    allowlisted `shell` handler `terminal.run` uses (no new commands, no
    allowlist bypass), then appends the output to the session's scrollback as
    DATA — the same `terminal_entry` shape `terminal.run` produces — so
    `terminal.scrollback(k, session)` reads identically no matter which
    principal ran the command.

    FAIL CLOSED: a citizen with no shell grant in its envelope is refused here,
    before any invoke is attempted — nothing runs, nothing is appended
    (default-deny). A citizen invoking outside its narrowed target scope, or a
    Morta-gated grant awaiting human approval, is denied by citizen_invoke's own
    gate (`{"refused": ...}` is returned, mirroring `terminal.run`'s shape) and
    nothing is appended either.

    Returns {"refused": reason} or {"out", "entry", "receipt", "seq", "citizen",
    "principal", "signer", "action", "disposition"}.
    """
    w = k.weave()
    cz = w.get(citizen_id)
    if cz is None or cz.type != "agent" or not cz.content.get("citizen"):
        raise ValueError(f"{citizen_id!r} is not an admitted citizen")
    sess = w.get(session)
    if sess is None or sess.type != terminal.SESSION_TYPE:
        raise ValueError(f"no such terminal session: {session!r}")

    shell_grant = _citizen_shell_grant(w, cz)
    if shell_grant is None:
        # No ambient authority: nothing to invoke through — fail closed, refused
        # BEFORE citizen_invoke is even called, nothing appended.
        return {"refused": "citizen holds no shell grant (no ambient authority)"}

    args = {"cmd": command}
    scope = shell_grant.content.get("target", "*")
    if scope != "*":
        args["target"] = scope    # name the citizen's own narrowed target scope

    res = citizens.citizen_invoke(k, citizen_id, shell_grant.id, args)

    if "denied" in res:
        return {"refused": res["denied"], "status": res.get("status"),
                "action": res.get("action_cell")}
    if "proposed" in res:
        return {"refused": f"queued for approval: {res.get('autonomy')}",
                "action": res.get("action_cell")}

    receipt = res["result_cell"]
    out = res["ok"].get("out", "")

    # Append the output to THIS session's isolated scrollback, exactly as
    # terminal.run does on success — OUTPUT IS DATA, never obeyed.
    w = k.weave()   # re-fold: citizen_invoke may have appended events (audit + disposition)
    seq = len(terminal._scrollback_cells(w, session))
    author = k.decima_agent_id
    principal = cz.content["principal"]
    eid = content_id({"terminal_entry": session, "command": command, "seq": seq,
                      "citizen": citizen_id})
    assert_content(k.weft, author, eid, terminal.ENTRY_TYPE, {
        "session": session,
        "command": command,
        "out": out,
        "seq": seq,
        "instruction_eligible": False,   # OUTPUT IS DATA — never an instruction
        "receipt": receipt,
        "citizen": citizen_id,
        "principal": principal,
    })
    assert_edge(k.weft, author, session, terminal._SCROLLBACK_REL, eid)
    assert_edge(k.weft, author, eid, "produced_by", receipt)
    assert_edge(k.weft, author, eid, "run_by", citizen_id)
    return {"out": out, "entry": eid, "receipt": receipt, "seq": seq,
            "citizen": citizen_id, "principal": principal,
            "signer": res.get("signer"), "action": res.get("action_cell"),
            "disposition": res.get("disposition")}
