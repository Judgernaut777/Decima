"""The canary monitor — measuring a live organ, and acting on what it measures (wave N5).

`Weave.canary_health` has been in the shipping kernel with ZERO callers and ZERO tests since
it was extracted. This module is its first caller, and the first thing to say is what that
exercise turned up, because it is load-bearing for how much the canary is worth:

  * **The `result` shape it folds had no producer.** It looks for a Cell of type `result`
    whose `of` is one of the capability's INVOKE events. Nothing in `decima/` ever wrote one
    — the runtime's `cells.record_receipt` writes type `receipt`, keyed on step/lease/attempt,
    with no `of` field at all, so it could never satisfy the fold. Wave N5's invoke seam
    (`decima/kernel/invoke.py`) is the producer that was missing.
  * **The `ok is False` half was unreachable.** That key came from the reference executor's
    ad-hoc result dict; `decima.workers.protocol.WorkerResponse` has no `ok` field. The
    executor now computes `ok` explicitly against the candidate's declared output contract —
    see `executor.conforms` — so the canary catches a wrongly-shaped answer, not only a crash.
    It still does not catch a wrong answer of the right type. That is the Reckoner's
    differential stage, upstream, and pretending otherwise would be the dangerous lie.
  * **The high-finding half had no producer either.** Nothing in `decima/` had ever written a
    `finding` Cell or a `found_in` edge, so `high_findings` was structurally always 0 and any
    test asserting `== 0` would have passed vacuously. `record_finding` below is that writer.

TWO SIGNALS, TWO DIFFERENT ACTIONS — and this is the design decision this module exists to
make explicit. The reference monitor calls `revoke` for both, and porting that verbatim would
have destroyed exactly the capability wave N4 was built to create.

  * **A canary threshold breach → `promotion.rollback` (DEMOTION).** A breach is a statement
    about what the organ DID: n failures in the window. That is evidence, and evidence can be
    revised — a fixed candidate, a re-run evaluation, a new promotion. Rollback RETRACTs the
    promotion Cell; quarantine is a derived flag, so the organ re-quarantines on the next fold
    while the capability, every grant attenuated from it, and the whole history survive. It is
    reversible and re-promotable, which is what "suspend pending re-evaluation" has to mean.

  * **A HIGH security finding → `lifecycle.revoke` (TERMINAL).** A high finding is a statement
    about what the organ CAN do — reach it never declared, dynamic execution, a containment
    escape. No amount of further evidence makes that untrue, and everything attenuated from
    the grant inherits the same reach. So it takes the DERIVED_AUTHORITY cascade: the
    capability is retracted, every descendant grant fails closed, and there is no un-revoke.
    The asymmetry is the point. A breach means "stop trusting this until it proves itself
    again"; a finding means "this must never run again."

WHO IS ALLOWED TO BE BELIEVED. The terminal action above is taken on EVIDENCE, so the
evidence has to be attributable — and `record_finding` writes whatever author it is handed,
while `Weave.canary_health` counts any `finding` Cell edged `found_in` at the capability no
matter who asserted it. Promotion's evidence is gated (`weave._is_trusted_promoter`, anchored
on the unforgeable genesis author; `promotion.promote` refuses an untrusted signer outright)
and the strictly more destructive, strictly irreversible action was gated by nothing: any
key-holding principal — including a sandboxed candidate under evaluation — could plant one
`finding` Cell and have the trusted monitor execute a permanent, cascading denial of the whole
grant subtree on its behalf. So `high_findings_by_auditors` re-derives that half of the health
fold and counts a high finding ONLY when an ANCHORED principal asserted it: a root-declared
promoter for the capability's tier, or the constitutional root itself. Everything else is
still recorded, still visible, and reported as `unattributed_high_findings` — evidence a
human can read, never a trigger.

That re-derivation reads the ASSERT EVENTS rather than the folded Cell, which also closes the
mirror hole: finding Cell ids are content-addressed over (capability, rule, severity), so an
attacker could otherwise re-ASSERT an auditor's cell with `severity: "low"` in its content and
LWW a real finding away. The auditor's own event still says `high`, and that is what counts.

AND THE SAME GOES FOR THE WITHDRAWAL, which is the half this module got wrong until now. A
`finding` is not one of `authorship.GUARDED_TYPES`, so nothing judges who may RETRACT one and
the fold honours a retraction from any key-holder — while this function used to read exactly
that folded `retracted` flag. One unauthenticated event from a principal with no anchor, no
relationship to the finding and no root key therefore turned `healthy: False` into
`healthy: True` and made `monitor_canary` decline to revoke a compromised organ. Gating who
may PLANT evidence is worth nothing if anyone may SHRED it — suppressing the containment path
is precisely what an escaped organ wants — so a withdrawal is now honoured only from a
principal who could have signed the finding itself: an anchored auditor for the tier, or root.
The rule is one predicate (`is_anchored_auditor`) asked of the asserter and of the retractor,
so the two halves cannot drift apart. Withdrawal remains the correction path a mistaken but
anchored finding needs; it is no longer a stranger's veto over containment.

AND THE VERB WAS NOT THE THING. Gating the RETRACT left the identical suppression reachable by
an ordinary ASSERT, because this function still confirmed a finding's SHAPE against the folded
Cell — and `Weave._apply` lets any content assertion overwrite `cell.type`. One unanchored
ASSERT re-typing an auditor's `finding` as a `note` disarmed the auto-revoke exactly as the
stranger's RETRACT had, and disarmed the kernel's own count with it, so the health report did
not even show it as unattributed evidence. The fix is not a fourth mode to judge: it is to stop
re-reading the Cell at all. Type comes from the asserting event's body, the `found_in` edge from
an anchored auditor's own EDGE event, liveness from the judged RETRACTs — the whole verdict is
now folded from events that somebody accountable signed, and there is no forgeable read left in
the loop for a fifth variant to reach. See `high_findings_by_auditors`.

`rollback` targets the PROMOTION Cell and `revoke` targets the CAPABILITY. They are not
interchangeable and the ids are not interchangeable either: handing a capability id to
`rollback` would RETRACT the capability with a `WITHDRAW` mode that the fold still defaults
to `DERIVED_AUTHORITY` for a capability — a silent cascade where a demotion was meant. So
this module resolves the live promotion Cells through `promotion.promotion_state` and never
guesses.

WHAT IS DELIBERATELY NOT PORTED. The reference also re-ASSERTs the capability's content with
`lifecycle: "SUSPENDED"`. Under N4's derived quarantine that write is at best redundant and
at worst reintroduces the exact race step 3 of `_cascade_retractions` was written to
eliminate: the fold recomputes `quarantined` and `sandbox_only` from promotion liveness on
every pass and would overwrite it anyway. The suspension is recorded as its own Cell, edged
to the capability, and the capability's own content is left to the fold.
"""

from __future__ import annotations

from typing import Any

from decima.kernel import lifecycle, model
from decima.kernel.hashing import content_id, nfc
from decima.kernel.weave import Weave
from decima.kernel.weft import ASSERT, RETRACT, Weft
from decima.services.nona import anchors, promotion
from decima.services.nona.reckoner import FINDING

INCIDENT = "incident"
SUSPENSION = "suspension"

REVOKED = "revoked"
SUSPENDED = "suspended"


def finding_cell_id(capability: str, rule: str, severity: str) -> str:
    """Content-addressed over (capability, rule, severity) so the same finding recorded twice
    is ONE finding — a monitor that re-runs must not inflate its own evidence."""
    return "finding:" + content_id(
        {"found_in": capability, "rule": nfc(rule), "severity": nfc(severity)}, kind="cell"
    )


def record_finding(
    weft: Weft,
    author: str,
    capability: str,
    *,
    severity: str,
    rule: str,
    detail: str = "",
) -> str:
    """Record a security finding AS A CELL, edged `found_in → capability`.

    The Reckoner records findings as a plain list inside `evaluation_result.security_findings`
    — fine as evidence about a candidate, but invisible to `canary_health`, which folds
    `finding` Cells and `found_in` edges. This is the writer that makes the auto-revoke path
    reachable at all; before it, `high_findings` could only ever be 0.

    Recording a finding does not act on it, and recording is deliberately OPEN: a scanner, a
    reviewer or an agent may all report what they saw. `monitor_canary` decides, and it moves
    the organ only on a `high` finding an ANCHORED auditor asserted (see
    `high_findings_by_auditors`) — because the action is terminal and cascading, and evidence
    nobody accountable signed is not grounds for it.
    """
    cell = finding_cell_id(capability, rule, severity)
    model.assert_content(
        weft,
        author,
        cell,
        FINDING,
        {
            "severity": nfc(severity),
            "rule": nfc(rule),
            "detail": nfc(detail),
            "capability": capability,
        },
    )
    model.assert_edge(weft, author, cell, "found_in", capability)
    return cell


def is_anchored_auditor(weave: Weave, principal: str, tier: str | None) -> bool:
    """May `principal`'s security finding MOVE a `tier` organ?

    The same trust root promotion uses, and no second mechanism: a LIVE `promoter` anchor the
    CONSTITUTIONAL ROOT asserted (`anchors.trusted_promoters` folds exactly those, filtering
    self-declared ones through `weave._is_trusted_promoter`), naming this principal for this
    tier — or the root itself, which is the principal that anchors everyone and can therefore
    already do this by anchoring itself.

    A capability that declares NO tier requires an anchor for SOME tier, not for none. The
    fold's own `_is_trusted_promoter(p, None)` returns True for ANY principal (the pre-cycle
    back-compat path), which is tolerable for lifting a quarantine on a legacy cap and is not
    tolerable for a terminal revocation, so this fails closed instead.
    """
    if principal and principal == weave._genesis_author:
        return True
    honoured = anchors.trusted_promoters(weave).get(principal)
    if not honoured:
        return False
    return tier is None or tier in honoured


def high_findings_by_auditors(weft: Weft, weave: Weave, capability: str) -> list[str]:
    """The high-severity findings against `capability` that an ANCHORED auditor asserted
    and no anchored auditor has since withdrawn.

    Deliberately folded from the ASSERT EVENTS, not from the folded Cells: the event carries
    its AUTHOR (which is what is being judged) and the severity that author actually wrote
    (which a later last-writer-wins ASSERT of the same content-addressed cell cannot revise).

    WITHDRAWAL IS READ FROM THE EVENTS FOR THE SAME REASON, and that is not a symmetry for its
    own sake — it closes a hole that made every gate above it decorative. `finding` is not one
    of `authorship.GUARDED_TYPES`, so `authorship.retract_refusal` does not judge a RETRACT of
    one and the fold applies it from ANY key-holder. Reading `cell.retracted` therefore meant a
    principal with no anchor, no relationship to the finding and no root key could withdraw an
    anchored auditor's HIGH finding with ONE unauthenticated event, `attributed_health` would
    report `healthy: True`, and `monitor_canary` would decline to revoke a demonstrably
    compromised organ. Gating who may PLANT evidence buys nothing if anyone may SHRED it: the
    terminal containment path is exactly what an attacker who has escaped containment wants
    suppressed. So a withdrawal counts only from a principal who could have signed the finding
    in the first place — an anchored auditor for the organ's tier, or the realm root — which is
    the same predicate, evaluated against the retractor. `is_anchored_auditor` is memoized per
    call because a log carries far more RETRACTs than findings and each answer folds the
    anchors.

    THE SHAPE IS READ FROM THE EVENTS FOR THE SAME REASON — and that is the third suppression
    this one function has had to close, not a tidiness pass. A finding must satisfy the shape
    `Weave.canary_health` folds: a `finding` Cell edged `found_in → capability`. This loop used
    to confirm that shape against the FOLDED Cell (`cell.type != FINDING`, then a `found_in`
    edge on `cell.edges_out`) even though the asserting event's own body had already been
    screened for `type == FINDING` five lines earlier. The re-read added no evidence and one
    attack: `Weave._apply` upserts `cell.type = body["type"]` on EVERY content assertion, and
    `finding` is not one of `authorship.GUARDED_TYPES`, so a principal with no anchor, no
    relationship to the finding and no root key could ASSERT the auditor's content-addressed
    cell as type `note` and this loop would drop the finding on the type line. Attributed count
    back to 0, `healthy: True`, `monitor_canary` declining to revoke a compromised organ — and
    because the kernel's `high_findings` is folded from the same overwritten Cell it fell to 0
    too, so the clamp below reported `unattributed_high_findings: 0` and the suppression was
    invisible in the health report as well. Bit for bit the outcome the withdrawal rule above
    was written to prevent, reached by an ordinary ASSERT instead of a RETRACT: gating the verb
    is worth nothing while the same effect is one VERB away. So nothing is taken off the folded
    Cell here any more. The type comes from the asserting event's own body, and the edge from an
    EDGE event whose author satisfies the same `anchored` predicate — an edge a stranger drew is
    not what makes an auditor's finding count, and `record_finding` writes both halves under the
    one author, so the honest path is unchanged.

    What this therefore no longer inherits from the kernel's fold is the finding's LIVENESS or
    its current shape, so the attributed count may legitimately EXCEED the kernel's: a real
    finding whose content was overwritten with a lower severity — or whose type was overwritten
    at all — is still counted here and is no longer counted there. `attributed_health` clamps
    `unattributed_high_findings` at zero for exactly that reason.

    Deterministic (Law 5): no clock, no randomness, no arrival-order dependence — `found`,
    `edged` and `withdrawn` are each accumulated over the whole event sequence before they are
    intersected and subtracted, so an EDGE that folds before the ASSERT it belongs to, or a
    RETRACT that folds before the ASSERT it withdraws, is honoured identically. Returns the Cell
    ids, sorted, so the caller's output is stable.
    """
    cap = weave.get(capability)
    # Ask the fold for the tier rather than re-deriving the rule here, so the auditor set can
    # never disagree with the promoter set the same tier selects.
    tier = weave._candidate_tier(cap) if cap is not None else None
    judged: dict[str, bool] = {}

    def anchored(principal: str) -> bool:
        verdict = judged.get(principal)
        if verdict is None:
            verdict = is_anchored_auditor(weave, principal, tier)
            judged[principal] = verdict
        return verdict

    found: set[str] = set()
    edged: set[str] = set()
    withdrawn: set[str] = set()
    for ev in weft.events():
        body = ev.body if isinstance(ev.body, dict) else {}
        if ev.verb == RETRACT:
            target = body.get("cell")
            # Every retraction MODE is a withdrawal here — WITHDRAW, REDACT and TERMINATE all
            # take the evidence out of the fold, so judging only the default would leave the
            # same suppression one keyword away.
            if isinstance(target, str) and anchored(ev.author):
                withdrawn.add(target)
            continue
        if ev.verb != ASSERT:
            continue
        kind = body.get("kind", "CONTENT")
        if kind == "EDGE":
            # The `found_in → capability` half of the shape, read from the auditor's OWN edge
            # event for exactly the reason the content half is. Judged by the same predicate,
            # so an edge a stranger drew is not what makes an auditor's finding count.
            src = body.get("src")
            if (
                body.get("rel") == "found_in"
                and body.get("dst") == capability
                and isinstance(src, str)
                and anchored(ev.author)
            ):
                edged.add(src)
            continue
        if kind != "CONTENT" or body.get("type") != FINDING:
            continue
        content = body.get("content")
        cell_id = body.get("cell")
        if not isinstance(content, dict) or not isinstance(cell_id, str):
            continue  # a sealed or malformed assertion proves nothing (fail closed)
        if str(content.get("severity", "")).lower() != "high":
            continue
        if content.get("capability") != capability:
            continue
        if not anchored(ev.author):
            continue
        found.add(cell_id)
    return sorted((found & edged) - withdrawn)


def attributed_health(
    weft: Weft, weave: Weave, capability: str, *, max_failures: int = 0
) -> dict[str, Any]:
    """`Weave.canary_health` with its high-finding half re-derived from ATTRIBUTED evidence.

    `high_findings` becomes the count an anchored auditor stands behind; the remainder is
    reported as `unattributed_high_findings` so planted evidence is visible rather than
    silently dropped, and `healthy` follows the attributed count.

    The FAILURE half is left exactly as the kernel folds it. Receipts are written by the invoke
    seam, and a forged one is still possible: N7 closed R1 for the four cells AUTHORITY is read
    from (`decima.kernel.authorship.GUARDED_TYPES` — capability, agent, promoter, promotion), and
    `result` is deliberately not among them, so any key-holder may still assert a receipt. That
    is tolerable here for the reason it always was: the action a breach takes is DEMOTION, which
    is reversible and re-promotable. Attribution is gated on the FINDING path instead, because
    that is the one that is terminal, cascading and irreversible.
    """
    health = weave.canary_health(capability, max_failures=max_failures)
    attributed = high_findings_by_auditors(weft, weave, capability)
    counted = int(health["high_findings"])
    return {
        **health,
        "high_findings": len(attributed),
        # Clamped: the attributed count can EXCEED the kernel's when a real finding's content
        # was overwritten with a lower severity, or its TYPE overwritten so the kernel's fold
        # stops seeing a `finding` at all (the auditor's own event still says high).
        "unattributed_high_findings": max(counted - len(attributed), 0),
        "healthy": not health["breach"] and not attributed,
    }


def _incident(weft: Weft, author: str, capability: str, reason: str, health: dict[str, Any]) -> str:
    """The incident record for an auto-revoke. Content-addressed over the health it was
    derived from, so re-running the monitor on unchanged health re-asserts the SAME cell
    rather than accreting duplicates."""
    cell = "incident:" + content_id(
        {"incident": capability, "reason": nfc(reason), "health": health}, kind="cell"
    )
    model.assert_content(
        weft,
        author,
        cell,
        INCIDENT,
        {
            "capability": capability,
            "reason": nfc(reason),
            "health": dict(health),
            "from_state": "PROMOTED",
            "to_state": "REVOKED",
            # Containment and compensation only: revoking a grant stops future invocations
            # and never claims to undo an effect that already left the machine.
            "note": "contains and compensates; never claims to undo an external effect",
        },
    )
    model.assert_edge(weft, author, cell, "incident_for", capability)
    return cell


def _suspension(
    weft: Weft, author: str, capability: str, reason: str, health: dict[str, Any]
) -> str:
    """The suspension record for a demotion — the counterpart of `_incident`, and
    deliberately a DIFFERENT Cell type, because "returned to quarantine" and "revoked
    forever" must not be answerable only by reading a `reason` string."""
    cell = "suspension:" + content_id(
        {"suspension": capability, "reason": nfc(reason), "health": health}, kind="cell"
    )
    model.assert_content(
        weft,
        author,
        cell,
        SUSPENSION,
        {
            "capability": capability,
            "reason": nfc(reason),
            "health": dict(health),
            "from_state": "PROMOTED",
            "to_state": "QUARANTINED",
        },
    )
    model.assert_edge(weft, author, cell, "suspends", capability)
    return cell


def monitor_canary(
    weft: Weft,
    weave: Weave,
    author: str,
    capability: str,
    *,
    max_failures: int = 0,
) -> dict[str, Any]:
    """Fold a promoted organ's health and act on it — the only place either action is taken.

    Returns `{"health", "action", ...}` where `action` is `None` (healthy), `"suspended"` (a
    threshold breach, demoted via `promotion.rollback`) or `"revoked"` (a HIGH finding from an
    ANCHORED auditor, terminated via `lifecycle.revoke`). A high finding wins over a breach:
    it is the stronger claim and it subsumes the weaker action.

    `health` is `attributed_health` — the kernel's fold with its high-finding half restricted
    to evidence an anchored auditor signed. A high finding from anyone else appears as
    `unattributed_high_findings` and moves nothing: the action is terminal and cascading, and
    it must not be reachable by any principal that happens to hold a key.

    The folds themselves are pure — they only MEASURE. Every write happens here, on the log,
    signed by `author`, so "why did this organ stop?" is answerable from the events forever.
    """
    health = attributed_health(weft, weave, capability, max_failures=max_failures)
    out: dict[str, Any] = {"health": health, "action": None, "capability": capability}

    if health["high_findings"]:
        reason = (
            f"canary auto-revoke: {health['high_findings']} high-severity security "
            "finding(s) from an anchored auditor — a statement about what this organ CAN "
            "do, which no further evidence can retract"
        )
        lifecycle.revoke(weft, author, capability)
        out["incident"] = _incident(weft, author, capability, reason, health)
        out["action"] = REVOKED
        out["reason"] = reason
        return out

    if health["breach"]:
        reason = (
            f"canary threshold breach: {health['failures']} failure(s) over a limit of "
            f"{int(max_failures)} — demotion, not revocation, because a breach is evidence "
            "and evidence can be revised"
        )
        # Roll back every LIVE promotion naming this capability. Quarantine is derived from
        # promotion liveness, so leaving one live would leave the organ runnable.
        state = promotion.promotion_state(weave, capability)
        rolled: list[str] = []
        for live in state["live_promotions"]:
            cell = str(live["cell"])
            promotion.rollback(weft, author, cell, reason=reason)
            rolled.append(cell)
        out["suspension"] = _suspension(weft, author, capability, reason, health)
        out["rolled_back"] = rolled
        out["action"] = SUSPENDED
        out["reason"] = reason
        return out

    return out


def promoted_organs(weave: Weave) -> list[str]:
    """Every capability with a LIVE promotion, in deterministic id order.

    "Promoted" is read from promotion liveness rather than from a flag on the capability,
    because that is what quarantine itself is derived from (N4): a capability whose
    promotions have all been retracted is already back in quarantine and there is nothing
    for the monitor to act on. Reading the same facts the kernel gates on is what keeps the
    sweep from ever disagreeing with enforcement.
    """
    live: set[str] = set()
    for cell in weave.cells.values():
        if cell.type != promotion.PROMOTION or cell.retracted:
            continue
        target = cell.content.get("capability")
        if isinstance(target, str):
            live.add(target)
    return sorted(live)


def sweep(weft: Weft, weave: Weave, *, max_failures: int = 0) -> dict[str, Any]:
    """Fold every promoted organ's health and act on the ones that breached — the pass that
    makes the canary product behaviour instead of a tested library.

    THIS IS THE MISSING HALF OF WAVE N5, and worth naming as such. N5 built
    `monitor_canary`, tested it, and wired it to nothing: the module had zero production
    callers, no route exposed organ health, and the Shell rendered none. Suspend-on-breach
    and auto-revoke-on-high-finding therefore existed and could not fire. Dead safety code
    is the specific trap the design names for `canary_health` itself, and shipping the fix
    for one half while re-creating it in the other would have been the same mistake twice.

    WHO SIGNS EACH ACTION. Not one sweep-wide author: since RETRACT is authorized
    (`kernel/authorship.py::retract_refusal`), a retraction from a principal that is neither
    the promotion's signer nor a root-anchored promoter for its tier is RECORDED AND THEN
    DECLINED BY THE FOLD — the automation would appear to run and change nothing, which is
    worse than not running. Each organ's action is therefore signed by ITS OWN promotion's
    signer, which is by construction a principal the fold will honour, and an organ whose
    signer cannot be determined is REPORTED rather than acted on.

    Deterministic and idempotent: organs are visited in id order; the health it acts on is a
    pure fold; the incident and suspension Cells are content-addressed over that health, so
    re-running on unchanged evidence re-asserts identical Cells rather than accreting
    duplicates. A demoted organ has no live promotion, so the next sweep skips it — the
    sweep is safe to run on any schedule, including twice.

    Returns `{"checked", "actions", "unattributed", "unsigned"}` — `actions` names the organs
    that moved and what happened to each, so a caller can surface exactly what changed.
    """
    checked: list[str] = []
    actions: list[dict[str, Any]] = []
    unattributed: list[dict[str, Any]] = []
    unsigned: list[str] = []

    for cap_id in promoted_organs(weave):
        signer = _acting_signer(weave, cap_id)
        if signer is None:
            unsigned.append(cap_id)
            continue
        checked.append(cap_id)
        outcome = monitor_canary(weft, weave, signer, cap_id, max_failures=max_failures)
        health = outcome["health"]
        if int(health.get("unattributed_high_findings", 0)):
            # Visible, never a trigger: a planted finding is evidence a human should read.
            unattributed.append(
                {"capability": cap_id, "count": int(health["unattributed_high_findings"])}
            )
        if outcome["action"] is not None:
            actions.append(
                {
                    "capability": cap_id,
                    "action": outcome["action"],
                    "reason": outcome.get("reason", ""),
                    "signer": signer,
                }
            )
    return {
        "checked": checked,
        "actions": actions,
        "unattributed": unattributed,
        "unsigned": unsigned,
    }


def _acting_signer(weave: Weave, capability: str) -> str | None:
    """The principal that may act on this organ: its live promotion's own `signer`.

    Deterministic when an organ carries more than one live promotion (the smallest signer id
    wins), and None when none names a signer — in which case the sweep reports the organ
    instead of writing a retraction the fold would decline.
    """
    signers = sorted(
        {
            str(c.content.get("signer"))
            for c in weave.cells.values()
            if c.type == promotion.PROMOTION
            and not c.retracted
            and c.content.get("capability") == capability
            and isinstance(c.content.get("signer"), str)
        }
    )
    return signers[0] if signers else None


def organ_health(weft: Weft, weave: Weave, *, max_failures: int = 0) -> list[dict[str, Any]]:
    """A pure read of every promoted organ's health — what the Shell shows, derived from the
    same folded facts the sweep acts on and the kernel gates on, so the panel cannot claim an
    organ is healthy while enforcement disagrees. Writes nothing."""
    out: list[dict[str, Any]] = []
    for cap_id in promoted_organs(weave):
        health = attributed_health(weft, weave, cap_id, max_failures=max_failures)
        cell = weave.get(cap_id)
        out.append(
            {
                "capability": cap_id,
                "name": str((cell.content.get("name") if cell else "") or ""),
                "tier": str((cell.content.get("declared_effect_class") if cell else "") or ""),
                "signer": _acting_signer(weave, cap_id),
                **health,
            }
        )
    return out
