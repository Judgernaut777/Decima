"""SELF-UPDATE — Decima updates its OWN code through the attested promotion spine.

An OS that can grow its own organs (the forge-real loop, Phase 3) must also be able
to update ITSELF — and the moment "install a new version of me" becomes a code path,
that path is the single most attractive target in the system: whoever can flip the
active-version pointer owns everything downstream. So this lane pins the law:

  AN UNATTESTED UPDATE CAN NEVER GO LIVE.

There is no second install path. A new version of a named component rides the SAME
spine every forged organ rides:

  • `propose_update` — Nona synthesizes the update's manifest (`forge.synthesize_
    manifest`, an honest description that GRANTS NOTHING) and authors the new
    version as an ExtensionCandidate (`candidate.author_candidate`) — BORN
    QUARANTINED (§3), default-deny: proposing an update activates nothing. The
    generated source is DATA (untrusted-is-data) until evaluated and promoted.

  • `promote_update` — the candidate goes through `promotion.promote`: the tier's
    REQUIRED SIGNER attests, and the weave fold lifts quarantine ONLY for a trusted
    promoter (§7). A missing/failed evaluation raises `PromotionBlocked`; a wrong
    signer produces NO lift and this module converts that into `PromotionBlocked`
    too — fail closed, never a half-promoted version. The promoted version is
    registered (`promotion.register_version`, an int version) and an
    `update_promotion` record Cell lands as audit.

  • `activate` — moving the ACTIVE-VERSION POINTER is the outward, irreversible
    act, so it is MORTA-GATED: a real INVOKE of a `requires_approval` capability
    through the full ocap spine (deny until `k.approve`). Before the gate is even
    consulted, `_require_promoted` re-derives the candidate's promotion FROM THE
    WEAVE — the capability cell's quarantine must have been lifted by a trusted
    promoter's attestation. A version that was never promoted CANNOT be activated,
    no matter who asks. The pointer itself is an LWW `active_version` Cell: a move
    is a NEW assertion on the same content-addressed cell; the old version's Cells
    stay on the Log forever (Law 1).

  • `rollback` — because the pointer is append-only and nothing is ever deleted,
    rollback is just moving the pointer BACK to the immediately-prior active
    version, which is still present (and still promoted) on the Log. Same Morta
    gate: undoing an update is as outward as applying one.

  • `active` / `active_record` / `history` — pure folds of the pointer cell: the
    current int version, its full record, and the append-only pointer history.

Laws upheld: everything on the Weft (the pointer moves by a new Cell, never an
edited row); zero ambient authority (the ONLY authority path is promotion.promote +
the Morta-gated activation INVOKE — proposing/describing an update confers nothing);
untrusted-is-data (goal + generated source recorded as data, evaluated before they
can run); ints-not-floats (versions are ints, rejected at the door); fail closed +
deterministic (offline, logical time, no clock; every refusal raises before any
pointer moves). Pure stdlib; composes forge/candidate/promotion/model/kernel public
APIs only — no core edit.
"""
from decima import executor
from decima import forge
from decima import candidate as C
from decima import promotion as P
from decima.promotion import PromotionBlocked
from decima.hashing import content_id, nfc
from decima.model import assert_content, assert_edge
from decima.weft import ASSERT

# Cell types this lane lays on the Weft (audit + the pointer itself).
UPDATE_PROPOSAL = "update_proposal"
UPDATE_PROMOTION = "update_promotion"
ACTIVE_VERSION = "active_version"

# The Morta gate: activating (or rolling back) a version is an outward act, gated by
# a real requires_approval capability INVOKE — never a bare function call.
ACTIVATE_CAP = "selfupdate.activate"
ACTIVATE_EFFECT = "selfupdate.activate"


class SelfUpdateError(Exception):
    """Malformed self-update input (a float version, an unknown component, nothing
    to roll back to) — refused loud at the door, before anything lands."""


class ActivationDenied(Exception):
    """The Morta gate refused the pointer move (no live approval / revoked cap).
    The active version is unchanged — fail closed."""


def _int(x, what) -> int:
    """Ints-not-floats at the door: versions/ticks are ints, never floats/bools."""
    if not isinstance(x, int) or isinstance(x, bool):
        raise SelfUpdateError(f"{what} must be an int (ints-not-floats), got {x!r}")
    return x


def _activation_effect(impl, args):
    """Executor handler for the activation effect. Pure record-keeping — ALL
    enforcement lives in authorize() (requires_approval) gating the INVOKE that
    reaches it, plus `_require_promoted` before the gate is consulted."""
    return {"out": {"op": args.get("op"), "name": args.get("name"),
                    "version": args.get("version")}}


executor.register(ACTIVATE_EFFECT, _activation_effect)


def _pointer_id(name: str) -> str:
    """The content-addressed id of a component's active-version pointer Cell —
    one stable identity per name, so every move is a new version of the SAME cell."""
    return content_id({"active_version": nfc(str(name))})


def activation_cap(k) -> str:
    """Mint (idempotently — content-addressed) the Morta-gated activation capability
    and grant it downhill to the decima orchestrator. It carries `requires_approval`,
    so every pointer move is denied until a human/Morta `k.approve`s it. Minted only
    through the existing kernel APIs (`k._assert_cap` + `k.grant`)."""
    cap_id = k._assert_cap(ACTIVATE_CAP, ACTIVATE_EFFECT,
                           caveats={"requires_approval": True})
    k.grant(cap_id, k.decima_agent_id)
    return cap_id


# ── propose: a candidate new version, born quarantined (default-deny) ───────────
def propose_update(k, name, goal, codegen, *, version,
                   effect_class="workspace_write", seed=49) -> dict:
    """Propose a new version of component `name` from `goal` via an INJECTED
    deterministic `codegen` (the same seam the forge-real loop uses — a model's
    output is DATA, never trusted instruction).

    Composes Nona's forge (`forge.synthesize_manifest` — the update's honest,
    discoverable description; it GRANTS NOTHING) and `candidate.author_candidate`
    (the ExtensionCandidate spine — BORN QUARANTINED, §3). The proposal is
    DEFAULT-DENY: it is NOT active, NOT promoted, holds no grant; `active(k, name)`
    is unchanged. A `update_proposal` audit Cell lands with provenance to the
    candidate. `version` is an int — a float is refused at the door."""
    name = nfc(str(name))
    version = _int(version, "version")
    goal = nfc(str(goal))
    # Nona synthesizes the manifest DESCRIBING the update — data, not authority.
    m = forge.synthesize_manifest(goal, name=f"{name}.v{version}")
    cand = C.author_candidate(k, goal, codegen, name=f"{name}.v{version}",
                              declared_effect_class=effect_class, seed=seed)
    live = k.weave().get(cand["cell"])
    if live is None or live.content.get("quarantined") is not True:
        raise SelfUpdateError(
            "a proposed update must be BORN QUARANTINED (default-deny) — refusing")
    pid = content_id({"update_proposal": name, "version": version,
                      "impl": cand["implementation_digest"]})
    assert_content(k.weft, k.reckoner.id, pid, UPDATE_PROPOSAL, {
        "name": name,
        "version": version,                            # int, never a float
        "goal": goal,                                  # recorded as DATA
        "instruction_eligible": False,                 # a goal/source is data, never obeyed
        "candidate": cand["cell"],
        "implementation_digest": cand["implementation_digest"],
        "manifest": m,                                 # the forged description (grants nothing)
        "status": "PROPOSED",
        "quarantined": True,
        "active": False,                               # default-deny: proposing activates nothing
    })
    assert_edge(k.weft, k.reckoner.id, pid, "proposes", cand["cell"])
    return {"proposal": pid, "candidate": cand, "name": name, "version": version}


# ── promote: through the attested promotion spine — fail closed ─────────────────
def promote_update(k, upd, evaluation, *, tier=None, signer_principal=None) -> dict:
    """Promote a proposed update through `promotion.promote` — the tier's required
    signer ATTESTs, and the weave fold lifts quarantine ONLY for a TRUSTED promoter
    (§7). Fail closed twice over:

      • a missing/failed evaluation ⇒ `promotion.promote` raises `PromotionBlocked`;
      • a wrong/untrusted signer ⇒ the fold does NOT lift, and this function raises
        `PromotionBlocked` too — no `update_promotion` record lands, so the version
        can never be activated.

    On success the version is registered (`promotion.register_version`, int) and an
    `update_promotion` audit Cell + `promotes_version` edge land on the Weft."""
    name = nfc(str(upd["name"]))
    version = _int(upd["version"], "version")
    res = P.promote(k, upd["candidate"], evaluation, tier=tier,
                    signer_principal=signer_principal)
    if not res.promoted:
        raise PromotionBlocked(
            f"update {name!r} v{version}: the promote-ATTEST did not lift quarantine "
            f"(signer is not a trusted promoter for tier {res.tier!r}) — fail closed")
    mid = P.register_version(k, name, version)
    rid = content_id({"update_promotion": name, "version": version, "cap": res.cap_id})
    assert_content(k.weft, k.reckoner.id, rid, UPDATE_PROMOTION, {
        "name": name,
        "version": version,                            # int
        "cap": res.cap_id,
        "tier": res.tier,
        "signer": res.signer,
        "state": res.to_state,
        "evaluation": res.evaluation,
        "manifest": mid,
        "proposal": upd.get("proposal"),
    })
    assert_edge(k.weft, k.reckoner.id, rid, "promotes_version", res.cap_id)
    return {"record": rid, "cap": res.cap_id, "name": name, "version": version,
            "tier": res.tier, "state": res.to_state, "manifest": mid}


# ── the attestation gate activate/rollback re-derive from the Weave ─────────────
def _require_promoted(w, cap_id, name, version):
    """Re-derive, FROM THE WEAVE, that this version's capability was genuinely
    promoted — its quarantine lifted by a trusted promoter's attestation (§7) and
    its cell still live. A record/claim is DATA; only the fold's verdict counts."""
    cap = w.get(cap_id) if cap_id else None
    if cap is None or cap.retracted or cap.content.get("quarantined") is not False:
        raise PromotionBlocked(
            f"update {name!r} v{version} is NOT promoted — its capability's quarantine "
            "was never lifted by a trusted attestation; an unattested update cannot go live")
    return cap


def _find_promoted(w, name, version):
    """The `update_promotion` record for (name, version) whose capability passes
    `_require_promoted`. No record ⇒ the version was never promoted ⇒ refuse."""
    recs = [c for c in w.of_type(UPDATE_PROMOTION)
            if c.content.get("name") == name and c.content.get("version") == version]
    if not recs:
        raise PromotionBlocked(
            f"update {name!r} v{version} was never promoted (no attested promotion "
            "record) — an unattested update cannot go live")
    last_err = None
    for rec in recs:
        try:
            return rec, _require_promoted(w, rec.content.get("cap"), name, version)
        except PromotionBlocked as e:
            last_err = e
    raise last_err


def _gated_pointer_move(k, op, name, version, cap_id, prev, promotion_cell,
                        extra=None) -> dict:
    """The ONE way the pointer moves: a Morta-gated INVOKE (requires_approval —
    denied until approved), then a NEW `active_version` assertion on the same
    content-addressed pointer cell. The old version's Cells stay on the Log."""
    acap = activation_cap(k)
    agent = k.weave().get(k.decima_agent_id)
    res = k.invoke(agent, acap, {"op": op, "name": name, "version": version})
    if "denied" in res:
        raise ActivationDenied(
            f"{op} of {name!r} v{version} refused by the Morta gate: {res['denied']}")
    pid = _pointer_id(name)
    content = {
        "name": name,
        "version": version,                            # int
        "cap": cap_id,
        "prev": prev,                                  # int | None — the rollback target
        "promotion": promotion_cell,
        "op": op,
        "invoke": res["invoke_event"],                 # audit: the gated act itself
        "receipt": res["result_cell"],
    }
    content.update(extra or {})
    assert_content(k.weft, k.decima.id, pid, ACTIVE_VERSION, content)
    assert_edge(k.weft, k.decima.id, pid, "activates", cap_id)
    return {"pointer": pid, "name": name, "version": version, "cap": cap_id,
            "prev": prev, "op": op}


# ── activate: Morta-gated; a never-promoted version CANNOT go live ──────────────
def activate(k, name, version) -> dict:
    """Move the active-version pointer to a PROMOTED version of `name`. Two gates,
    both fail-closed, both BEFORE the pointer moves:

      1. ATTESTATION — the version must have a genuine promotion, re-derived from
         the Weave (`_require_promoted`): quarantine lifted by a trusted signer.
         Never promoted / forged record / retracted cap ⇒ `PromotionBlocked`.
      2. MORTA — the move is a real INVOKE of the `requires_approval` activation
         capability; without a live approval it is denied ⇒ `ActivationDenied`.

    The pointer is an LWW Cell: activation asserts a NEW version of the same cell;
    the previous version's Cells stay on the Log (rollback stays possible)."""
    name = nfc(str(name))
    version = _int(version, "version")
    w = k.weave()
    rec, cap = _find_promoted(w, name, version)
    prev = active(k, name)
    return _gated_pointer_move(k, "activate", name, version, cap.id, prev, rec.id)


# ── folds: the current version + the append-only history ────────────────────────
def active_record(k, name) -> dict | None:
    """The active-version pointer's CURRENT content (a pure fold), or None."""
    cell = k.weave().get(_pointer_id(nfc(str(name))))
    if cell is None or cell.retracted or not cell.content:
        return None
    return dict(cell.content)


def active(k, name):
    """The currently active int version of `name` (a pure fold), or None."""
    rec = active_record(k, name)
    return rec.get("version") if rec else None


def history(k, name) -> list:
    """The FULL append-only pointer history of `name`, oldest first — every
    activate/rollback ever asserted on the pointer cell. Nothing is deleted; a
    pointer move only ever ADDS an entry (Law 1: the log is the truth)."""
    pid = _pointer_id(nfc(str(name)))
    out = []
    for ev in k.weft.events():
        body = ev.body if isinstance(ev.body, dict) else {}
        if ev.verb == ASSERT and body.get("cell") == pid \
                and body.get("type") == ACTIVE_VERSION:
            out.append(dict(body.get("content") or {}))
    return out


# ── rollback: move the pointer BACK — the prior version is still on the Log ─────
def rollback(k, name) -> dict:
    """Move the pointer back to the immediately-prior active version. Because the
    pointer is append-only and versions are never deleted, the prior version — its
    candidate, promotion record, and promoted capability — is STILL on the Log;
    rollback re-verifies its attestation (`_require_promoted`) and passes the SAME
    Morta gate as activate (undoing an update is as outward as applying one).
    Restores exactly the prior version (version, cap, and its own `prev`)."""
    name = nfc(str(name))
    hist = history(k, name)
    if not hist:
        raise SelfUpdateError(f"no active version for {name!r} — nothing to roll back")
    current = hist[-1]
    prev_version = current.get("prev")
    if prev_version is None:
        raise SelfUpdateError(
            f"{name!r} v{current.get('version')} has no prior version — nothing to roll back")
    prior = next(e for e in reversed(hist)
                 if e.get("version") == prev_version and e.get("cap"))
    w = k.weave()
    cap = _require_promoted(w, prior["cap"], name, prev_version)
    return _gated_pointer_move(
        k, "rollback", name, _int(prev_version, "version"), cap.id,
        prior.get("prev"), prior.get("promotion"),
        extra={"rolled_back_from": current.get("version")})
