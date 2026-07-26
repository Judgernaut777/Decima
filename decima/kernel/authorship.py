"""Who may ASSERT a cell that AUTHORITY is read from (Nona N7 / design R1).

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
  * the FOLD and the READ    — `Weave._cell_author` records who asserted each guarded
                               cell and `capability.verify_delegation` /
                               `Weave._cascade_retractions` refuse to derive authority
                               from a cell whose asserter had no business asserting it.

The fold/read pass is the actual security boundary. A write door protects only what THIS
process writes NOW: it does nothing for a log already on disk, a restored backup, or a
peer's forgery. This module is the shared rule; `weave.py` and `capability.py` are where
it is enforced against history.

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
  * `Weave._is_trusted_promoter` still lifts a capability that declares NO tier on ANY
    promote-ATTEST (`tier is None → True`), which is legacy back-compat, not authorship.

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
