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

from collections.abc import Callable
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
    *,
    grantee: str,
    granter: str | None = None,
) -> dict[str, Any]:
    """Build the content of a grant. `grantee` is REQUIRED and must name a principal.

    It used to default to `None`, and that default was a hole rather than a convenience:
    `authorize_detail` refused a mismatched grantee only `if grantee is not None`, so a
    grant naming nobody was usable by ANY principal that could get it into an agent
    envelope — and an ordinary `agent` cell is deliberately not authorship-bound
    (`kernel/authorship.py`), so getting it into one is a single ASSERT. "Do not mint a
    capability without a grantee" was documentation; this is the signature that means a
    caller cannot forget, paired with the read-side refusal in `authorize_detail` that is
    the half which holds for a log already on disk.

    The refusal is raised HERE rather than left to the type checker because the mint sites
    are the last point at which the mistake is cheap, and because a `""` passes typing and
    would match no acting principal at read time — a grant that can never authorize
    anything is a bug to report, not a grant to write.

    NO BYTES MOVE. The returned mapping still carries the `grantee` key in the same
    position with the same encoding; only the ways of NOT supplying one are gone. Every
    content address, signature and golden vector is unchanged (there is no capability in
    any fixture regardless — `protocol/fixtures/fold.json` holds `note` cells only)."""
    if not isinstance(grantee, str) or not grantee:
        raise ValueError(
            "a capability must name the principal it is granted TO: `grantee` is required "
            "and must be a non-empty principal id. A grant naming nobody is usable by "
            "anyone who can place it in an agent envelope, so it is refused at the mint "
            "and again at the read (DenialCode.NO_GRANTEE)."
        )
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

# The first SET-VALUED caveat: the destinations an organ may reach. It is named here for
# documentation only — `_caveats_downhill` attenuates by SHAPE, so this constant does not
# gate the rule and a caveat nobody registered still cannot widen. Egress itself has no
# executor (`network` is NOT EXECUTABLE until a mediation seam exists), so today this caveat
# is authorization vocabulary with nothing behind it — which is the honest order to build it
# in: the attenuation rule has to be right BEFORE anything acts on the value.
EGRESS_ALLOW = "egress_allow"


def _is_set_valued(value: object) -> bool:
    """A caveat whose value ENUMERATES what is permitted, rather than bounding it."""
    return isinstance(value, (list, tuple, set, frozenset))


def _caveats_downhill(child: dict[str, Any], parent: dict[str, Any]) -> bool:
    """Child caveats must be at least as strict as the parent's.

    Three shapes of caveat, three rules, and the third one was missing:

      * NUMERIC BOUNDS (`_SHRINK_ONLY`) may only shrink — a child lease expires no later and
        is used no more times than its parent.
      * FLAGS must persist — a truthy parent constraint (`requires_approval`, `sandbox_only`)
        cannot be dropped by a child.
      * SET-VALUED caveats — ones that ENUMERATE permitted values rather than bounding them —
        must be a SUBSET. This clause did not exist, and its absence was a silent widening
        hole in the ocap core: a list-valued parent caveat only had to be truthy in the child,
        so a child could list `[a]`'s parent as `[a, b]` and `attenuation_valid` approved it.
        No caveat in the tree was list-valued yet, which is exactly why it went unnoticed —
        the first one to arrive would have carried the defect in with it.

    The rule is keyed on the parent value's SHAPE rather than on a name list, so it closes the
    class instead of the instance: any caveat that enumerates is attenuated by subset, whether
    or not anyone remembered to register it. A child that omits the key entirely fails the
    truthiness clause below, and a child that supplies a non-enumerating value for an
    enumerating parent is refused rather than coerced (fail closed on shape confusion)."""
    pc, cc = parent.get("caveats", {}), child.get("caveats", {})
    for k in _SHRINK_ONLY:  # numeric bounds may only shrink
        if k in pc and (k not in cc or int(cc[k]) > int(pc[k])):
            return False
    for k, v in pc.items():  # parent constraints must persist
        if k in _SHRINK_ONLY:
            continue
        if _is_set_valued(v):
            # Checked BEFORE the truthiness clause below, and not in addition to it: an
            # EMPTY child set is the narrowest possible attenuation ("reach nothing"), and
            # it is falsy, so the truthiness rule would have refused the strictest child
            # while permitting an equal one. Presence is still required — a missing key is
            # not set-valued, so dropping the caveat entirely still fails here.
            cv = cc.get(k)
            if not _is_set_valued(cv) or not set(cv).issubset(set(v)):
                return False
            continue
        if v and not cc.get(k):
            return False
    return True


def _grant_authorship(weave: Weave, cap: Cell) -> tuple[bool, str]:
    """Was this grant WRITTEN by someone entitled to write it? (Nona N7 / design R1.)

    Every other check in this module reads the graph — and an ASSERT was, until N7,
    unauthorized, so a hostile key-holder could write the graph the checks read: a
    capability naming itself `granter` and `grantee`, `parent: None`, `quarantined: False`,
    plus an agent cell whose envelope holds it, and `authorize` returned `(True, "ok")`
    with every check passing on forged inputs. This is the check that reads something the
    forger does not control: WHO ASSERTED the cell (`Weave.cell_asserted_by` — the author
    of the winning assertion, folded substrate, not content).

      * a ROOT grant (no `parent`) — authority that descends from nothing on the log — may
        be asserted only by the realm ROOT or by a principal root ANCHORED as a promoter
        (`Weave.may_mint_root_grant`). This is the clause that refuses the self-grant: a
        principal can name itself `granter` in content, but it cannot make itself root and
        it cannot anchor itself (a `promoter` cell counts only when ROOT asserted it, and
        root is the unforgeable min-`seq` genesis author);
      * a DELEGATED grant must be asserted by its own `granter`, or by root. Combined with
        the `granter == parent.grantee` hop check below, a live grant therefore traces to
        root along a path where every hop was written by the principal that actually held
        the authority it handed on.

    Fails CLOSED on ignorance: a cell with no recorded asserter (a Weave reassembled from
    snapshot leaves has no fold substrate) or a view with no anchored root cannot be
    judged, and an authority decision is never made on a view that cannot answer."""
    root = weave.genesis_author()
    if root is None:
        return False, "no constitutional root anchored in this view: grant cannot be judged"
    asserter = weave.cell_asserted_by(cap.id)
    if asserter is None:
        return False, "grant has no recorded asserter (unfolded or snapshot-leaf view)"
    if asserter == root:
        return True, "ok"
    if not cap.content.get("parent"):
        if weave.may_mint_root_grant(asserter):
            return True, "ok"
        return False, (
            f"root grant was asserted by {asserter}, who is neither the realm root {root} "
            "nor a root-anchored promoter (a self-issued grant is not authority)"
        )
    granter = cap.content.get("granter")
    if asserter != granter:
        return False, (
            f"grant was asserted by {asserter} but names {granter!r} as its granter "
            "(authority may only be handed on by the principal that holds it)"
        )
    return True, "ok"


def verify_delegation_detail(weave: Weave, cap: Cell) -> tuple[bool, str, str]:
    """Walk the grant chain to its root, checking each hop is downhill, that the granter
    actually held what it delegated (granter == parent's grantee), and — since N7 — that
    each hop was ASSERTED by a principal entitled to assert it (`_grant_authorship`).

    Returns `(valid, reason_sentence, denial_code)`. An authorship failure is reported as
    its OWN code (`UNAUTHORIZED_GRANT`), never folded into `DELEGATION_INVALID`: "this
    chain is malformed" and "this grant was forged by a key-holder" are different findings
    and a responder must be able to tell them apart from the code alone. `DenialCode`'s
    contract is that a code is produced AT the denial site, so it is produced here."""
    seen: set[str] = set()
    while True:
        if cap.id in seen:
            return False, "cyclic delegation", DenialCode.DELEGATION_INVALID
        seen.add(cap.id)
        ok, why = _grant_authorship(weave, cap)
        if not ok:
            return False, why, DenialCode.UNAUTHORIZED_GRANT
        if not cap.content.get("parent"):
            return True, "ok", DenialCode.OK
        parent = weave.get(cap.content["parent"])
        if parent is None or parent.type != "capability":
            return (
                False,
                "broken delegation: parent grant missing",
                DenialCode.DELEGATION_INVALID,
            )
        if parent.retracted:
            return (
                False,
                "delegation path revoked upstream (Morta)",
                DenialCode.DELEGATION_INVALID,
            )
        if cap.content.get("granter") != parent.content.get("grantee"):
            return (
                False,
                "granter did not hold the parent grant",
                DenialCode.DELEGATION_INVALID,
            )
        if not _caveats_downhill(cap.content, parent.content):
            return (
                False,
                "attenuation widened authority (not downhill)",
                DenialCode.DELEGATION_INVALID,
            )
        cap = parent


def verify_delegation(weave: Weave, cap: Cell) -> tuple[bool, str]:
    """`verify_delegation_detail` without the denial code — the frozen reference surface
    (heartbeat parity); new code that branches on the outcome calls the detail form."""
    valid, why, _code = verify_delegation_detail(weave, cap)
    return valid, why


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
    # The grant names NO grantee at all. Kept distinct from WRONG_GRANTEE because the two
    # say different things to a responder: WRONG_GRANTEE is "this grant belongs to someone
    # else" (the holder is wrong), NO_GRANTEE is "this grant belongs to nobody" (the GRANT
    # is malformed — a mint-time content defect, and every principal is equally refused).
    NO_GRANTEE = "NO_GRANTEE"
    DELEGATION_INVALID = "DELEGATION_INVALID"
    # N7 (design R1): the grant chain is well-formed but some hop was ASSERTED by a
    # principal with no right to assert it — a forged grant, not a malformed one. Kept
    # distinct from DELEGATION_INVALID so a responder can tell "broken chain" from
    # "a key-holder minted authority for itself" without parsing prose.
    UNAUTHORIZED_GRANT = "UNAUTHORIZED_GRANT"
    # N7: the agent Cell the invocation acts through claims the SANDBOX privilege (the
    # quarantine runtime) but was not asserted by the realm root, so the privilege is not
    # conferred and the quarantined grant behind it stays unreachable.
    UNAUTHORIZED_SANDBOX = "UNAUTHORIZED_SANDBOX"
    # The grant's effect class is Morta-gated (`MORTA_FLOORS`) but the grant does not carry
    # the floor. Distinct from APPROVAL_REQUIRED: that one says "this operation needs an
    # approval you have not supplied", this one says "this GRANT should never have existed in
    # this shape" — the answer is to re-mint it with its floor, not to approve anything.
    MORTA_FLOOR_MISSING = "MORTA_FLOOR_MISSING"
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
    # The SANDBOX privilege is conferred by the realm ROOT, never claimed (N7 / R1). It is
    # the one flag that makes a QUARANTINED capability invocable and satisfies the
    # `sandbox_only` Morta caveat, so an agent cell a principal asserted for itself must
    # not carry it — otherwise "promote yourself out of quarantine" is one ASSERT away.
    # `authorship.refusal` refuses that write at the door and at the acceptance gate; this
    # is the read-side half, and it is the half that holds for a log already on disk.
    claims_sandbox = bool(agent_cell.content.get("sandbox", False))
    sandbox_root = weave.genesis_author()
    agent_is_sandbox = claims_sandbox and (
        sandbox_root is not None and weave.cell_asserted_by(agent_cell.id) == sandbox_root
    )
    if claims_sandbox and not agent_is_sandbox:
        return (
            False,
            "agent cell claims `sandbox` but was not asserted by the realm root: the "
            "quarantine runtime is conferred, not self-declared",
            DenialCode.UNAUTHORIZED_SANDBOX,
        )
    if cap.content.get("quarantined") and not agent_is_sandbox:
        return (False, "capability quarantined (not promoted by Nona)", DenialCode.QUARANTINED)

    # 1. The grant must be in the agent's envelope...
    if not envelope_holds(weave, agent_cell, cap_id):
        return (False, "no grant in envelope (no ambient authority)", DenialCode.NO_ENVELOPE)
    # 2. ...and that grant must name THIS principal as its grantee.
    #
    #    A grant that names NOBODY is refused for EVERYONE. This clause used to read
    #    `if grantee is not None and ...`, which made a grantee-less grant authorize any
    #    principal that could name it — and an ordinary `agent` cell is deliberately not
    #    authorship-bound (the powerbox must be able to write another agent's envelope),
    #    so naming it was one unguarded ASSERT away. No authorship rule closes that: the
    #    grant may be perfectly well-authored and still name no holder, which is why
    #    SECURITY.md carried it as a *content* defect. It is closed at both ends now —
    #    `capability_content` refuses to mint one, and this refuses to honour one that is
    #    already on the log, which is the half that holds for a restored backup or a grant
    #    minted before the rule existed. A non-string binding is never coerced (the same
    #    discipline as `authorship._principal`): it simply matches no principal.
    grantee = cap.content.get("grantee")
    if not isinstance(grantee, str) or not grantee:
        return (
            False,
            "grant names no grantee: a capability is issued TO a principal, and one issued "
            "to nobody authorizes nobody (it is not a bearer token)",
            DenialCode.NO_GRANTEE,
        )
    if grantee != acting_principal:
        return (
            False,
            "grant issued to a different principal (id is public, not a bearer token)",
            DenialCode.WRONG_GRANTEE,
        )
    # 3. The delegation path must be downhill, granter-held, and — since N7 — written by
    #    principals entitled to write it, all the way up to a ROOT-asserted root grant.
    ok, why, code = verify_delegation_detail(weave, cap)
    if not ok:
        return False, why, code

    # 4. Caveats — beginning with the realm's PERMANENT floor for this grant's effect class,
    #    RE-DERIVED here rather than trusted from the bytes.
    #
    #    `MORTA_FLOORS` used to be merged in by the two issuing code paths and by nobody
    #    else, so the floor was a property of the CODE THAT HAPPENED TO MINT the grant, not
    #    of the grant. A principal entitled to mint one — root, or a root-anchored promoter —
    #    could mint a `shell` grant with no `requires_approval` or a `financial` grant with no
    #    `reversible_only` simply by not calling `with_morta_floor`, and every read honoured
    #    it. Compromise of a minting authority was compromise of the realm's constitution.
    #    Deriving the floor from the effect class the cell itself declares makes the mint-time
    #    merge an OPTIMISATION and this the guarantee: a floored effect is refused unless the
    #    grant carries its floor, whoever wrote it and whenever they wrote it.
    #
    #    Scoped honestly, because the alternative is a claim wider than the code:
    #      * it is keyed on `effect`, which every grant carries. It is NOT keyed on the Nona
    #        TIER, which `attenuate` drops when it rebuilds content — a brokered child of a
    #        `financial`-tier organ carries `effect: generated_code` and no tier at all, so a
    #        read-time tier floor is not a pure function of the folded cell and is not
    #        attempted here (SECURITY.md carries what remains).
    #      * `reversible_only` — half of the `financial` floor — has no enforcement point
    #        anywhere in `decima/`. What this clause guarantees for it is PRESENCE, not
    #        semantics: every live `financial` grant is now known to declare it, so the day a
    #        reader lands there is no fleet of grants silently exempt from it. Saying it
    #        "enforces reversibility" would be a lie.
    #
    #    Pure and replayable: `MORTA_FLOORS` is a constant table and `effect` is folded
    #    content, so the same log yields the same verdict on every peer (Law 5).
    caveats = cap.content.get("caveats", {})
    effect = cap.content.get("effect")
    floor = morta_floor(effect) if isinstance(effect, str) else {}
    unfloored = sorted(k for k, v in floor.items() if v and not caveats.get(k))
    if unfloored:
        return (
            False,
            f"grant for the Morta-gated effect class {effect!r} is missing its permanent "
            f"floor {unfloored}: the realm's minimum caveats for an effect are a property of "
            "the effect, not of whoever minted the grant",
            DenialCode.MORTA_FLOOR_MISSING,
        )
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
        elif _is_set_valued(v) and _is_set_valued(caveats.get(k)):
            # SET-VALUED caveats INTERSECT, for the same reason numeric ones take a `min`:
            # this constructor must be structurally incapable of producing a widening child.
            # A plain assignment here would let a caller "narrow" a `[a]` parent to `[a, b]`
            # and rely on `attenuation_valid` to catch it downstream — a constructor that can
            # build an invalid object and a validator that rejects it is one refactor away
            # from a hole. Sorted for determinism: caveats are hashed into the cell id.
            caveats[k] = sorted(set(v) & set(caveats[k]))
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
    verify_sig: Callable[[str, str, str], bool] | None = None,
) -> tuple[bool, str]:
    """Verify a proof before its INVOKE is written. Binds key-possession to the
    exact request, then runs the full ocap check (envelope, grantee, delegation,
    caveats — including the time-locked/single-use LEASE caveats, evaluated at the
    logical frontier `now` with `prior_uses` folded from the Weave).

    `verify_sig(pid, message, sig) -> bool` optionally REPLACES the keyring check of the
    holder's possession signature. Default None = `keyring.verify` (unchanged for every
    existing caller). The acceptance gate (`decima/kernel/acceptance.py`) passes the
    Weft's ROTATION-AWARE verifier so a proof made by an author enrolled on a succession
    chain is checked against the key valid AT that event's point — the same key that
    signed the event — instead of being refused. It replaces only the possession check;
    every authority check below is unchanged."""
    holder = cast("str | None", proof.get("holder"))
    if holder != agent_cell.content.get("principal"):
        return False, "holder is not the acting agent"
    expect = invocation_bind(verb, body, nonce, parents)
    if proof.get("invocation_bind") != expect:
        return False, "invocation bind mismatch (replayed or altered request)"
    check_sig = verify_sig if verify_sig is not None else keyring.verify
    if not check_sig(cast(str, holder), expect, proof.get("holder_sig", "")):
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
