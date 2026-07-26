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
from decima.kernel.weft import ASSERT, Weft
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
    """The high-severity findings against `capability` that an ANCHORED auditor asserted.

    Deliberately folded from the ASSERT EVENTS, not from the folded Cells: the event carries
    its AUTHOR (which is what is being judged) and the severity that author actually wrote
    (which a later last-writer-wins ASSERT of the same content-addressed cell cannot revise).
    A finding still has to satisfy the shape `Weave.canary_health` folds — a live `finding`
    Cell edged `found_in → capability` — so this is always a SUBSET of the kernel's count,
    never a second, laxer source of findings.

    Returns the Cell ids, sorted, so the caller's output is deterministic.
    """
    cap = weave.get(capability)
    # Ask the fold for the tier rather than re-deriving the rule here, so the auditor set can
    # never disagree with the promoter set the same tier selects.
    tier = weave._candidate_tier(cap) if cap is not None else None
    found: set[str] = set()
    for ev in weft.events():
        if ev.verb != ASSERT:
            continue
        body = ev.body if isinstance(ev.body, dict) else {}
        if body.get("kind", "CONTENT") != "CONTENT" or body.get("type") != FINDING:
            continue
        content = body.get("content")
        cell_id = body.get("cell")
        if not isinstance(content, dict) or not isinstance(cell_id, str):
            continue  # a sealed or malformed assertion proves nothing (fail closed)
        if str(content.get("severity", "")).lower() != "high":
            continue
        if content.get("capability") != capability:
            continue
        if not is_anchored_auditor(weave, ev.author, tier):
            continue
        cell = weave.get(cell_id)
        if cell is None or cell.retracted or cell.type != FINDING:
            continue  # withdrawn evidence is not evidence
        if any(e["rel"] == "found_in" and e["dst"] == capability for e in cell.edges_out):
            found.add(cell.id)
    return sorted(found)


def attributed_health(
    weft: Weft, weave: Weave, capability: str, *, max_failures: int = 0
) -> dict[str, Any]:
    """`Weave.canary_health` with its high-finding half re-derived from ATTRIBUTED evidence.

    `high_findings` becomes the count an anchored auditor stands behind; the remainder is
    reported as `unattributed_high_findings` so planted evidence is visible rather than
    silently dropped, and `healthy` follows the attributed count.

    The FAILURE half is left exactly as the kernel folds it. Receipts are written by the invoke
    seam, and while an unauthorized ASSERT could still forge one (R1 — the ASSERT path is not
    authorized at the Weft door; that is N7), the action a breach takes is DEMOTION, which is
    reversible and re-promotable. Attribution is gated here because the finding path is the one
    that is terminal, cascading and irreversible.
    """
    health = weave.canary_health(capability, max_failures=max_failures)
    attributed = high_findings_by_auditors(weft, weave, capability)
    counted = int(health["high_findings"])
    return {
        **health,
        "high_findings": len(attributed),
        # Clamped: the attributed count can EXCEED the kernel's when a real finding's content
        # was overwritten with a lower severity (the event still says high).
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
