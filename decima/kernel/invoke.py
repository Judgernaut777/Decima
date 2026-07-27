"""The authorized invoke seam — the one door an effect walks through (Nona wave N5).

Until this module existed, `decima/` could AUTHORIZE an invocation and could never PERFORM
one. `capability.build_proof` had no product caller at all; `verify_proof` had exactly one,
on the ingest side, re-checking somebody else's event. Nothing in the shipping product ever
appended an INVOKE. So the ocap spine was a boundary with no door in it, and
`tests/architecture/test_no_raw_invoke_append.py` was guarding a door that had not been cut
yet. This is the door, and it is deliberately the only one.

ORDER IS THE WHOLE SECURITY ARGUMENT, so it is fixed here and cannot be reordered by a
caller:

  1. fold the deterministic inputs — the logical frontier (`weave.frontier_lamport`),
     `prior_uses` (the folded count of INVOKEs this grant already authorized),
     `spent_to_date` (the folded integer cost of its receipts) and the capability-scoped
     approval set. All four are pure functions of the Log, so a lease expires, a budget
     exhausts and a Morta gate clears identically on every fold;
  2. mint the invocation nonce and BIND a proof to this exact request;
  3. `verify_proof` — possession, envelope, grantee, delegation, and every caveat
     (quarantine, budget, Morta approval, sandbox_only, lease). A refusal returns here and
     NOTHING is written: no INVOKE, no effect, no receipt;
  4. validate the receipt's `cost` as a non-negative int BEFORE any write, so a malformed
     cost cannot land half a transaction;
  5. append the INVOKE, carrying its proof, signed by the holder;
  6. spend a single-use invocation-scoped approval (RETRACT) — the authorized INVOKE is on
     the log, so that approval can never authorize a second operation;
  7. dispatch the DECLARED EFFECT to an injected handler;
  8. record a `result` Cell.

The handler is INJECTED, and the default REFUSES. The kernel may import only nacl, sqlite3,
blake3, cbor2 and stdlib, so it structurally cannot reach `decima.workers` — which is the
right shape anyway: the trusted core decides WHETHER an effect may happen and the untrusted
side decides HOW. An effect with no registered handler is refused with `NO_HANDLER`, which
is a refusal, not a crash and not a silent success. Same discipline as
`candidate.propose_candidate(codegen=...)`: no default path to power.

ONE FRONTIER, AND THE SEAM OWNS IT. The seam FOLDS the Log itself — it does not accept a
`Weave` from its caller — and it folds the CAUSAL FRONTIER of the very parents the INVOKE
will descend from (`Weave.fold_frontier(weft, [weft.head])`), under `weft.lock`, so the
decision and the event are one critical section. That is not tidiness; it is the only way
the seam can agree with `acceptance.recheck_invoke_authority`, which re-derives authority
from `Weave.fold_frontier(weft, parents)` when the event reaches another node. A caller-
supplied Weave could name a DIFFERENT frontier from `weft.head` — a long-lived service
caching one fold is the obvious case — and then `now`, `prior_uses` and `parents` come from
two different worlds: a revoked organ runs locally and the INVOKE it wrote is REFUSED by
the ingest gate on every replica, so the origin holds an event its own acceptance gate
rejects, its tail orphans, and the peers fold to a different `state_root` (Law 5).

NO AUTHORITY INPUT COMES FROM THE CALLER. Both halves of the Morta gate and both halves of
the lease are DERIVED here, never passed in. Approvals are `capability.capability_approvals`
over the fold — the capability-scoped half — plus the invocation-scoped approval
`verify_proof` matches against the same frontier. `spent` is `spent_to_date`, the summed
integer `cost` of this grant's live receipts. An earlier shape of this seam took an
`approvals` set and a `spent` float as arguments, which meant a `requires_approval` gate
could be cleared by in-process assertion with nothing on the log (exactly the "ambient,
unauditable, gone on restart" shape approvals were moved onto the Weft to kill) and a
`budget` caveat was a no-op that re-armed at its full ceiling on every call. Any authority
input a caller can choose is not a caveat; it is a suggestion.

THE NONCE IS DERIVED, NOT RANDOM. The reference seam mints `os.urandom(16).hex()`. That
nonce enters the invocation bind, the INVOKE body, the event id and the receipt's
idempotency key — so a random one makes the whole invoke path unreplayable and breaks Law 5
for every organ. Here it is `content_id({cap, args, attempt})` where `attempt` is the folded
INVOKE count for that grant. Stated honestly: a derived nonce is weaker than a random one as
replay protection, and the monotonic `attempt` drawn from the log is what carries that
weight — two identical calls get DIFFERENT nonces because the first one incremented the
count. A caller may still PIN a nonce, which is the seam an invocation-scoped approval needs
to name an operation before it happens.

NOTHING HOST-VARIABLE ENTERS THE RECEIPT. A handler returns a small, checked outcome:
status, a semantic `ok`, an output DIGEST (never the output — an untrusted worker's return
value can contain a `repr()` with a memory address in it), and a flat provenance map of
ints/strings/bools. A handler that returns anything else does not get to poison the log: the
outcome is replaced with a `HANDLER_CONTRACT` refusal and recorded as such.

WHAT `ok` MEANS, because `Weave.canary_health` folds it. The canary counts a receipt as a
failure when `status == "FAILED"` **or** `ok is False`. `status` covers crashes, timeouts
and refusals. `ok` is the seam for a SEMANTIC failure — an effect that returned normally and
returned something wrong. The kernel does not decide that; the handler does, against the
contract the candidate declared. If a handler cannot judge it, it says so
(`ok = (status == SUCCEEDED)`) and records that the receipt carries no semantic verdict,
rather than implying one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from decima.kernel import capability, model, receipts
from decima.kernel.crypto import Keyring
from decima.kernel.hashing import content_id
from decima.kernel.weave import Cell, Weave
from decima.kernel.weft import INVOKE, RETRACT, Weft

# The Cell type `Weave.canary_health` folds. It is NOT `decima.runtime.cells.RECEIPT`:
# that Cell is keyed on step/lease/attempt for the runtime's reconciliation projections and
# carries no `of`, so it can never satisfy the canary fold. Two receipt shapes for two
# different questions; bending either into the other would corrupt the other's projection.
RESULT = "result"

# Refusal codes. A refusal is a STRUCTURAL statement — "there is no path from here to that
# effect" — and is deliberately distinguishable from an authorization denial, which says
# "there is a path and you may not take it". Conflating them is how a system teaches its
# operator to click `approve` at something that cannot run.
NO_HANDLER = "NO_HANDLER"  # no declared handler for this effect
HANDLER_RAISED = "HANDLER_RAISED"  # the handler blew up; the effect did not happen
HANDLER_CONTRACT = "HANDLER_CONTRACT"  # the handler returned an unrecordable outcome

# The denial code reported when `verify_proof` refused for a reason `authorize_detail` does
# not model: the possession/bind half (holder mismatch, altered request, bad signature).
PROOF_INVALID = "PROOF_INVALID"

_STATUSES = frozenset({receipts.SUCCEEDED, receipts.FAILED, receipts.UNKNOWN})


@dataclass(frozen=True)
class EffectRequest:
    """Everything a declared effect handler is given — and nothing else.

    Notably absent: the Keyring, the Weft, and any way to mint authority. A handler is
    handed an ALREADY-VERIFIED proof and a digest-bound description of what to run; it
    cannot widen the grant it was called under, and it cannot write to the log through
    anything in here.
    """

    cap_id: str
    cap_content: dict[str, Any]
    effect: str
    args: dict[str, Any]
    holder: str
    invocation_id: str
    nonce: str
    attempt: int
    now: int
    proof: dict[str, Any]


@dataclass(frozen=True)
class EffectOutcome:
    """What a handler is allowed to say about what happened.

    `status` is the definite/indefinite outcome (`SUCCEEDED` / `FAILED` / `UNKNOWN` — an
    unobservable outcome is UNKNOWN and never a fabricated pass). `ok` is the SEMANTIC
    verdict the canary reads. `output_digest` is a content-address of the return value,
    because the value itself is untrusted and may not be deterministic. `provenance` is a
    FLAT map of ints/strings/bools — a projection, never a manifest.
    """

    status: str = receipts.FAILED
    ok: bool = False
    output_digest: str = ""
    refusal: str = ""
    error: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)


EffectHandler = Callable[[EffectRequest], EffectOutcome]


def _refuse_unhandled(request: EffectRequest) -> EffectOutcome:
    """The default for every effect: refuse. There is no ambient executor."""
    return EffectOutcome(
        status=receipts.FAILED,
        ok=False,
        refusal=NO_HANDLER,
        error=(
            f"no declared handler for effect {request.effect!r}: the invoke seam dispatches "
            "only to handlers a caller injected, and there is deliberately no default "
            "executor (decima/kernel/invoke.py)"
        ),
    )


def prior_uses(weave: Weave, cap_id: str) -> int:
    """The deterministic count of INVOKEs this grant has already authorized.

    Pure fold over the Log, so it time-travels like all state: it is the spend side of a
    `max_uses` lease AND the monotonic sequence that keeps the derived nonce unique.
    """
    return sum(1 for inv in weave.invocations if inv.cap == cap_id)


def spent_to_date(weave: Weave, cap_id: str) -> int:
    """The deterministic integer spend this grant has already committed — the `budget`
    caveat's missing other half.

    `capability.authorize_detail` checks `spent + cost > budget`, and until this fold
    existed `spent` was an ambient caller argument that defaulted to zero. A grant with
    `budget: 10` could therefore be invoked without limit, ten units at a time, and every
    one of those events ingested cleanly on a peer — so nothing downstream caught it
    either. The data was already on the Log: every `result` Cell this seam writes records
    the grant (`cap`) and an integer `cost`.

    Counted at AUTHORIZATION, not at success: a FAILED or refused effect still consumed the
    budget it was authorized against. Refunding a failure would let an organ that fails
    reliably spend forever. Integers only — money and units never enter folded content as
    floats, and a `bool` is not a cost.
    """
    total = 0
    for cell in weave.of_type(RESULT):
        if cell.retracted or cell.content.get("cap") != cap_id:
            continue
        cost = cell.content.get("cost")
        if isinstance(cost, int) and not isinstance(cost, bool):
            total += cost
    return total


def invocation_nonce(cap_id: str, args: dict[str, Any], attempt: int) -> str:
    """The per-invocation nonce, derived from Cell data instead of `os.urandom`.

    Determinism is Law 5 and the nonce is load-bearing for the event id, so it may not come
    from the OS entropy pool or the clock. `attempt` is the folded INVOKE count, which is
    what makes two otherwise identical invocations distinct — without it the second call
    would collapse onto the first one's bind, and a captured proof would replay.
    """
    return content_id({"cap": cap_id, "args": args, "attempt": int(attempt)}, kind="nonce")


def result_cell_id(invoke_event: str) -> str:
    """The receipt's Cell id — content-addressed on the INVOKE it descends from, so one
    invocation has exactly one receipt and re-deriving it is idempotent."""
    return f"result:{content_id({'result_of': invoke_event}, kind='cell')}"


def _flat_scalars(value: object) -> bool:
    """Provenance is ints, strings and bools — nothing else, no nesting.

    This is the guard that keeps the worker's isolation manifest out of hashed content. That
    manifest carries a `mkdtemp` path, a live fd list, an inner pid and host-arch-dependent
    seccomp state; every one of those varies run to run or host to host, and any of them in
    a `result` Cell makes `state_root()` unreplayable. Floats are refused for the same
    reason the rest of the log refuses them.
    """
    if not isinstance(value, dict):
        return False
    for key, item in value.items():
        if not isinstance(key, str):
            return False
        if isinstance(item, bool):
            continue
        if isinstance(item, int) or isinstance(item, str):
            continue
        return False
    return True


def _checked(outcome: object) -> EffectOutcome:
    """Hold a handler to the outcome contract; a violation becomes a recorded refusal.

    Deliberately NOT an exception: by the time a handler runs, the INVOKE is already on the
    log, and an invocation with no receipt is a hole in the audit trail. So a misbehaving
    handler produces an honest FAILED receipt naming the breach instead of a traceback and a
    missing record.
    """
    if not isinstance(outcome, EffectOutcome):
        return EffectOutcome(
            status=receipts.FAILED,
            ok=False,
            refusal=HANDLER_CONTRACT,
            error=f"handler returned {type(outcome).__name__}, not an EffectOutcome",
        )
    if outcome.status not in _STATUSES:
        return EffectOutcome(
            status=receipts.FAILED,
            ok=False,
            refusal=HANDLER_CONTRACT,
            error=f"handler returned status {outcome.status!r}, not one of {sorted(_STATUSES)}",
        )
    if not _flat_scalars(outcome.provenance):
        return EffectOutcome(
            status=receipts.FAILED,
            ok=False,
            refusal=HANDLER_CONTRACT,
            error=(
                "handler provenance must be a FLAT map of int/str/bool: signed content "
                "carries no floats and no host-variable structures (an isolation manifest "
                "would make the receipt unreplayable)"
            ),
        )
    if not isinstance(outcome.output_digest, str) or not isinstance(outcome.error, str):
        return EffectOutcome(
            status=receipts.FAILED,
            ok=False,
            refusal=HANDLER_CONTRACT,
            error="handler output_digest/error must be strings",
        )
    return outcome


def _frontier(weft: Weft) -> tuple[Weave, list[str]]:
    """The ONE view this seam decides on, and the parents its INVOKE will descend from.

    `Weave.fold_frontier(weft, [head])` is the ancestor closure of the event about to be
    written — byte-for-byte the view `acceptance.recheck_invoke_authority` reconstructs when
    that event reaches a peer. A whole-log `Weave.fold` would ALSO sweep in any concurrent
    branch a sync unioned in, which is not in the closure, so the two gates could disagree
    on a forked log. Empty log ⇒ no parents and nothing to authorize against anyway.

    An incomplete closure RAISES here rather than becoming a denial: the log is append-only
    and never pruned, and every ingested event's parents are already present, so a missing
    ancestor is store corruption — not an authorization outcome, and not something to report
    to a caller as "you may not do that".
    """
    parents = [weft.head] if weft.head else []
    weave = Weave.fold_frontier(weft, parents) if parents else Weave.fold(weft)
    return weave, parents


def invoke(
    weft: Weft,
    keyring: Keyring,
    agent_cell: Cell,
    cap_id: str,
    args: dict[str, Any],
    *,
    effects: Mapping[str, EffectHandler] | None = None,
    nonce: str | None = None,
    recorder: str | None = None,
) -> dict[str, Any]:
    """Authorize, enact and record ONE invocation of `cap_id` by `agent_cell`.

    Returns `{"denied": <sentence>, "code": <DenialCode>}` — having written NOTHING — when
    the ocap spine refuses, or `{"ok", "status", "refusal", "result_cell", "invoke_event",
    "nonce", "attempt", "signer"}` when the invocation was authorized. Note that an
    authorized invocation can still have a FAILED or refused effect: authority and outcome
    are different questions and are reported separately.

    `effects` maps a capability's declared `effect` to a handler; an unmapped effect is
    refused. There is deliberately NO parameter for a frontier, an approval set or a spend
    ledger: every authority input is folded from the Log here (see the module docstring), so
    this seam's decision is the same decision the ingest gate will make about the event it
    writes.
    """
    holder = agent_cell.content.get("principal")
    if not isinstance(holder, str) or not holder:
        raise ValueError("agent cell has no principal: there is no one to invoke as")

    # Steps 1-6 are STORE WORK and are held as ONE critical section (`weft.lock` is public,
    # re-entrant and documented for exactly this): the frontier the decision is made on is
    # then provably the frontier the INVOKE descends from, even with another thread
    # appending. The effect itself is dispatched OUTSIDE the lock — never hold a store lock
    # across an effect of unbounded latency.
    with weft.lock:
        # 1. Deterministic inputs, folded from the Log — never a wall clock, and never a
        #    caller's word for it.
        weave, parents = _frontier(weft)
        now = weave.frontier_lamport
        attempt = prior_uses(weave, cap_id)
        spent = spent_to_date(weave, cap_id)
        approvals = capability.capability_approvals(weave)
        op_nonce = nonce if nonce is not None else invocation_nonce(cap_id, args, attempt)
        body: dict[str, Any] = {"cap": cap_id, "args": args}

        # 2/3. Bind a proof to THIS exact request, then run the full ocap check.
        proof = capability.build_proof(
            weave, keyring, holder, cap_id, INVOKE, body, op_nonce, parents
        )
        ok, why = capability.verify_proof(
            weave,
            keyring,
            agent_cell,
            proof,
            INVOKE,
            body,
            op_nonce,
            parents,
            spent,
            approvals,
            now=now,
            prior_uses=attempt,
        )
        if not ok:
            # Re-derive the machine-readable code at the same inputs so a caller can branch
            # on WHY. `verify_proof` also refuses on the possession/bind half, which
            # `authorize_detail` does not model — that case reports PROOF_INVALID rather
            # than borrowing an authorization code it did not actually hit.
            effective = set(approvals)
            if capability.invocation_approved(weave, cap_id, INVOKE, body, op_nonce):
                effective.add(cap_id)
            _allowed, _reason, code = capability.authorize_detail(
                weave,
                agent_cell,
                cap_id,
                args,
                holder,
                spent,
                effective,
                now=now,
                prior_uses=attempt,
            )
            if code == capability.DenialCode.OK:
                code = PROOF_INVALID
            return {"denied": why, "code": code}

        cap = weave.get(cap_id)
        if cap is None:  # unreachable: authorize_detail already refused a missing cap
            return {
                "denied": "no such capability",
                "code": capability.DenialCode.NO_SUCH_CAPABILITY,
            }

        # 4. Receipt hygiene BEFORE any write: signed content carries integer money/units,
        #    never a float and never a bool-as-int. A malformed cost fails loud with nothing
        #    on the log.
        cost = args.get("cost", 0)
        if not (isinstance(cost, int) and not isinstance(cost, bool) and cost >= 0):
            raise ValueError(f"receipt cost must be a non-negative int (not bool), got {cost!r}")

        # 5. The INVOKE carries its AuthorizationProof and is signed by the holder's key. The
        #    body shape is pinned by acceptance._POST_BIND_FIELDS — nonce and proof, nothing
        #    else — so ingest can re-verify this event on another node.
        inv = weft.append(
            holder, INVOKE, {**body, "nonce": op_nonce, "proof": proof}, authorized=cap_id
        )

        # 6. Spend a single-use invocation-scoped approval. The authorized INVOKE is on the
        #    log, so that approval must not be able to authorize a second operation.
        #    WITHDRAW, not a cascade: spending an approval is not revoking anything.
        if capability.invocation_approved(weave, cap_id, INVOKE, body, op_nonce):
            spent_id = capability.approval_id(cap_id, capability.op_bind(INVOKE, body, op_nonce))
            weft.append(
                holder,
                RETRACT,
                {"cell": spent_id, "mode": "WITHDRAW", "reason": "consumed by its invocation"},
            )

    # 7. Dispatch the DECLARED effect. Untrusted work happens on the other side of this
    #    call, never in this process.
    effect = cast(str, cap.content.get("effect") or "")
    handler = (effects or {}).get(effect, _refuse_unhandled)
    request = EffectRequest(
        cap_id=cap_id,
        cap_content=dict(cap.content),
        effect=effect,
        args=dict(args),
        holder=holder,
        invocation_id=inv.id,
        nonce=op_nonce,
        attempt=attempt,
        now=now,
        proof=dict(proof),
    )
    try:
        outcome = _checked(handler(request))
    except Exception as exc:  # a handler blow-up is a FAILED effect, never a fake pass
        outcome = EffectOutcome(
            status=receipts.FAILED,
            ok=False,
            refusal=HANDLER_RAISED,
            error=f"{type(exc).__name__}: {exc}",
        )

    # 8. The receipt. Shaped so `Weave.canary_health` folds it: `of` is the INVOKE event,
    #    `status` is the receipts vocabulary, and `ok` is the semantic verdict.
    rid = result_cell_id(inv.id)
    content: dict[str, Any] = {
        "of": inv.id,
        "cap": cap_id,
        "name": cap.content.get("name") or "",
        "effect": effect,
        "status": outcome.status,
        "ok": bool(outcome.ok),
        "refusal": outcome.refusal,
        "error": outcome.error,
        "output_digest": outcome.output_digest,
        "idempotency": op_nonce,
        "attempt": int(attempt),
        "cost": int(cost),
        "provenance": dict(outcome.provenance),
    }
    model.assert_content(weft, recorder or holder, rid, RESULT, content)
    return {
        "ok": bool(outcome.ok),
        "status": outcome.status,
        "refusal": outcome.refusal,
        "error": outcome.error,
        "result_cell": rid,
        "invoke_event": inv.id,
        "nonce": op_nonce,
        "attempt": int(attempt),
        "signer": holder,
    }
