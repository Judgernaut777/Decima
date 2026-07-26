"""Promotion and rollback (Nona wave N4).

This is where a candidate becomes an organ of the system — and where it can be taken back.

PROMOTION IS A SIGNATURE; ROLLBACK IS A RETRACTION. `VISION.md` has always claimed exactly
that, and until now it was not implementable: an `ATTEST` is folded into a target's
attestations and has no cell id, so there was literally nothing to retract, and the lift
mutated the capability's content (which an ordinary later `ASSERT` could silently undo).
Wave N4 introduces the **`promotion` Cell** and makes quarantine a DERIVED flag in the fold
(`Weave._cascade_retractions` step 3, owner Decision 3), so:

  * `promote()` asserts a promotion Cell and attests it — the capability becomes live;
  * `rollback()` RETRACTs that Cell — the capability re-quarantines on the next fold, with
    no second mechanism and no special path;
  * the flag is order-independent, so merge/sync can neither silently promote nor silently
    re-quarantine;
  * demotion is expressible WITHOUT revoking the organ (rollback ≠ revoke).

THE PROMOTION CELL CITES ITS EVIDENCE. A promotion names the candidate, the suite, the
evaluation result, the tier and the signer. That makes "why is this capability live?" a
graph walk rather than an act of faith — and it is what lets `promote()` refuse to sign a
result that the gate did not mark eligible. Evidence is not decoration: it is checked.

THE TIER LADDER DECIDES WHO MAY SIGN (owner Decision 1). `pure` and `read_only` may be
signed by the Reckoner automatically — that is the decision that makes this a compounding
engine rather than a tool the operator drives. Everything above needs a human or Morta, and
`network` has NO EXECUTOR AT ALL (no mediated egress), so it can be authored and evaluated
but never promoted to something runnable. The UI says NOT EXECUTABLE rather than "requires
approval", because prompting for something that cannot run teaches people to click yes.

MORTA SURVIVES BOTH DIRECTIONS. Promotion strips `sandbox_only` and nothing else; demotion
restores it and nothing else. A `requires_approval` floor is untouched by either, so an
unstrippable gate stays unstrippable across the whole lifecycle.
"""

from __future__ import annotations

from typing import Any

from decima.kernel import model
from decima.kernel.hashing import content_id
from decima.kernel.weave import Weave
from decima.kernel.weft import ATTEST, RETRACT, Weft
from decima.services.nona import anchors

PROMOTION = "promotion"

# Tier → how a promotion of that tier may be signed (design §5.5, Decision 1).
AUTOMATED = "automated"  # the Reckoner may sign; no human in the cycle
HUMAN = "human"  # a human attestation is required
NOT_EXECUTABLE = "not_executable"  # no executor exists; never promotable to runnable

SIGNER_POLICY: dict[str, str] = {
    "pure": AUTOMATED,
    "read_only": AUTOMATED,
    "workspace_write": HUMAN,  # canary + rollback target land in N5
    "network": NOT_EXECUTABLE,
    "financial": HUMAN,
}


class PromotionRefused(RuntimeError):
    """A promotion was refused. Always because the EVIDENCE or the AUTHORITY was missing —
    never because of a transient condition — so a refusal is a statement about the
    candidate, not about the moment."""


def promotion_cell_id(capability: str, evaluation: str) -> str:
    """Content-addressed over (capability, evaluation): promoting the same capability on the
    same evidence twice is ONE promotion, not two records to reconcile."""
    return (
        f"promotion:{content_id({'capability': capability, 'evaluation': evaluation}, kind='cell')}"
    )


def promote(
    weft: Weft,
    weave: Weave,
    signer: str,
    *,
    capability: str,
    candidate: str,
    evaluation: str,
    tier: str,
) -> dict[str, Any]:
    """Promote `capability` by asserting a promotion Cell and attesting it.

    Refuses — before writing anything — unless every one of these holds:

      * the tier HAS an executor (a `network` organ cannot be promoted to runnable);
      * the signer is a trusted promoter for that tier (root-declared anchor, see
        `anchors`), so authority is data on the log rather than a role in code;
      * the cited `evaluation_result` exists AND is `promote_eligible`. This is the check
        that makes the gate mean something: a promotion cannot cite a failing evaluation, so
        "the tests gate the code" holds at the promotion boundary too, not just inside the
        Reckoner.
    """
    policy = SIGNER_POLICY.get(tier)
    if policy is None:
        raise PromotionRefused(f"unknown tier {tier!r}")
    if policy == NOT_EXECUTABLE:
        raise PromotionRefused(
            f"tier {tier!r} has no executor (no mediated egress is wired), so it cannot be "
            "promoted to a runnable organ — it may be authored and evaluated only"
        )
    if not weave._is_trusted_promoter(signer, tier):
        raise PromotionRefused(
            f"{signer} is not a trusted promoter for tier {tier!r}: promotion authority is "
            "a root-declared anchor on the log, not a role in code"
        )
    result = weave.get(evaluation)
    if result is None:
        raise PromotionRefused(f"no such evaluation_result {evaluation!r}")
    if result.content.get("promote_eligible") is not True:
        raise PromotionRefused(
            f"evaluation {evaluation!r} is not promote-eligible "
            f"({result.content.get('verdict_reason', 'no reason recorded')}): a promotion "
            "may not cite a failing evaluation"
        )

    cell = promotion_cell_id(capability, evaluation)
    model.assert_content(
        weft,
        signer,
        cell,
        PROMOTION,
        {
            "capability": capability,
            "candidate": candidate,
            "evaluation_result": evaluation,
            "tier": tier,
            "signer": signer,
            "from_state": "QUARANTINED",
            "to_state": "PROMOTED",
        },
    )
    # The ATTEST records the promotion as evidence on the capability itself, so the
    # attestation trail reads correctly for anyone auditing the cap rather than the
    # promotion. The DERIVED flag (weave step 3) is what actually lifts quarantine.
    weft.append(signer, ATTEST, {"target_cell": capability, "promote": True})
    return {"promotion": cell, "capability": capability, "tier": tier, "signer": signer}


def rollback(weft: Weft, author: str, promotion: str, *, reason: str = "") -> dict[str, Any]:
    """Roll a promotion back by RETRACTing its Cell — the whole mechanism.

    The capability re-quarantines on the next fold because quarantine is derived from the
    promotion's liveness. Note what this is NOT: it is not a revoke. The organ, its grants
    and its history all survive; it simply returns to needing a sandbox. That is the
    difference between "this needs re-evaluation" and "this must never run again", and
    before N4 only the latter was expressible.

    `mode=WITHDRAW` deliberately, NOT a cascade: rolling back a promotion must not fail
    closed everything derived from the capability — demotion is not revocation.
    """
    weft.append(author, RETRACT, {"cell": promotion, "mode": "WITHDRAW", "reason": reason})
    return {"promotion": promotion, "rolled_back_by": author, "reason": reason}


def promotion_state(weave: Weave, capability: str) -> dict[str, Any]:
    """The auditable answer to "why is this capability live (or not)?".

    A pure read: which promotions name it, which are live, and what the derived flag is. This
    is the view the Shell shows and the operator trusts, and it is derived from the same
    folded facts the kernel gates on — so it cannot disagree with enforcement.
    """
    cap = weave.get(capability)
    promotions = [
        {
            "cell": cid,
            "signer": c.content.get("signer"),
            "tier": c.content.get("tier"),
            "evaluation_result": c.content.get("evaluation_result"),
            "live": not c.retracted,
        }
        for cid, c in weave.cells.items()
        if c.type == PROMOTION and c.content.get("capability") == capability
    ]
    return {
        "capability": capability,
        "quarantined": None if cap is None else bool(cap.content.get("quarantined")),
        "promotions": promotions,
        "live_promotions": [p for p in promotions if p["live"]],
    }


def signer_policy(tier: str) -> str:
    """How a promotion of `tier` may be signed — `AUTOMATED`, `HUMAN`, or `NOT_EXECUTABLE`.

    Exposed so the Shell can render the honest label (Decision 2) instead of inventing an
    approval prompt for a tier that has no executor.
    """
    return SIGNER_POLICY.get(tier, HUMAN)


def anchored_tiers() -> tuple[str, ...]:
    """The tiers a promoter may actually be anchored for (mirrors `anchors.SIGNABLE_TIERS`),
    so callers do not have to know that `network` is deliberately unanchorable."""
    return anchors.SIGNABLE_TIERS
