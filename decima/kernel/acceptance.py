"""Per-invocation authority RE-CHECK at the acceptance gate (WEFT §2 item 7).

`Weft.ingest` proves a foreign event's INTEGRITY and CAUSALITY: the id recomputes from
canonical bytes, the signature is authentic under the key valid at the event's point,
parents are canonically sorted and PRESENT (closed DAG), and the lamport is honest.
What it did NOT do was look at AUTHORITY — a peer could sign a perfectly well-formed
INVOKE naming a capability it was never granted (or one revoked before it acted) and the
union accepted it, because authority was judged only at the ORIGIN. This module closes
that gap: an ingested INVOKE that CLAIMS a capability must carry an AuthorizationProof
(WEFT §3) that still verifies against the local view AT THAT EVENT'S CAUSAL FRONTIER
before it may enter the log. Fail closed: a proof that does not verify is a terminal
refusal and NOTHING is inserted.

WHY THE FRONTIER, NEVER "CURRENT" STATE (WEFT §2 item 7). Authority is judged where the
act happened. Re-authorizing against mutable current state would REFUSE legitimate
history: a grant revoked later, a lease exhausted later, or — the sharpest case — a
single-use invocation approval CONSUMED by the origin immediately after the invoke (the
consuming RETRACT is by construction a DESCENDANT of the INVOKE) would retroactively
invalidate an event that was properly authorized when it was made. Judging at the
ancestor closure also keeps ingest DETERMINISTIC: the closure of an event is a property
of the DAG alone, never of the order a feed delivered events, so a peer handed the same
event set accepts the same subset and folds to the same state_root regardless of arrival
order (FOLD §11.2). A seq-PREFIX fold would NOT have that property — a concurrent revoke
that happened to land first would refuse a valid event, and after a merge no single
prefix even describes the closure.

WHAT IS RE-CHECKED — all of it through the ONE ocap spine (`capability.verify_proof`),
so this module adds no second authorization path:
  • the proof's holder IS the event's author (the principal whose key signed the event);
  • the invocation bind recomputes over (verb, body, nonce, parents) — the event's OWN
    parents, so a proof captured from another request or frontier does not match;
  • holder key-possession over that bind, verified rotation-aware at the event's point;
  • at the frontier: the capability exists, is a capability, is not retracted /
    quarantined / closed by a DERIVED_AUTHORITY cascade, and its lease is live; the
    holder holds it in an agent envelope; the grant names the holder as grantee; the
    delegation path is downhill and granter-held; caveats (approval, sandbox) hold; and
    `grant_event` / `delegation_path` match the frontier's grant chain.

WHAT IS NOT RE-DERIVED HERE. The cumulative `budget` SPEND is folded at the origin from
the grant's receipts (`invoke.spent_to_date`), but a receipt is written AFTER its INVOKE
and is therefore not in that INVOKE's ancestor closure, so the spend visible at this
frontier is a lower bound on the origin's. The re-check deliberately evaluates the budget
with spend 0: never STRICTER than the origin, so it cannot manufacture a false refusal
against history that was properly authorized. The FOLDED lease bounds (`expires_at`,
`max_uses`) and every other caveat are re-checked exactly. A WEFT §3 proof names a holder PRINCIPAL, not an agent Cell, so the envelope
check is existential over that holder's OWN live agent cells at the frontier; a grant
sitting in another principal's envelope can never satisfy it.

An INVOKE that claims NO capability (no `authorized`, no body `cap`, no `proof`) carries
no authority to re-check and is accepted exactly as before — it never authorized
anything, so the gate has nothing to judge (and this keeps the existing capability-less
INVOKE golden vectors ingesting unchanged).

DETERMINISM. Nothing here is written to the Log and no content is hashed: "now" is the
frontier's logical lamport (never wall-clock), `prior_uses` is a folded integer count,
and the only float in play is `verify_proof`'s pre-existing `spent` parameter, which
never enters signed content.
"""

from __future__ import annotations

from typing import Any, cast

from decima.kernel import authorship, capability
from decima.kernel.weave import Cell, Weave
from decima.kernel.weft import ASSERT, INVOKE, Weft

# The Cell type an agent is folded as (`decima/runtime/cells.py::AGENT`), restated here
# so the TCB never imports outward into the runtime.
AGENT = "agent"

# Terminal refusal codes. `Weft.ingest` returns them as `rejected:<code>`; the event is
# NEVER inserted (fail closed).
NO_PROOF = "missing-authorization-proof"
HOLDER_MISMATCH = "proof-holder-mismatch"
UNAUTHORIZED = "unauthorized-invoke"

# Body fields the invoke seam adds AFTER the proof was bound (`{**body, "nonce": nonce,
# "proof": proof}`), so the bound body is the event body minus exactly these two.
# Anything else a peer put in the body was part of the bind and must still hash to it.
_POST_BIND_FIELDS = ("nonce", "proof")


def claims_authority(payload: dict[str, Any]) -> bool:
    """True iff this event CLAIMS capability authority — it names an `authorized` grant,
    or its body names a `cap`, or it carries a `proof`. Only such an event has authority
    to re-check; a capability-less INVOKE gains nothing from this gate."""
    if payload.get("authorized") is not None:
        return True
    body = payload.get("body")
    if not isinstance(body, dict):
        return False
    return body.get("cap") is not None or body.get("proof") is not None


def _holder_agents(weave: Weave, holder: str) -> list[Cell]:
    """The holder's OWN live agent cells at the frontier, in deterministic id order. The
    §3 proof names a holder PRINCIPAL, not an agent Cell, so the envelope check is
    existential over the cells that principal actually binds (`authorize_detail` step 0
    re-checks the principal for each candidate, so no other principal's envelope can be
    borrowed)."""
    return sorted(
        (
            c
            for c in weave.of_type(AGENT)
            if isinstance(c.content, dict) and c.content.get("principal") == holder
        ),
        key=lambda c: c.id,
    )


def recheck_invoke_authority(weft: Weft, payload: dict[str, Any]) -> tuple[bool, str]:
    """Re-verify an ingested INVOKE's carried AuthorizationProof at its causal frontier.

    Returns `(True, "ok")` when there is nothing to judge (not an INVOKE, or it claims no
    capability) or when the proof VERIFIES at the frontier; otherwise `(False, <code>)`
    with a terminal refusal code (`NO_PROOF` / `HOLDER_MISMATCH` / `UNAUTHORIZED`).

    Pure read: it folds the event's ancestor closure and writes nothing. It NEVER raises
    — any failure to reconstruct the frontier is itself a refusal (an authority decision
    is never made on a partial view)."""
    if payload.get("verb") != INVOKE or not claims_authority(payload):
        return True, "ok"
    body = payload.get("body")
    if not isinstance(body, dict):
        return False, NO_PROOF
    proof = body.get("proof")
    if not isinstance(proof, dict):
        return False, NO_PROOF  # claims a capability but carries no proof
    cap_id = proof.get("capability")
    nonce = body.get("nonce")
    parents = payload.get("parents")
    lamport = payload.get("lamport")
    if not isinstance(cap_id, str) or not isinstance(nonce, str):
        return False, NO_PROOF
    if not isinstance(parents, list) or not isinstance(lamport, int):
        return False, NO_PROOF
    # The proof must name the SAME grant the event claims — both the `authorized`
    # provenance field and the body's `cap` — so authority cannot be laundered by
    # attaching a valid proof for some OTHER capability.
    if {payload.get("authorized"), body.get("cap")} - {None, cap_id}:
        return False, NO_PROOF
    holder = payload.get("author")
    if not isinstance(holder, str) or proof.get("holder") != holder:
        return False, HOLDER_MISMATCH  # the proof was not made by this event's signer
    # The bound body is the event body minus the two post-bind fields; the bind also
    # commits to the event's OWN parents, so a proof lifted from another request or
    # another frontier cannot match.
    bind_body = {k: v for k, v in body.items() if k not in _POST_BIND_FIELDS}
    try:
        weave = Weave.fold_frontier(weft, parents)
    except Exception:
        # A frontier we cannot reconstruct (not closed locally, or an ancestor body the
        # fold refuses) means authority CANNOT be established → refuse (fail closed).
        return False, UNAUTHORIZED
    # Frontier inputs, all folded and integral: "now" is the frontier's logical lamport
    # (the value the origin's own frontier had on a linear log — never wall-clock), and
    # `prior_uses` is the count of INVOKEs this grant already authorized inside the
    # closure (the spend side of a single-use / max_uses lease).
    now = weave.frontier_lamport
    prior_uses = sum(1 for inv in weave.invocations if inv.cap == cap_id)
    # Capability-scoped approvals as FOLDED state (the durable equivalent of the invoke
    # seam's `approvals` set); an invocation-scoped approval naming exactly this
    # operation is matched inside `verify_proof` against this same frontier.
    approvals = capability.capability_approvals(weave)

    def _verify_holder_sig(pid: str, message: str, sig: str) -> bool:
        # Rotation-aware possession: the origin signed the bind with the same key that
        # signed the event, so an author enrolled on a succession chain is verified
        # against the key valid AT this event's point instead of being refused.
        return weft.verify_author_sig(pid, message, sig, lamport)

    for agent_cell in _holder_agents(weave, holder):
        ok, _why = capability.verify_proof(
            weave,
            weft.keyring,
            agent_cell,
            proof,
            INVOKE,
            bind_body,
            nonce,
            parents,
            approvals=approvals,
            now=now,
            prior_uses=prior_uses,
            verify_sig=_verify_holder_sig,
        )
        if ok:
            return True, "ok"
    return False, UNAUTHORIZED


def recheck_assert_authority(weft: Weft, payload: dict[str, Any]) -> tuple[bool, str]:
    """Re-verify an ingested ASSERT of an AUTHORITY-BEARING cell at its causal frontier
    (Nona N7 / design R1) — the same rule `Weft.append` applies at the local write door.

    An event reaches a peer's log through SYNC, not through `append`, so a door-only rule
    buys nothing across sync: a peer that forged a `capability` naming itself granter, or a
    `promotion` naming someone else as signer, would hand it over and the union would take
    it. Refusing it here means the forgery is never inserted — and because the rule is the
    one pure predicate in `authorship.py`, the door, this gate and the fold cannot drift.

    Judged AT THE FRONTIER, never against current state, for the reason this module's
    docstring gives: the constitutional root is read from the fold of exactly this event's
    ancestor closure, which is a property of the DAG and not of delivery order, so two
    peers handed the same event set accept the same subset (FOLD §11.2). A frontier with NO
    anchored root — the closure of a parentless event — cannot be judged, and the write is
    ALLOWED there rather than refused: whoever commits a log's first event becomes its
    root, and a later parentless event can never displace the first (its `seq` is
    necessarily higher), so the fold still refuses to derive authority from it.

    Returns `(True, "ok")` when there is nothing to judge (not an ASSERT, or not a guarded
    type) or when the authorship rule holds; otherwise `(False, UNAUTHORIZED_ASSERT)`.
    Pure read: it writes nothing and NEVER raises — a frontier that cannot be
    reconstructed is itself a refusal (fail closed)."""
    if payload.get("verb") != ASSERT:
        return True, "ok"
    body = payload.get("body")
    if not isinstance(body, dict):
        return True, "ok"  # a malformed body is refused by the §2 checks, not by authority
    cell_type = body.get("type")
    if cell_type not in authorship.GUARDED_TYPES:
        return True, "ok"  # two dict lookups for every ordinary assertion: no fold
    author = payload.get("author")
    parents = payload.get("parents")
    if not isinstance(author, str) or not isinstance(parents, list):
        return False, authorship.UNAUTHORIZED_ASSERT
    try:
        weave = Weave.fold_frontier(weft, parents)
    except Exception:
        # A frontier we cannot reconstruct means the root CANNOT be established → refuse
        # (an authority decision is never made on a partial view).
        return False, authorship.UNAUTHORIZED_ASSERT
    if authorship.refusal(
        cast(str, cell_type), body.get("content"), author, weave.genesis_author()
    ):
        return False, authorship.UNAUTHORIZED_ASSERT
    return True, "ok"
