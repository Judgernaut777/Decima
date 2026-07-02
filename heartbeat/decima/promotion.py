"""Trusted, tiered promotion + canary + versioning + rollback (NONA_RECKONER §7–§10).

Stage B's Reckoner produces EVIDENCE — an EvaluationResult Cell with a promote-eligibility
verdict. This module turns that evidence into a governed lifecycle transition, WITHOUT ever
mutating candidate code (§7): a promotion GRANTS A NEW, still-attenuated capability EDGE to
the immutable `implementation_digest`. The generated source is referenced by digest; it is
never rewritten.

The trust spine is DATA on the Weft (weave.py folds it):

  • `install_trust_anchors(k)` root-asserts the `promoter` cells that name which principals
    may sign which effect-class TIERS (§7). The fold honors ONLY root-declared anchors, so a
    principal cannot self-grant promotion authority.

  • `promote(k, candidate, evaluation, tier=…)` builds the capability (born quarantined),
    then has the TIER's required signer ATTEST the promotion. The weave fold lifts quarantine
    ONLY if that signer is trusted for the tier — pure/read-only ⇒ the automated Reckoner
    (Nona); reversible workspace_write ⇒ automated promote + canary; network ⇒ a HUMAN
    attestation; financial ⇒ Morta. Promotion never strips Morta's approval caveat.

  • `monitor_canary` folds canary health (weave.canary_health) and ACTS: a threshold breach
    asserts a suspension proposal (→ SUSPENDED); a high-severity finding auto-revokes the
    lease under a pre-authorized Morta policy (§8).

  • `supersede` registers a new manifest version (latest-wins, §10); `rollback` RETRACTs the
    promotion/grant edges and asserts an incident Cell — it never claims to undo an external
    effect (§9).

Ints (not floats) in signed content: traffic fraction, expiry, versions are ints.
Public kernel/model/hashing/capability API only — the sole core edit this cycle is weave.py.
"""
from decima.weft import ASSERT, ATTEST
from decima.capability import capability_content
from decima.hashing import content_id
from decima.model import assert_content, assert_edge
from decima.manifest import capability_manifest, register as register_manifest
from decima import reckoner as R

CAPABILITY = "capability"
PROMOTER = "promoter"
INCIDENT = "incident"
SUSPENSION = "suspension"

# §7 required signer by declared effect-class tier (least → most power). The role names
# resolve to principals via `signer_for`; the tiers each anchor covers are declared in
# `install_trust_anchors` and folded by weave._is_trusted_promoter.
SIGNER_ROLE = {
    "pure": "reckoner",           # automated trusted Reckoner (Nona) may promote
    "read_only": "reckoner",      # automated
    "workspace_write": "reckoner",  # automated promote + canary + rollback
    "network": "human",           # network/production mutation ⇒ human attestation required
    "financial": "morta",         # financial/identity/destructive ⇒ Morta's permanent gate
}
# Tiers each ROLE may sign (a superset chain: Morta ⊇ human ⊇ reckoner). Declared on the
# Weft as `promoter` cells; the fold checks membership.
ROLE_TIERS = {
    "reckoner": ["pure", "read_only", "workspace_write"],
    "human": ["pure", "read_only", "workspace_write", "network"],
    "morta": ["pure", "read_only", "workspace_write", "network", "financial"],
}


class PromotionBlocked(Exception):
    """Promotion refused BEFORE any lift — the evidence gate said the candidate is not
    promote-eligible (a deterministic failure, §4/§5). No capability is exposed."""


class PromotionResult:
    def __init__(self, cap_id, tier, signer, to_state, promoted, evaluation):
        self.cap_id = cap_id
        self.tier = tier
        self.signer = signer
        self.to_state = to_state
        self.promoted = promoted        # True iff the weave fold actually lifted quarantine
        self.evaluation = evaluation

    def __str__(self):
        v = f"{self.to_state} ✓" if self.promoted else "NOT PROMOTED ✗ (untrusted signer)"
        return f"[promotion] tier={self.tier} → {v}"


def signer_for(k, role: str) -> str:
    """Resolve a §7 signer role to a principal in this Kernel. Morta is the realm
    authority (root); the human tier is the operator principal; the reckoner is Nona."""
    return {"reckoner": k.reckoner.id, "human": k.human.id, "morta": k.root.id}[role]


def install_trust_anchors(k) -> list:
    """Root-declare the trusted-promoter anchors (§7) as `promoter` cells on the Weft.
    Idempotent (content-addressed by principal+role). ONLY these root-asserted cells are
    honored by the fold — a principal that self-asserts a `promoter` cell is ignored."""
    ids = []
    for role, tiers in ROLE_TIERS.items():
        pr = signer_for(k, role)
        cid = content_id({"promoter": pr, "role": role})
        k.weft.append(k.root.id, ASSERT, {
            "cell": cid, "type": PROMOTER,
            "content": {"principal": pr, "role": role, "tiers": list(tiers)},
        })
        ids.append(cid)
    return ids


def build_capability(k, candidate, tier, *, canary=False, expires_at=None,
                     traffic_fraction=100, name=None) -> tuple[str, dict]:
    """Build the capability that a promotion will expose — BORN QUARANTINED (§3), its
    impl an EDGE to the immutable `implementation_digest` (§7). The generated source is
    referenced by digest; it is NEVER mutated. Effect is `generated_code`, so an INVOKE
    runs the candidate's source ONLY inside isolation.spawn_worker (footprint bound).

    Canary caveats (§8) — traffic fraction, read-only, a tight `expires_at` lease — are
    ints and TIGHTEN the eventual gate; they never widen it."""
    src = candidate["source_blobs"]
    entry = candidate.get("entrypoint") or R._entrypoint(src)
    digest = candidate["implementation_digest"]
    name = name or candidate["name"]
    impl = {"source_blobs": src, "entrypoint": entry, "limits": {"cpu_seconds": 2}}
    caveats = {"sandbox_only": True}
    # A financial/destructive candidate carries Morta's UNSTRIPPABLE approval floor — it
    # survives promotion (weave lift strips only sandbox_only), so even a promoted
    # financial cap still needs a human approval to invoke (Morta + human, §7).
    if tier == "financial":
        caveats["requires_approval"] = True
    if canary:
        caveats.update({"canary": True, "traffic_fraction": int(traffic_fraction),
                        "read_only": True, "shadow": False})
        if expires_at is not None:
            caveats["expires_at"] = int(expires_at)   # tight lease (int frontier), §8
    content = capability_content(name=name, effect="generated_code", impl=impl,
                                 caveats=caveats, quarantined=True)
    content["declared_effect_class"] = tier          # the fold reads the tier here
    content["implementation_digest"] = digest        # the immutable handle the edge grants
    content["candidate"] = candidate["cell"]
    content["lifecycle"] = "BUILT"
    cap_id = content_id({"promoted_cap": name, "impl": digest, "tier": tier,
                         "canary": bool(canary)})
    assert_content(k.weft, k.reckoner.id, cap_id, CAPABILITY, content)
    # Provenance: the capability's authority is an EDGE to the immutable impl/candidate.
    assert_edge(k.weft, k.reckoner.id, cap_id, "impl_of", candidate["cell"])
    return cap_id, content


def promote(k, candidate, evaluation, *, tier=None, canary=False, expires_at=None,
            traffic_fraction=100, signer_principal=None) -> PromotionResult:
    """Promote an EVALUATED candidate under the §7 tiered policy. The tier's required
    SIGNER ATTESTs `promote:True`; the weave fold lifts quarantine ONLY if that signer is
    a trusted promoter for the tier (defense in depth — a wrong signer cannot lift, even
    hand-rolled). Wires on top of Stage B's promote-eligibility verdict + evidence.

    Raises PromotionBlocked when the evidence gate fails (no fabricated success, §4)."""
    tier = tier or candidate["content"].get("declared_effect_class") or "pure"
    if tier not in SIGNER_ROLE:
        raise ValueError(f"unknown tier {tier!r}")
    if not getattr(evaluation, "promote_eligible", False):
        raise PromotionBlocked(
            f"candidate not promote-eligible: {getattr(evaluation, 'reason', '')}")
    role = SIGNER_ROLE[tier]
    signer = signer_principal or signer_for(k, role)
    # workspace_write and any outward tier go through canary first (§7/§8).
    canary = canary or tier in ("workspace_write", "network", "financial")
    cap_id, _content = build_capability(k, candidate, tier, canary=canary,
                                        expires_at=expires_at,
                                        traffic_fraction=traffic_fraction)
    to_state = "CANARY" if canary else "PROMOTED"
    k.weft.append(signer, ATTEST, {
        "target_cell": cap_id,
        "claim": f"promote {candidate['name']} tier={tier} → {to_state}",
        "promote": True, "from_state": "EVALUATED", "to_state": to_state,
        "evaluation": evaluation.result_cell, "tier": tier,
    })
    live = k.weave().get(cap_id)
    promoted = live.content.get("quarantined") is False
    if promoted:
        # Lifecycle is provenance, not an edited row: re-assert with the reached state.
        k.weft.append(k.reckoner.id, ASSERT, {
            "cell": cap_id, "type": CAPABILITY,
            "content": {**live.content, "lifecycle": to_state}})
        assert_edge(k.weft, signer, cap_id, "evaluated_by_result", evaluation.result_cell)
    return PromotionResult(cap_id, tier, signer, to_state, promoted, evaluation.result_cell)


def grant_to(k, cap_id, agent_id):
    """Expose a promoted capability to another agent — a real, downhill grant (the ocap
    spine still gates every INVOKE)."""
    k.grant(cap_id, agent_id)


# ── canary monitoring (§8) ────────────────────────────────────────────────────────
def _incident(k, cap_id, reason, detail=None) -> str:
    iid = content_id({"incident": cap_id, "reason": reason, "at": k.weft.head})
    assert_content(k.weft, k.root.id, iid, INCIDENT, {
        "capability": cap_id, "reason": reason, "detail": detail or {},
        "note": "contains + compensates; never claims to undo an external effect (§9)"})
    assert_edge(k.weft, k.root.id, iid, "incident_for", cap_id)
    return iid


def monitor_canary(k, cap_id, *, max_failures=0) -> dict:
    """Fold canary health and ACT (§8). A HIGH-severity security finding triggers
    AUTOMATIC lease revocation under a pre-authorized Morta policy (revoke + incident). A
    threshold breach asserts a SUSPENSION proposal and moves the cap to SUSPENDED (fail
    closed: the grant is revoked so the next INVOKE is denied). Healthy ⇒ no action."""
    w = k.weave()
    health = w.canary_health(cap_id, max_failures=max_failures)
    out = {"health": health, "action": None}
    if health["high_findings"]:
        k.revoke(cap_id)                              # Morta pre-authorized auto-revoke
        out["incident"] = _incident(
            k, cap_id, "canary auto-revoke: high-severity security finding", health)
        out["action"] = "revoked"
        return out
    if health["breach"]:
        cell = w.get(cap_id)
        sid = content_id({"suspension": cap_id, "at": k.weft.head})
        assert_content(k.weft, k.root.id, sid, SUSPENSION, {
            "capability": cap_id, "reason": "canary threshold breach",
            "health": health, "from_state": "CANARY", "to_state": "SUSPENDED"})
        assert_edge(k.weft, k.root.id, sid, "suspends", cap_id)
        # Move to SUSPENDED (provenance) and fail closed by revoking the grant.
        k.weft.append(k.root.id, ASSERT, {
            "cell": cap_id, "type": CAPABILITY,
            "content": {**cell.content, "lifecycle": "SUSPENDED"}})
        k.revoke(cap_id)
        out["suspension"] = sid
        out["action"] = "suspended"
    return out


# ── versioning / supersede (§10) ──────────────────────────────────────────────────
def register_version(k, name, version) -> str:
    """Register a promoted capability's manifest at an explicit int version. The registry
    is latest-wins (manifest.registry), so a higher version SUPERSEDES the incumbent."""
    m = capability_manifest(
        name, description=f"promoted generated capability {name}", archetype="COMPUTE",
        effect_class="READ", source="promoted", version=int(version),
        tags=["promoted", "generated"])
    return register_manifest(k, m, author=k.reckoner.id)


def supersede(k, name, old_cap_id, new_cap_id, *, version) -> dict:
    """§10: a new candidate for an existing name registers at a HIGHER manifest version
    (latest-wins) and SUPERSEDES the incumbent — the incumbent cap is tombstoned and
    points forward to the replacement (never erased). Differential-regression gating is
    upstream: `promote` refuses a candidate whose EvaluationResult failed the differential
    stage, so only a non-regressing new version ever reaches here."""
    mid = register_version(k, name, version)
    k.supersede(old_cap_id, replacement=new_cap_id)   # SUPERSEDE: tombstone + forward pointer
    return {"manifest": mid, "old": old_cap_id, "new": new_cap_id, "version": int(version)}


# ── rollback (§9) ─────────────────────────────────────────────────────────────────
def rollback(k, cap_id, *, reason="promotion rolled back") -> dict:
    """§9: rollback = RETRACT the promotion/grant edges (the capability cell), so its
    authority cascade fails closed and the next INVOKE is denied — plus assert an incident
    Cell linking the affected capability. It NEVER claims to undo an external effect; it
    contains and compensates."""
    k.revoke(cap_id)                                  # RETRACT (WITHDRAW) → cascade fails closed
    inc = _incident(k, cap_id, reason)
    return {"capability": cap_id, "incident": inc}
