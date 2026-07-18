"""FORGE / REFUSED SAY-HOOK SURFACING (Batch U) — the operator-facing surface for
"what did my last turn want to do but couldn't, and what does the catalog
suggest activating instead?"

Before this, a REFUSED `_delegate` step (ungranted / governance-banned / org-
policy-refused) and a `discover()`-submitted `catalog.activate:<name>` inbox
item both landed on the Weft — but nothing showed a human either one as a
single readable view. `decima/sayhook.py` is that view: a PURE read
(`refused_outcomes`, `activation_suggestions`, `surface`) over the same weave/
inbox every other read-only report already uses. This check proves:

  - a `say` turn whose delegate plan needs a capability Decima does not hold
    ("ungranted") surfaces via `refused_outcomes`;
  - a `say` turn a recorded governance ban refuses at delegate-time
    ("governance_denied") surfaces too, citing the rule cell;
  - refused rows order newest-relevant first by LAMPORT (an int on the Weft's
    own logical clock — never a wall-clock read);
  - a `discover()`-driven "use" suggestion's `catalog.activate:<name>`
    ApprovalInbox item surfaces via `activation_suggestions`, with its manifest
    provenance;
  - `surface()` MINTS/RECORDS NOTHING: `k.weft.count()` is identical before and
    after the call — this is a read, never an approval, never an activation;
  - every returned dict is DATA (`instruction_eligible: False`) — a refused
    capability name or a catalog manifest's text is untrusted content and must
    never be treated as an instruction.

Mutation-resistance (documented, not executed here — apply by hand to drive
RED, then revert):
  - Make `sayhook.surface` call `discovery.submit_activation`/
    `ApprovalInbox.approve`/`.deny` (mint an approval or auto-activate a
    capability) → the "Weft count unchanged" assertion below goes RED.
  - Narrow `REFUSED_STATUSES` in sayhook.py to just `("refused",)` (drop
    "ungranted"/"governance_denied") → the ungranted/governance-denied
    assertions below go RED (nothing surfaces for either refusal class).

Contract: run(k, line). Fail loud. Owns its own fresh Kernel over a tmp db (the
shared `k` is untouched — this lane reads only, mints nothing on the shared
weave either).
"""
import os
import tempfile
import types

from decima import discovery as D
from decima import manifest as M
from decima import memory
from decima import model as _model
from decima import sayhook
from decima.agent import RuleBrain
from decima.hashing import content_id
from decima.inbox import ApprovalInbox
from decima.kernel import Kernel


def run(k, line):
    line("\n== SAY-HOOK SURFACING (refused steps + activation suggestions, read-only) — Batch U ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "w.db"), fresh=True)
    kk.brain = RuleBrain()          # deterministic decide(): no model/network dependency

    # 1. UNGRANTED — a say turn whose delegate plan needs a capability Decima
    #    does not hold ("delegate <cap> as <name>: <objective>" — RuleBrain's
    #    own syntax; kernel.say routes a "delegate" Action straight to _delegate).
    said = kk.say("delegate ghost.tool as Worker: fetch the thing")
    line("  " + "\n  ".join(said))
    refused = sayhook.refused_outcomes(kk)
    ungranted = [r for r in refused
                 if r["status"] == "ungranted" and r["capability"] == "ghost.tool"]
    assert ungranted, f"an ungranted delegation must surface via refused_outcomes: {refused}"
    assert ungranted[0]["instruction_eligible"] is False, ungranted[0]
    line(f"  refused_outcomes surfaces the ungranted step: {ungranted[0]}")

    # 2. GOVERNANCE_DENIED — a held capability whose OBJECTIVE Decima's own
    #    recorded governance bans, refused AT DELEGATE-TIME (LOOP1, kernel.py
    #    `_delegate` ~616-631) rather than spawning a worker. Driven directly
    #    through `_delegate` (the 114_live_governance.py idiom) rather than a
    #    second `say`: RuleBrain.decide() runs its OWN, EARLIER orientation gate
    #    over the raw utterance (agent.py `_orient`/`_oriented_block`) and would
    #    intercept a banned objective as a flat "respond" denial before any
    #    delegate Action is even built — a different, upstream refusal surface,
    #    not the `_delegate` task-cell refusal this lane surfaces. Going straight
    #    to `_delegate` isolates exactly the refusal `sayhook` is built to show.
    kk.integrate_tool("deploy", lambda impl, args: {"out": "deployed"})
    ev_id = content_id({"policy_src": "sayhook-freeze"})
    _model.assert_content(kk.weft, kk.decima_agent_id, ev_id, "note",
                          {"text": "release freeze: a prod outage last week"})
    memory.remember_governance(kk.weft, kk.decima_agent_id, memory.BANNED_ACTION,
                               target="deploy to prod", reason="under a change freeze",
                               evidence_src=ev_id)
    decima_cell = kk.weave().get(kk.decima_agent_id)
    gov_action = types.SimpleNamespace(tasks=[{
        "subagent": "Worker", "objective": "deploy to prod now",
        "capability": "deploy", "budget": 5}])
    gov_lines, _ = kk._delegate(decima_cell, gov_action, depth=1, label="decima",
                               parent_task=None)
    line("  " + "\n  ".join(gov_lines))
    refused2 = sayhook.refused_outcomes(kk)
    gov = [r for r in refused2 if r["status"] == "governance_denied"]
    assert gov, f"a governance-denied delegation must surface via refused_outcomes: {refused2}"
    assert gov[0]["governance"] and gov[0]["evidence"], gov[0]
    assert gov[0]["instruction_eligible"] is False, gov[0]
    line(f"  refused_outcomes surfaces the governance-denied step: {gov[0]}")

    # ordering: newest-relevant first by LAMPORT (int, not wall-clock).
    lamports = [r["lamport"] for r in refused2]
    assert lamports and all(isinstance(n, int) for n in lamports), refused2
    assert lamports == sorted(lamports, reverse=True), \
        f"refused_outcomes must order newest-first by lamport: {lamports}"
    line(f"  ordering is newest-first by lamport (int, no wall-clock): {lamports}")

    # 3. ACTIVATION SUGGESTION — a discover()-driven "use" submits a durable
    #    catalog.activate:<name> ApprovalInbox item (discovery.submit_activation).
    M.register(kk, M.capability_manifest(
        "send_email", title="send an email message",
        description="send an email message to a recipient", archetype="EFFECT",
        effect_class="COMMUNICATION", tags=["email", "message", "notify"]))
    sug = D.discover(kk, "send an email to the customer", threshold=1)
    assert sug["action"] == "use" and sug.get("activation"), sug
    assert sug["activation"]["status"] == "pending", sug["activation"]

    suggestions = sayhook.activation_suggestions(kk)
    hit = [s for s in suggestions if s["name"] == "send_email"]
    assert hit, f"a pending catalog.activate item must surface: {suggestions}"
    assert hit[0]["manifest"] == sug["manifest"], hit[0]
    assert hit[0]["instruction_eligible"] is False, hit[0]
    line(f"  activation_suggestions surfaces the pending activation, with manifest "
         f"provenance: {hit[0]}")

    # 4. surface() = the combined read; MINTS/RECORDS NOTHING (Weft unchanged).
    before = kk.weft.count()
    combined = sayhook.surface(kk)
    after = kk.weft.count()
    assert after == before, \
        f"surface() must record NOTHING on the Weft (count {before} -> {after})"
    assert combined["refused"] == sayhook.refused_outcomes(kk), combined
    assert combined["suggestions"] == sayhook.activation_suggestions(kk), combined
    assert len(combined["refused"]) >= 2 and len(combined["suggestions"]) >= 1, combined
    line(f"  surface() = {{refused: {len(combined['refused'])}, "
         f"suggestions: {len(combined['suggestions'])}}} — Weft event count "
         f"unchanged ({before} -> {after}), a pure read ✓")

    # the ApprovalInbox item is STILL pending — surfacing it decided nothing.
    still_pending = [it.id for it in ApprovalInbox(kk).pending()]
    assert sug["activation"]["item"] in still_pending, \
        "surfacing an activation suggestion must not decide it"
    line("  the suggested activation is still PENDING after surfacing — a human "
         "still approves/denies it via the ordinary ApprovalInbox spine ✓")

    # limit= is honored on both projections.
    assert len(sayhook.refused_outcomes(kk, limit=1)) == 1
    assert len(sayhook.activation_suggestions(kk, limit=0)) == 0
    line("  limit= honored on both projections ✓")

    line("  → a human can now SEE what the last turn(s) wanted and could not do, and "
         "what the catalog suggests activating instead — a pure read; nothing here "
         "mints an approval or installs a capability.")
