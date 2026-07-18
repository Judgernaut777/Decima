"""CITIZEN TERMINAL — a terminal session runs as a CITIZEN, not the orchestrator.

Composes two proven-but-unconnected stacks (checks/272_terminal.py's multiplexed
sessions over the sandboxed `shell` effect, checks/440_citizens.py's attenuated
sub-principal admission) via decima/citizen_terminal.py:

  (a) ATTRIBUTED + GATED (load-bearing) — a citizen terminal opened with
      `open_citizen_terminal` holds a shell grant born with the `shell` effect
      class's Morta floor: `citizen_run` is REFUSED until a human approves that
      exact grant (the citizen's shell access is MORE gated than the
      orchestrator's own, never less). Once approved, running a command through
      it lands an INVOKE on the Weft signed by the CITIZEN's own principal
      (never the orchestrator's), authorized by the citizen's OWN attenuated
      grant (never the orchestrator's base `shell` cap), with a `citizen_action`
      audit Cell naming it — and the output lands in the session's scrollback as
      DATA (instruction_eligible=False), reading identically to an
      orchestrator-run entry.
  (b) FAIL CLOSED, NO GRANT — a citizen admitted with no shell grant (or any
      grant at all) cannot run a command through the terminal: `citizen_run`
      refuses before any invoke is attempted, and nothing is appended to the
      session's scrollback (default-deny, no ambient authority).
  (c) DOWNHILL ONLY — the citizen's shell grant cannot be re-attenuated to a
      wider budget or a wider target: `citizens.re_attenuate` rejects the
      widening with `CitizenError` (a legitimate narrowing still flows), and
      `capability.attenuation_valid` independently confirms the widened shape
      is not a valid downhill delegation.

Mutation-resistance (the load-bearing line, demonstrated directly): running the
command via `k.invoke(decima_agent, base_shell_cap, ...)` — i.e. as
`k.decima_agent_id`/the orchestrator, exactly what a `citizen_run` that forgot
to route through `citizens.citizen_invoke` would collapse to — signs the INVOKE
with the ORCHESTRATOR's principal instead of the citizen's, and leaves no
`citizen_action` audit Cell naming that INVOKE at all: assertion (a)'s
attribution/gating check goes RED under that mutation. The real `citizen_run`
path (already exercised above in this same run) recovers both — GREEN.

Contract: run(k, line). Fail loud (assert). Owns its own fresh Kernel over a
tmp db; no ambient effects registered.
"""
import os
import tempfile

from decima import capability, citizen_terminal, citizens, terminal
from decima.kernel import Kernel
from decima.weft import INVOKE


def _n_citizen_actions(w) -> int:
    return len(w.of_type(citizens.CITIZEN_ACTION))


def run(k, line):
    line("\n== CITIZEN TERMINAL — a terminal session runs as a CITIZEN (Batch U) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    decima = kk.weave().get(kk.decima_agent_id)
    decima_principal = decima.content["principal"]

    # ── (a) ATTRIBUTED + GATED ───────────────────────────────────────────────
    opened = citizen_terminal.open_citizen_terminal(kk, "term-citizen-1", "ops-citizen")
    citizen_id, grant_id, session = opened["citizen"], opened["grant"], opened["session"]
    citizen_principal = opened["principal"]
    assert citizen_principal != decima_principal, \
        "the citizen must have its OWN principal, distinct from the orchestrator's"

    gcell = kk.weave().get(grant_id)
    assert gcell.content["effect"] == terminal._SHELL_EFFECT, \
        f"the citizen's grant must be on the shell effect: {gcell.content}"
    parent_cap = terminal._shell_cap(kk, decima)
    assert gcell.content["parent"] == parent_cap.id, \
        "the citizen's grant must be attenuated FROM the orchestrator's own " \
        "terminal-shell capability, the SAME one terminal._shell_cap resolves"
    assert gcell.content["caveats"]["sandbox"]["effects"] == [terminal._SHELL_EFFECT], \
        "the effect-allowlist must bind to the sandboxed shell effect, no new commands"
    assert gcell.content["caveats"]["requires_approval"] is True, \
        "a citizen shell grant must be BORN with the Morta floor, even though the " \
        "orchestrator's own base grant need not carry it"
    assert gcell.content["caveats"]["budget"] < parent_cap.content["caveats"].get("budget", 10**9), \
        "the citizen's budget must be strictly narrower than the orchestrator's own"
    line(f"  admitted term-citizen-1 ({citizen_id[:8]}) holding ONE attenuated shell "
         f"grant ({grant_id[:8]}) attenuated from the orchestrator's own terminal-shell "
         "cap, Morta-floored (requires_approval) from birth ✓")

    # Before human approval, the Morta-gated grant refuses — GATED holds even for
    # an otherwise-legitimate citizen run.
    pre = citizen_terminal.citizen_run(kk, citizen_id, session, "whoami")
    assert "refused" in pre, f"an unapproved Morta-gated citizen grant must refuse: {pre}"
    assert terminal.scrollback(kk, session) == [], \
        "a refused (unapproved) run must append NOTHING to the scrollback"
    line("  before human approval: citizen_run refuses (Morta gate intact) — "
         "nothing appended ✓")

    # The human approves THIS grant (Morta gate) — the ordinary way any
    # requires_approval capability is opened.
    kk.approve(grant_id)
    n_acts0 = _n_citizen_actions(kk.weave())
    res = citizen_terminal.citizen_run(kk, citizen_id, session, "whoami")
    assert "refused" not in res, f"an approved citizen grant must run: {res}"
    assert res["out"], "the command must produce output"
    assert res["signer"] == citizen_principal, \
        f"the INVOKE must be signed by the CITIZEN's principal, got {res['signer']!r}"
    assert res["signer"] != decima_principal, \
        "the INVOKE must NOT be attributed to the orchestrator"

    # Ground truth on the Weft itself — not just the returned dict.
    inv_ev = next(ev for ev in kk.weft.events()
                 if ev.verb == INVOKE and ev.authorized == grant_id)
    assert inv_ev.author == citizen_principal, \
        f"the ledger's INVOKE event must be authored (signed) by the citizen's " \
        f"principal, got {inv_ev.author!r}"
    assert inv_ev.authorized == grant_id, \
        "the INVOKE must be authorized by the CITIZEN's OWN attenuated grant, " \
        "never the orchestrator's base shell capability"

    acts = kk.weave().of_type(citizens.CITIZEN_ACTION)
    assert len(acts) == n_acts0 + 1, "the run must leave exactly one new citizen_action Cell"
    act = acts[-1]
    assert act.content["citizen"] == citizen_id and act.content["cap"] == grant_id \
        and act.content["outcome"] == "SUCCEEDED" and act.content["invoke_event"] == inv_ev.id, \
        f"the citizen_action audit Cell must name this citizen, grant, and INVOKE: {act.content}"
    line(f"  approved: citizen_run('whoami') → '{res['out']}', INVOKE signed by the "
         f"CITIZEN's principal ({citizen_principal[:8]}, not the orchestrator's "
         f"{decima_principal[:8]}), authorized by its OWN grant, a citizen_action "
         "audit Cell names it — ATTRIBUTED + GATED ✓")

    # The output landed in the session's scrollback as DATA, reading identically
    # to an orchestrator-run entry (terminal.scrollback is principal-agnostic).
    sb = terminal.scrollback(kk, session)
    assert len(sb) == 1 and sb[0]["command"] == "whoami" and sb[0]["out"] == res["out"], sb
    assert sb[0]["instruction_eligible"] is False, "citizen-run output must be DATA"
    entry = kk.weave().get(res["entry"])
    assert entry.content["instruction_eligible"] is False and entry.content["citizen"] == citizen_id
    line("  the command's output landed in the session scrollback as DATA "
         "(instruction_eligible=False), tagged with the citizen that ran it ✓")

    # citizen_invoke's OWN disposition additionally ingested the output as
    # untrusted data (the citizen-specific wiring terminal.run alone never had).
    assert res["disposition"] is not None, \
        "a successful citizen_invoke must disposition its output as untrusted data"

    # ── (b) FAIL CLOSED, NO GRANT ────────────────────────────────────────────
    stranger = citizens.admit_citizen(kk, "term-stranger")   # from_cap=None: empty envelope
    assert kk.weave().get(stranger["citizen"]).content["envelope"] == [], \
        "a grantless citizen must start with an EMPTY envelope"
    before_sb = terminal.scrollback(kk, session)
    denied = citizen_terminal.citizen_run(kk, stranger["citizen"], session, "whoami")
    assert "refused" in denied and "no shell grant" in denied["refused"], \
        f"a citizen with no shell grant must be refused BEFORE any invoke: {denied}"
    assert terminal.scrollback(kk, session) == before_sb, \
        "a refused (grantless) run must append NOTHING — even to a session others use"
    line("  a citizen admitted with NO shell grant is refused before any invoke is "
         "attempted — default-deny, fail closed, nothing appended ✓")

    # ── (c) DOWNHILL ONLY — cannot widen beyond the granted shell scope ─────
    widened = {**gcell.content, "parent": grant_id,
              "caveats": {**gcell.content["caveats"], "budget": 10**9}}
    okw, whyw = capability.attenuation_valid(widened, gcell.content)
    assert not okw, f"a widened budget must fail attenuation_valid: {whyw}"
    try:
        citizens.re_attenuate(kk, citizen_id, grant_id, caveats={"budget": 10**9})
        raise AssertionError("a WIDENED re-attenuation (bigger budget) was accepted")
    except citizens.CitizenError:
        pass
    try:
        citizens.re_attenuate(kk, citizen_id, grant_id, caveats={"requires_approval": False})
        raise AssertionError("dropping the Morta floor via re-attenuation was accepted")
    except citizens.CitizenError:
        pass
    narrower = citizens.re_attenuate(kk, citizen_id, grant_id, caveats={"budget": 1})
    assert kk.weave().get(narrower).content["caveats"]["budget"] == 1, \
        "a legitimate NARROWING must still flow (downhill stays open)"
    line("  downhill only: widening the citizen's budget or dropping its Morta floor "
         "is REJECTED (attenuation_valid false, re_attenuate refuses); a genuine "
         "narrowing still flows ✓")

    # ── MUTATION-RESISTANCE: run "as the orchestrator" instead of via citizen_invoke ──
    # This is exactly what a citizen_run that forgot to route through
    # citizens.citizen_invoke (e.g. calling terminal.run(k, decima_agent, ...) or
    # k.invoke(decima_agent, base_shell_cap, ...) directly) would collapse to.
    base_shell = next(c for c in kk.weave().of_type("capability")
                      if c.content.get("name") == "shell")
    mutated = kk.invoke(decima, base_shell.id, {"cmd": "whoami"})
    assert "ok" in mutated, f"the orchestrator's own base shell cap must run: {mutated}"
    assert mutated["signer"] == decima_principal and mutated["signer"] != citizen_principal, \
        "MUTATED: routing the run through the orchestrator instead of citizen_invoke " \
        "attributes the INVOKE to the ORCHESTRATOR, not the citizen — assertion (a)'s " \
        "attribution check goes RED under this mutation"
    acts_after = kk.weave().of_type(citizens.CITIZEN_ACTION)
    assert not any(a.content.get("invoke_event") == mutated["invoke_event"] for a in acts_after), \
        "MUTATED: no citizen_action audit Cell names this INVOKE — the citizen-side " \
        "gating/audit is entirely absent when the run bypasses citizen_invoke — " \
        "assertion (a)'s gating check goes RED"
    line("  mutation-resistance: running the command as k.decima_agent_id/the "
         "orchestrator (bypassing citizens.citizen_invoke) signs the INVOKE with the "
         "ORCHESTRATOR's principal and leaves no citizen_action audit Cell at all — "
         "(a)'s attribution+gating check goes RED under exactly that mutation; the "
         "real citizen_run path exercised above recovers both (signed by the citizen, "
         "audited) — GREEN.")

    line("  → a terminal session now runs as a CITIZEN: its own principal signs every "
         "run, its own attenuated (Morta-floored, budget-narrowed) grant authorizes it, "
         "its own audit Cell records it, a grantless citizen can run nothing, and "
         "authority only ever flows downhill.")
