"""Capabilities — Law 2: no ambient authority.

A capability is a Cell (authority is data). Authority is NOT the public Cell id —
ids are content hashes that appear all over the log and graph. Authority is a
*signed grant* to a specific principal, plus that principal proving possession of
its key on each request. Before any INVOKE is written, the kernel verifies, in
order: the signer is the acting agent, the agent holds a grant whose grantee is
that principal, the delegation path is downhill and granter-held, then every
caveat (budget, approval, sandbox).

Authority only ever flows DOWNHILL: `attenuate` narrows, never widens. A
compromised or prompt-injected agent's blast radius is exactly its grants — and
knowing a capability id buys nothing, because the id is not a bearer token.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from decima.kernel.hashing import content_id

if TYPE_CHECKING:
    from decima.kernel.crypto import Keyring
    from decima.kernel.weave import Cell, Weave


def capability_content(
    name: str,
    effect: str,
    target: str = "*",
    caveats: dict[str, Any] | None = None,
    delegable: bool = True,
    impl: dict[str, Any] | None = None,
    quarantined: bool = False,
    parent: str | None = None,
    grantee: str | None = None,
    granter: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "effect": effect,  # echo | shell | transform | forge
        "target": target,
        "caveats": caveats or {},  # budget, expires, rate, requires_approval, sandbox_only
        "delegable": delegable,
        "impl": impl,  # for authored caps: how the effect is realized
        "quarantined": quarantined,  # born True for forged caps until Nona promotes
        "parent": parent,  # the cap this was attenuated from, if any
        "grantee": grantee,  # the principal this grant was issued TO
        "granter": granter,  # the principal that issued this grant
    }


def envelope_holds(weave: Weave, agent_cell: Cell, cap_id: str) -> bool:
    """True if the agent holds cap_id directly — a grant edge in its envelope."""
    return cap_id in set(agent_cell.content.get("envelope", []))


# Lease caveats are numeric BOUNDS that may only shrink under attenuation: a child
# lease can expire no later, and be used no more times, than its parent — never the
# reverse (downhill). `expires_at` and `max_uses` are treated like `budget`.
_SHRINK_ONLY = ("budget", "expires_at", "max_uses")


def _caveats_downhill(child: dict[str, Any], parent: dict[str, Any]) -> bool:
    """Child caveats must be at least as strict as the parent's."""
    pc, cc = parent.get("caveats", {}), child.get("caveats", {})
    for k in _SHRINK_ONLY:  # numeric bounds may only shrink
        if k in pc and (k not in cc or int(cc[k]) > int(pc[k])):
            return False
    for k, v in pc.items():  # parent constraints must persist
        if k in _SHRINK_ONLY:
            continue
        if v and not cc.get(k):
            return False
    return True


def verify_delegation(weave: Weave, cap: Cell) -> tuple[bool, str]:
    """Walk the grant chain to its root, checking each hop is downhill and that
    the granter actually held what it delegated (granter == parent's grantee)."""
    seen: set[str] = set()
    while cap.content.get("parent"):
        if cap.id in seen:
            return False, "cyclic delegation"
        seen.add(cap.id)
        parent = weave.get(cap.content["parent"])
        if parent is None or parent.type != "capability":
            return False, "broken delegation: parent grant missing"
        if parent.retracted:
            return False, "delegation path revoked upstream (Morta)"
        if cap.content.get("granter") != parent.content.get("grantee"):
            return False, "granter did not hold the parent grant"
        if not _caveats_downhill(cap.content, parent.content):
            return False, "attenuation widened authority (not downhill)"
        cap = parent
    return True, "ok"


def lease_status(caveats: dict[str, Any], now: int | None, prior_uses: int) -> tuple[bool, str]:
    """Evaluate a grant's LEASE caveats — time-locked + single-use authority — at a
    logical frontier `now` and a deterministic count of prior INVOKEs this cap has
    already authorized. Fails CLOSED on expiry/exhaustion exactly like a revoked
    grant. "now" is the logical frontier time (lamport), never wall-clock, and the
    bounds are ints — no float, no clock, in signed/folded content (DETERMINISM §1).

    - `expires_at` (int): authority is denied once `now >= expires_at` (time-locked
      / time-locked-wallet). A grant whose lease has lapsed is dead capability.
    - `max_uses` (int): authority is denied once `prior_uses >= max_uses`
      (single-use = max_uses 1, e.g. an ephemeral single-use card).

    Returns (live, reason). `live` False means the lease has failed closed; the
    caller treats it as if the grant were RETRACTed."""
    expires_at = caveats.get("expires_at")
    # "now" must be known to evaluate a time-lock; absent a frontier we fail CLOSED
    # rather than silently treat the lease as live (fail-closed on ambiguity, like
    # the cascade's missing-ancestor rule).
    if expires_at is not None and (now is None or int(now) >= int(expires_at)):
        return False, (f"lease expired (frontier {now} ≥ expires_at {expires_at})")
    max_uses = caveats.get("max_uses")
    if max_uses is not None and int(prior_uses) >= int(max_uses):
        return False, (f"lease exhausted ({prior_uses}/{max_uses} uses spent)")
    return True, "ok"


# Stable machine-readable denial vocabulary, produced AT the denial site (never
# re-derived from the human sentence — the 0.3.0-era authorization facade substring-
# matched the prose, so any rewording silently degraded classification to DENIED).
# `decima.kernel.authorization.ReasonCode` re-exports these values as the public
# contract downstream code branches on; keep them stable across refactors.
class DenialCode:
    OK = "OK"
    SIGNER_MISMATCH = "SIGNER_MISMATCH"
    NO_SUCH_CAPABILITY = "NO_SUCH_CAPABILITY"
    NOT_A_CAPABILITY = "NOT_A_CAPABILITY"
    REVOKED = "REVOKED"
    LEASE_FAILED = "LEASE_FAILED"
    QUARANTINED = "QUARANTINED"
    NO_ENVELOPE = "NO_ENVELOPE"
    WRONG_GRANTEE = "WRONG_GRANTEE"
    DELEGATION_INVALID = "DELEGATION_INVALID"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    SANDBOX_ONLY = "SANDBOX_ONLY"
    DENIED = "DENIED"  # reserved fallback; no denial site below produces it


def authorize_detail(
    weave: Weave,
    agent_cell: Cell,
    cap_id: str,
    args: dict[str, Any],
    acting_principal: str,
    spent: float = 0.0,
    approvals: set[str] | None = None,
    now: int | None = None,
    prior_uses: int = 0,
) -> tuple[bool, str, str]:
    """The ocap check performed before every INVOKE is written to the Weft.

    Returns ``(allowed, reason_sentence, denial_code)`` — the code is a `DenialCode`
    value chosen at the exact denial site, so the machine-readable outcome can never
    drift from the human sentence.

    `acting_principal` is the principal that will SIGN the INVOKE. The id being
    public is exactly why this — not id-possession — is the gate.

    `now` is the current logical frontier time (lamport) and `prior_uses` is the
    deterministic count of INVOKEs this capability has already authorized (folded
    from the Weave). Together they drive the LEASE caveats — time-locked
    (`expires_at`) and single-use (`max_uses`) — which fail CLOSED on
    expiry/exhaustion just like a revoked grant.
    """
    approvals = approvals or set()

    # 0. Possession proof: you act as yourself. The signer must be the agent.
    if acting_principal != agent_cell.content.get("principal"):
        return (
            False,
            "signer is not the acting agent (possession proof failed)",
            DenialCode.SIGNER_MISMATCH,
        )

    cap = weave.get(cap_id)
    if cap is None:
        return False, "no such capability", DenialCode.NO_SUCH_CAPABILITY
    if cap.type != "capability":
        return False, "target is not a capability", DenialCode.NOT_A_CAPABILITY
    if cap.retracted:
        # A lapsed LEASE fails closed via the SAME retraction path as a revoke — but
        # name WHY (expiry/exhaustion) so the denial is legible, not just "revoked".
        if getattr(cap, "lease_expired", False):
            _, why = lease_status(cap.content.get("caveats", {}), now, prior_uses)
            return False, f"lease failed closed: {why}", DenialCode.LEASE_FAILED
        return False, "capability revoked (RETRACTed)", DenialCode.REVOKED
    agent_is_sandbox = agent_cell.content.get("sandbox", False)
    if cap.content.get("quarantined") and not agent_is_sandbox:
        return (False, "capability quarantined (not promoted by Nona)", DenialCode.QUARANTINED)

    # 1. The grant must be in the agent's envelope...
    if not envelope_holds(weave, agent_cell, cap_id):
        return (False, "no grant in envelope (no ambient authority)", DenialCode.NO_ENVELOPE)
    # 2. ...and that grant must name THIS principal as its grantee.
    grantee = cap.content.get("grantee")
    if grantee is not None and grantee != acting_principal:
        return (
            False,
            "grant issued to a different principal (id is public, not a bearer token)",
            DenialCode.WRONG_GRANTEE,
        )
    # 3. The delegation path must be downhill and granter-held.
    ok, why = verify_delegation(weave, cap)
    if not ok:
        return False, why, DenialCode.DELEGATION_INVALID

    # 4. Caveats.
    caveats = cap.content.get("caveats", {})
    budget = caveats.get("budget")
    if budget is not None and spent + float(args.get("cost", 0)) > float(budget):
        return (
            False,
            f"budget exceeded (grant budget {budget}, spent {spent})",
            DenialCode.BUDGET_EXCEEDED,
        )
    if caveats.get("requires_approval") and cap_id not in approvals:
        return (False, "requires human approval (Morta gate)", DenialCode.APPROVAL_REQUIRED)
    if caveats.get("sandbox_only") and not agent_is_sandbox:
        return (
            False,
            "sandbox_only: not runnable outside a sandbox principal",
            DenialCode.SANDBOX_ONLY,
        )
    # Lease caveats — time-locked (`expires_at`) + single-use (`max_uses`). Fail
    # CLOSED on expiry/exhaustion exactly like a revoked grant. `now` is the logical
    # frontier (lamport); `prior_uses` is the deterministic fold of prior INVOKEs.
    live, why = lease_status(caveats, now, prior_uses)
    if not live:
        return False, why, DenialCode.LEASE_FAILED
    return True, "ok", DenialCode.OK


def authorize(
    weave: Weave,
    agent_cell: Cell,
    cap_id: str,
    args: dict[str, Any],
    acting_principal: str,
    spent: float = 0.0,
    approvals: set[str] | None = None,
    now: int | None = None,
    prior_uses: int = 0,
) -> tuple[bool, str]:
    """`authorize_detail` without the denial code — the frozen reference surface
    (heartbeat parity); new code that branches on the outcome should call
    `authorize_detail` or `decima.kernel.authorization.authorize_decision`."""
    allowed, reason, _code = authorize_detail(
        weave,
        agent_cell,
        cap_id,
        args,
        acting_principal,
        spent=spent,
        approvals=approvals,
        now=now,
        prior_uses=prior_uses,
    )
    return allowed, reason


# ── Morta permanent gates (MORTA_CAPABILITIES §4) ───────────────────────────
# Realm-constitution effect classes whose MINIMUM caveats ordinary attenuation
# or brokering cannot remove. A broker (powerbox.py) merges these in before it
# issues a grant, so a scoped grant for a gated effect is born with its floor
# intact — there is no "magical unchangeable bit", just a floor the narrowing
# path must always carry.
MORTA_FLOORS = {
    "shell": {"requires_approval": True},  # arbitrary local effect
    "financial": {"requires_approval": True, "reversible_only": True},
}
# (browser's outward `publish` is Morta-gated at boot via its impl split, so it
#  is not floored by effect name here — see kernel._boot / specs/BROWSER_WORKER.md.)


def morta_floor(effect: str) -> dict[str, Any]:
    """The permanent minimum caveats for an effect class (empty if ungated)."""
    return dict(MORTA_FLOORS.get(effect, {}))


def with_morta_floor(effect: str, caveats: dict[str, Any]) -> dict[str, Any]:
    """Merge the realm's permanent minimum caveats for `effect` over `caveats`.
    Floors only ever ADD or strengthen constraints; a floor caveat cannot be
    dropped by the caller proposing a looser scope."""
    merged = dict(caveats)
    merged.update(morta_floor(effect))  # floor wins
    return merged


def attenuation_valid(child: dict[str, Any], parent: dict[str, Any]) -> tuple[bool, str]:
    """Structural narrowing proof (MORTA §5): a child grant is valid only if its
    permitted-invocation set ⊆ the parent's. Checks effect specialization, target
    subset (the prototype selector grammar is `*` ⊇ everything ⊇ an exact target),
    and that caveats are downhill. The broker proves this BEFORE issuing — an
    attestation can never substitute for a proof of authority narrowing."""
    if child.get("effect") != parent.get("effect"):
        return False, "effect changed (not a specialization)"
    pt, ct = parent.get("target", "*"), child.get("target", "*")
    if pt != "*" and ct != pt:
        return False, "target is not a subset of the parent selector"
    if not _caveats_downhill(child, parent):
        return False, "caveats widened (not downhill)"
    return True, "ok"


def attenuate(
    parent_content: dict[str, Any],
    stricter: dict[str, Any],
    parent_id: str,
    grantee: str,
    granter: str,
) -> dict[str, Any]:
    """Derive a weaker capability granted to `grantee` by `granter`.
    Caveats can only get tighter."""
    caveats = dict(parent_content.get("caveats", {}))
    for k, v in stricter.items():
        if k in _SHRINK_ONLY:
            # numeric bounds (budget + lease caveats) may only shrink, never widen.
            # ints only — floats are forbidden in canonical/hashed content (§1)
            caveats[k] = min(int(v), int(caveats.get(k, v)))
        else:
            caveats[k] = v  # adding a constraint (e.g. requires_approval) only narrows
    return capability_content(
        name=parent_content["name"],  # keep the routable name; attenuation lives in caveats/parent
        effect=parent_content["effect"],
        target=parent_content["target"],
        caveats=caveats,
        delegable=parent_content["delegable"],
        impl=parent_content.get("impl"),
        quarantined=parent_content.get("quarantined", False),
        parent=parent_id,
        grantee=grantee,
        granter=granter,
    )


# ── AuthorizationProof (Weft Protocol §3) ──────────────────────────────────
# Authority is not just "I hold the grant" — it is "I am the grantee, I possess
# the key, and this signature is bound to THIS exact request." The invocation
# bind is what makes a captured proof useless against any other request.


def invocation_bind(verb: str, body: dict[str, Any], nonce: str, parents: list[str]) -> str:
    """Hash binding a proof to one exact request: verb, body, nonce, and the
    causal frontier. Change any of them and the proof no longer matches."""
    return content_id({"verb": verb, "body": body, "nonce": nonce, "parents": parents}, kind="bind")


# ── Approvals as Weft events (Morta gate) ────────────────────────────────────
# The `requires_approval` Morta gate used to consult an in-memory per-capability set
# on the kernel — ambient, unauditable, gone on restart. Approvals are now EVENTS on
# the Weft (folded state), in two scopes:
#   • capability — approve the cap itself (operator-enables it; authorizes its
#     requires_approval invokes). Back-compat: this is what `kernel.approve` records.
#   • invocation — approve exactly ONE operation (this cap + verb + args + nonce).
#     Approving "pay 5" does NOT authorize "pay 500": the approval names the operation,
#     not the capability. Single-use — consumed (RETRACTed) once its invoke lands.
APPROVAL = "approval"


def op_bind(verb: str, body: dict[str, Any], nonce: str) -> str:
    """A frontier-INDEPENDENT bind identifying one exact operation: verb + body
    (cap + args) + nonce. Unlike `invocation_bind` it omits `parents`, so an
    invocation approval stays matchable across intervening events (the approval event
    itself moves the frontier) until the operation runs and consumes it."""
    return content_id({"verb": verb, "body": body, "nonce": nonce}, kind="op")


def approval_id(cap_id: str, ob: str | None = None) -> str:
    """Cell id for an approval. `ob=None` → capability-scoped; `ob=<op_bind>` →
    invocation-scoped. Content-addressed so re-approving is idempotent (same cell)."""
    return content_id({"approval": cap_id, "op": ob}, kind="approval")


def capability_approvals(weave: Weave) -> set[str]:
    """The set of cap ids that carry a live CAPABILITY-scoped approval on the Weft —
    the folded equivalent of the old in-memory approvals set."""
    return {
        cast(str, c.content.get("capability"))
        for c in weave.of_type(APPROVAL)
        if not c.retracted and c.content.get("scope") == "capability"
    }


def invocation_approved(
    weave: Weave, cap_id: str, verb: str, body: dict[str, Any], nonce: str
) -> bool:
    """True iff a live INVOCATION-scoped approval names EXACTLY this operation. Any
    change to cap/verb/args/nonce yields a different `op_bind`, so the approval fails
    to match — approval is bound to the operation, never the whole capability."""
    ob = op_bind(verb, body, nonce)
    cell = weave.get(approval_id(cap_id, ob))
    return (
        cell is not None
        and not cell.retracted
        and cell.type == APPROVAL
        and cell.content.get("scope") == "invocation"
    )


def grant_event_of(weave: Weave, cap: Cell | None) -> str | None:
    """The latest event that asserted this grant (its provenance tail)."""
    return cap.provenance[-1] if cap and cap.provenance else None


def delegation_events(weave: Weave, cap: Cell | None) -> list[str]:
    """Grant events from this capability up through every attenuation to the root."""
    path: list[str] = []
    seen: set[str] = set()
    while cap and cap.id not in seen:
        seen.add(cap.id)
        ge = grant_event_of(weave, cap)
        if ge:
            path.append(ge)
        parent = cap.content.get("parent")
        cap = weave.get(parent) if parent else None
    return path


def build_proof(
    weave: Weave,
    keyring: Keyring,
    holder: str,
    cap_id: str,
    verb: str,
    body: dict[str, Any],
    nonce: str,
    parents: list[str],
) -> dict[str, Any]:
    """The proof a holder presents to authorize an invocation (Event field 5)."""
    cap = weave.get(cap_id)
    bind = invocation_bind(verb, body, nonce, parents)
    return {
        "capability": cap_id,
        "grant_event": grant_event_of(weave, cap),
        "delegation_path": delegation_events(weave, cap),
        "holder": holder,
        "invocation_bind": bind,
        "holder_sig": keyring.sign(holder, bind),  # possession, bound to the request
    }


def verify_proof(
    weave: Weave,
    keyring: Keyring,
    agent_cell: Cell,
    proof: dict[str, Any],
    verb: str,
    body: dict[str, Any],
    nonce: str,
    parents: list[str],
    spent: float = 0.0,
    approvals: set[str] | None = None,
    now: int | None = None,
    prior_uses: int = 0,
) -> tuple[bool, str]:
    """Verify a proof before its INVOKE is written. Binds key-possession to the
    exact request, then runs the full ocap check (envelope, grantee, delegation,
    caveats — including the time-locked/single-use LEASE caveats, evaluated at the
    logical frontier `now` with `prior_uses` folded from the Weave)."""
    holder = cast("str | None", proof.get("holder"))
    if holder != agent_cell.content.get("principal"):
        return False, "holder is not the acting agent"
    expect = invocation_bind(verb, body, nonce, parents)
    if proof.get("invocation_bind") != expect:
        return False, "invocation bind mismatch (replayed or altered request)"
    if not keyring.verify(cast(str, holder), expect, proof.get("holder_sig", "")):
        return False, "holder signature invalid (possession proof failed)"
    # Approval (Morta): the caller's capability-scoped set, OR a live invocation-scoped
    # approval naming EXACTLY this operation (frontier-independent op_bind). An approval
    # for one operation never satisfies a different one — anti-ambient, anti-replay.
    cap_id = cast(str, proof.get("capability"))
    approvals = set(approvals or set())
    if invocation_approved(weave, cap_id, verb, body, nonce):
        approvals = approvals | {cap_id}
    ok, why = authorize(
        weave,
        agent_cell,
        cap_id,
        body.get("args", {}),
        cast(str, holder),
        spent,
        approvals,
        now=now,
        prior_uses=prior_uses,
    )
    if not ok:
        return False, why
    cap = weave.get(cap_id)
    if proof.get("grant_event") != grant_event_of(weave, cap):
        return False, "grant_event does not match the live grant"
    if proof.get("delegation_path") != delegation_events(weave, cap):
        return False, "delegation path does not match the grant chain"
    return True, "ok"
