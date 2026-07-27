"""Who may ASSERT — and who may RETRACT — a cell that AUTHORITY is read from (N7 / R1).

`Weft.append` validates the verb, derives the causal clock, content-addresses the
payload and signs it — and, until this module, nothing else. `ingest` said so outright:
*"Authority is NOT re-judged here."* Combined with the fact that every check
`capability.authorize` makes is a READ of the folded graph, that left the widest hole in
the trust model: any principal whose key the keyring holds could ASSERT

    capability  {quarantined: False, parent: None, grantee: self, granter: self}
    agent       {principal: self, envelope: [that capability]}

and `authorize` returned `(True, "ok")` — not because a check was skipped, but because
the attacker wrote both sides of every check. The promotion gate (NONA_RECKONER §7)
guarded the ATTEST path; the ASSERT path is the wider one and was unguarded.

WHAT THIS MODULE IS. One pure function — `refusal` — over `(cell_type, content, author,
root)`. It reads no log, folds nothing, holds no state, and returns either None
(permitted) or a sentence naming the refusal. That shape is deliberate: the SAME
predicate is evaluated at three places that must never disagree —

  * `Weft.append`            — fail closed at the door, so a local write that the fold
                               would distrust is never recorded at all (the precedent is
                               the rotation check: "refused, nothing recorded");
  * `Weft.ingest`            — the same rule at the acceptance gate, judged at the
                               event's CAUSAL FRONTIER (`acceptance.recheck_assert_authority`),
                               because an event reaches a peer's log through sync, not
                               through `append`, and a door-only rule buys nothing across
                               sync;
  * the FOLD and the READ    — `Weave.cell_asserted_by` names who asserted each guarded
                               cell and `capability.verify_delegation` /
                               `Weave._cascade_retractions` refuse to derive authority
                               from a cell whose asserter had no business asserting it.

The fold/read pass is the actual security boundary. A write door protects only what THIS
process writes NOW: it does nothing for a log already on disk, a restored backup, or a
peer's forgery. This module is the shared rule; `weave.py` and `capability.py` are where
it is enforced against history.

ONE RULE THIS MODULE CANNOT CARRY, and where it lives instead. Choosing which of a guarded
cell's CONCURRENT assertions the realm materializes is also an authority decision — an
adjudication ATTEST (MERGE_SEMANTICS §4) that supersedes root's head hands the cell's content
to whoever wrote the surviving branch. That rule cannot be expressed here: this predicate is
pure over one body, and deciding it needs the folded head set and each head's author, which
`Weft.append` may not compute (it holds the store lock) and which no single ASSERT body
carries. It therefore lives ONLY in the fold — `Weave._may_supersede_head`, paired with
`cell_asserted_by` deriving its answer from the head that actually materialized. Same
asymmetry as the `capability` clause below, and the same justification: refusing to DERIVE
authority is the security property, refusing the write is hygiene. There is also a positive
reason not to mirror it at the gate: an ATTEST the fold declines to honour is still recorded
as an attestation, which is precisely how the promote-ATTEST already fails closed
(NONA_RECKONER §7 — "recorded as evidence but does NOT lift quarantine"). A rejection would
throw that evidence away.

THE RULE, per guarded type — "only the cell's granter chain, or root, may assert it":

  `capability`  the ASSERT's author must be the grant's own `granter`, or the realm ROOT.
                You may hand on authority only IN YOUR OWN NAME: a grant claiming that
                someone else issued it is refused, which is what makes `granter` a field
                the rest of the chain walk can believe.

                This door rule is deliberately WEAKER than what the fold enforces, and the
                asymmetry is the point. A hostile principal CAN still name itself `granter`
                of its own root grant and get that cell onto the log. What it cannot do is
                make the cell confer anything, because `capability.verify_delegation`
                additionally requires a ROOT GRANT (no `parent`) to have been asserted by
                the realm ROOT — or by a principal ROOT ANCHORED as a promoter for the
                grant's declared tier, which is how the Reckoner legitimately mints organ
                grants (`services/nona/executor.py::build_capability`). That clause needs
                the folded promoter anchors, and `append`'s critical section may not run a
                fold — it holds the store lock — so the door decides the half it can decide
                from the body alone and the fold decides the rest. Refusing to DERIVE
                authority is the security property; refusing the write is hygiene.
  `promotion`   the author must be the `signer` the record names. N4's derived quarantine
                reads `promotion.content['signer']` to decide whether to lift quarantine
                and strip `sandbox_only`, and never asked who WROTE that record — so any
                key-holder could forge `{capability: <real cap>, tier: 'pure',
                signer: <the reckoner's pid>}` and lift a real quarantine. This closes it.
  `promoter`    ROOT only. The fold already filtered a non-root promoter anchor at READ
                time (`_is_trusted_promoter`); refusing the write too means the log does
                not accumulate anchors that are inert-but-alarming.
  `agent`       an agent cell that confers the SANDBOX privilege (`sandbox` truthy) may be
                asserted only by ROOT. The sandbox principal is the quarantine runtime: it
                is the one flag that makes a QUARANTINED capability invocable and satisfies
                the `sandbox_only` Morta caveat, so a principal that could mint its own
                sandbox agent would promote itself out of quarantine by declaration.
                Ordinary agent cells are NOT authorship-bound — see the residual below.

WHAT IS DELIBERATELY NOT REFUSED, and why (the honest residual — SECURITY.md carries it):

  * An ORDINARY `agent` cell may be asserted by any principal. The powerbox is why: a
    broker issuing a grant must APPEND it to the requesting agent's envelope
    (`services/nona/powerbox.py::issue`), and that agent cell was created by someone else.
    Binding agent authorship to its creator would refuse the one legitimate cross-principal
    envelope write in the product. The escalation this leaves open is bounded by the
    capability rule instead: a self-asserted envelope can only name grants that already
    trace to root, and `authorize` still requires the grant to name the acting principal as
    its `grantee`.
  * A capability that names NO `grantee` (`capability_content`'s default) is usable by any
    principal that can name it in an envelope — `authorize_detail` refuses only when
    `grantee is not None`. That is a separate hole from authorship and no authorship rule
    closes it.
  * A `TYPE_DEF` ASSERT is not one of the guarded types, so any principal may redeclare a
    guarded type's MERGE CLASS. That fails closed rather than open — a guarded cell that
    materializes outside a register has no single asserting head, so `cell_asserted_by`
    answers None and every authority read refuses — but it is a denial of service, and
    binding `TYPE_DEF` authorship would refuse the ordinary type declarations the runtime
    makes on every boot. SECURITY.md carries it.

DETERMINISM (Law 5). Nothing here reads a clock, a random source, or arrival order.
`root` is the caller's already-derived constitutional anchor (the author of the parentless
event with the smallest local `seq`; see `Weave._apply`), and every other input is folded
content. The same event set therefore yields the same verdict on every peer and every
replay. No content is added, removed or reshaped: this module only ever REFUSES, so every
byte that is hashed or signed is exactly what it was before N7 (fixture-safe by
construction).
"""

from __future__ import annotations

from typing import Any

# Cell types whose content the authorization path READS to decide who may do what. The
# names are restated here as literals — never imported from `decima/runtime/cells.py` or
# `decima/services/` — so the TCB keeps its import boundary
# (tests/architecture/test_import_boundaries.py) exactly as `acceptance.AGENT` does.
CAPABILITY = "capability"
AGENT = "agent"
PROMOTER = "promoter"
PROMOTION = "promotion"

# The cheap prefilter every enforcement site takes FIRST: an ASSERT of any other type is
# two dict lookups away from the door, with no fold and no crypto (the same shape as
# `Weft._rot_apply`'s `body["type"] != "key_rotation"` screen).
GUARDED_TYPES: frozenset[str] = frozenset({CAPABILITY, AGENT, PROMOTER, PROMOTION})

# Terminal refusal codes. `Weft.ingest` returns them as `rejected:<code>`; the event is
# NEVER inserted (fail closed). `Weft.append` raises `WeftError` instead — a local caller
# gets the sentence, a peer gets the code.
UNAUTHORIZED_ASSERT = "unauthorized-assert"

# The RETRACT counterpart (`retract_refusal`). There is deliberately no `append` clause for
# it — a RETRACT body names a `cell` id, not a type, so judging one means looking the target
# up, which the door cannot do under the store lock. `Weft.ingest` returns this code and the
# fold declines to honour the retraction; see `retract_refusal`.
UNAUTHORIZED_RETRACT = "unauthorized-retract"


def _principal(content: object, field: str) -> str | None:
    """The principal a guarded cell binds its authorship to, or None if it names none
    (absent, null, or not a string — a non-string binding is NEVER coerced: it simply
    fails to match any author, which fails closed)."""
    if not isinstance(content, dict):
        return None
    value = content.get(field)
    return value if isinstance(value, str) else None


def refusal(cell_type: str, content: object, author: str, root: str | None) -> str | None:
    """None if `author` may ASSERT this guarded cell; otherwise the refusal sentence.

    `root` is the realm's constitutional authority — the author of the genesis event
    (`Weave.genesis_author()` / `Weft.genesis_author()`). `root is None` means NO genesis
    is anchored in the view being judged: an empty log, or the frontier of a parentless
    event. There is no authority to usurp yet — whoever commits the first event BECOMES
    root — so the write is permitted here and judged again by the fold, where the anchor
    is known and a second parentless event can never displace the first (its `seq` is
    necessarily higher).

    Pure: no I/O, no clock, no randomness, no mutation. Never raises — a malformed
    content dict yields a refusal, never a traceback (an authority decision is never
    made by an exception handler)."""
    if cell_type not in GUARDED_TYPES:
        return None
    if root is None:
        return None
    if author == root:
        return None  # the constitutional authority may write its own realm

    if cell_type == PROMOTER:
        return (
            f"only the realm root may assert a `promoter` trust anchor; {author} is not "
            f"root ({root}). A self-declared promoter confers nothing at read time — "
            "this refuses the write as well, so the log holds no inert anchors."
        )

    if cell_type == PROMOTION:
        signer = _principal(content, "signer")
        if signer is None or author != signer:
            return (
                f"a `promotion` record must be asserted by the signer it names "
                f"(signer={signer!r}, author={author}). The fold lifts quarantine and "
                "strips `sandbox_only` on the strength of that field, so a record its "
                "signer did not write would forge a promotion."
            )
        return None

    if cell_type == CAPABILITY:
        granter = _principal(content, "granter")
        if granter is None or author != granter:
            return (
                f"a grant must be asserted by its own `granter` (granter={granter!r}, "
                f"author={author}): you may only hand on authority in your own name, and "
                "you may not write a grant that claims someone else issued it."
            )
        return None

    # AGENT — only the SANDBOX privilege is authorship-bound (see the module docstring:
    # the powerbox legitimately writes another agent's envelope, so ordinary agent cells
    # are deliberately not creator-bound).
    if cell_type == AGENT and isinstance(content, dict) and content.get("sandbox"):
        return (
            f"only the realm root may assert an agent cell carrying `sandbox`; {author} "
            f"is not root ({root}). The sandbox principal is the quarantine RUNTIME — it "
            "is what makes a quarantined capability invocable and satisfies the "
            "`sandbox_only` Morta caveat — so minting your own would be promoting "
            "yourself by declaration."
        )
    return None


def retract_refusal(
    cell_type: str,
    content: object,
    author: str,
    root: str | None,
    *,
    anchored_promoter: bool = False,
) -> str | None:
    """None if `author` may RETRACT this guarded cell; otherwise the refusal sentence.

    THE OTHER HALF OF N7. `refusal` above guards who may WRITE authority; this guards who
    may TAKE IT AWAY. Nothing guarded that half until now, so any key-holding principal
    could `RETRACT` root's capability and the fold applied it: `retracted = True`, plus the
    DERIVED_AUTHORITY cascade that fails closed every grant descending from it. Not an
    escalation — the attacker gains nothing — which is exactly why R1 got the attention and
    this did not. It is still a one-event, unauthenticated shutdown of any organ, any
    delegation subtree, and (via the promotion record) any promotion on the log.

    It also decides a question the canary cannot answer without it. `monitor.monitor_canary`
    suspends by retracting a promotion and revokes by retracting a capability; until the log
    can tell the monitor's retraction from a stranger's, "why did this organ stop?" is
    answerable from the events but "was whoever stopped it allowed to?" is not.

    WHO MAY TAKE BACK WHAT — the mirror of the assert rule, plus one addition:

      `promoter`    ROOT only, exactly as asserting one is root-only. Withdrawing a trust
                    anchor un-anchors every promoter decision downstream of it.
      `promotion`   the `signer` the record names, ROOT, or a root-ANCHORED PROMOTER for
                    the record's tier. The signer clause is what makes N4's "rollback is a
                    RETRACT" the promoter's own act; the anchored clause is what lets the
                    canary demote an organ it did not personally promote.
      `capability`  the grant's own `granter`, ROOT, or an anchored promoter for the grant's
                    declared tier. The granter clause is the ocap rule — you may take back
                    what you handed on. The anchored clause is why the Reckoner can revoke
                    an organ grant it minted, and why an auto-revoke on a HIGH finding has
                    a signature that means something.
      `agent`       only a cell carrying `sandbox` is bound, and to ROOT — the same
                    asymmetry as asserting one. Retracting an ordinary agent cell is
                    unguarded for the same reason asserting one is (the powerbox writes
                    envelopes it did not create).

    `anchored_promoter` is passed IN rather than derived here, because deciding it needs the
    folded promoter anchors and this predicate is pure over one cell. The one caller that can
    afford the fold — `Weave._cascade_retractions`, via `Weave._may_retract` — derives it and
    hands it over.

    THE FOLD IS THE *ONLY* ENFORCEMENT POINT FOR THIS RULE, which is a sharper claim than the
    assert rule makes, and deliberate. `Weft.append` cannot judge a RETRACT: the body names a
    `cell` id and not a type, so the door would have to look the target up, and it holds the
    store lock. Nor can the apply pass, since a concurrent branch may carry the target's
    ASSERT at a higher order than its RETRACT. And `Weft.ingest` deliberately does NOT gate it
    either — see the comment at that call site: because the door cannot refuse these, an
    ordinary honest log CONTAINS retractions the fold declines, and on a linear log every
    later event names them as parents, so refusing one at the acceptance gate would orphan the
    whole remainder of an honest peer's log (including the legitimate rollback that follows a
    forged one). A forged retraction is therefore RECORDED everywhere and HONOURED nowhere.

    Pure: no I/O, no clock, no randomness, no mutation, never raises."""
    if cell_type not in GUARDED_TYPES:
        return None
    if root is None:
        return None  # no anchored constitution to protect — see `refusal`
    if author == root:
        return None
    if anchored_promoter and cell_type in (CAPABILITY, PROMOTION):
        return None

    if cell_type == PROMOTER:
        return (
            f"only the realm root may retract a `promoter` trust anchor; {author} is not "
            f"root ({root}). Withdrawing an anchor un-anchors every promotion that names "
            "it, so it is the same authority as declaring one."
        )

    if cell_type == PROMOTION:
        signer = _principal(content, "signer")
        if signer is not None and author == signer:
            return None  # the promoter takes back its own promotion — N4's rollback
        return (
            f"a `promotion` may be retracted only by the signer it names, the realm root, "
            f"or a root-anchored promoter for its tier (signer={signer!r}, author={author})."
            " Quarantine is derived from promotion liveness, so retracting one re-quarantines"
            " the organ — demotion is an authority decision, not a comment."
        )

    if cell_type == CAPABILITY:
        granter = _principal(content, "granter")
        if granter is not None and author == granter:
            return None  # you may take back what you handed on — the ocap rule
        return (
            f"a grant may be retracted only by its own `granter`, the realm root, or a "
            f"root-anchored promoter for its tier (granter={granter!r}, author={author}). "
            "A capability RETRACT defaults to a DERIVED_AUTHORITY cascade, so an "
            "unauthorized one would fail closed every grant beneath it."
        )

    if cell_type == AGENT and isinstance(content, dict) and content.get("sandbox"):
        return (
            f"only the realm root may retract an agent cell carrying `sandbox`; {author} "
            f"is not root ({root}). Withdrawing the sandbox principal would strand every "
            "quarantined organ that depends on it to run at all."
        )
    return None
