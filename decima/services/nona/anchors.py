"""Trust anchors and the sandbox principal (Nona wave N1).

THE ANCHOR PROBLEM. `Weave._is_trusted_promoter` honours a `promoter` Cell only when the
CONSTITUTIONAL ROOT asserted it — where "root" is `_genesis_author`, the author of the
parentless event with the smallest local `seq`. That anchor is deliberately un-forgeable:
a principal cannot self-declare promotion authority even by grinding a second parentless
event, because its `seq` is necessarily higher. The consequence for this module is a hard
ordering rule: **the anchors must be asserted by the root, as the first events on a fresh
Weft.** `provision` therefore installs them at first-run, before any other principal has
written anything.

WHAT AN ANCHOR SAYS. Exactly one thing: *this principal may sign promotions for these
tiers.* It grants no capability, no budget and no reach — promotion authority is the
authority to say "this candidate passed", nothing more. The Reckoner cannot invoke the
organs it promotes; it cannot promote a tier it was not named for; and a tier it was
named for can be withdrawn by RETRACTing the anchor, at which point the fold stops
honouring its signatures on the next re-fold (fail closed, no code change).

THE TIER LADDER. A candidate declares its effect class, and the tier selects which
promoter set may sign it. The ladder is ordered by blast radius:

  * ``pure``      — a deterministic function of its inputs. No filesystem, no network,
                    no clock. The whole failure mode of a wrong promotion is a wrong
                    answer, which the caller can still reject.
  * ``read_only`` — may READ granted state, may not write or reach outward.
  * ``network``   — declared for completeness and DELIBERATELY UNSIGNABLE here: there is
                    no mediated-egress worker profile (`workers/profiles.py` PROVIDER is
                    structure-only), so a network organ can be authored and evaluated but
                    never executed. Naming a promoter for it would be theatre; the UI says
                    NOT EXECUTABLE rather than "requires approval", because prompting for
                    something that cannot run only teaches the user to click yes.

MORTA SURVIVES PROMOTION. Lifting quarantine strips `sandbox_only` and nothing else — the
kernel keeps a `requires_approval` caveat across the lift (`weave.py`, promotion arm). An
unstrippable gate stays unstrippable; a promotion that tried to remove one would itself be
a recorded, attestable, retractable event.
"""

from __future__ import annotations

from typing import Any

from decima.kernel import model
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft

# The Reckoner: the evaluation authority. It signs promotions for the tiers named below
# and holds no capability to invoke what it promotes (separation of judge and actor).
RECKONER_NAME = "nona.reckoner"

# The sandbox principal: the ONLY principal a quarantined candidate may run as. It exists
# so "quarantined" has a runtime meaning and not merely a flag — see `sandbox_agent`.
SANDBOX_NAME = "nona.sandbox"

PURE = "pure"
READ_ONLY = "read_only"
WORKSPACE_WRITE = "workspace_write"
NETWORK = "network"

# Tiers a promoter may be anchored for. `network` is excluded ON PURPOSE (see module
# docstring): it has no executable path, so no principal is given authority to bless it.
#
# `workspace_write` is excluded for a DIFFERENT reason, and the difference matters. It now
# HAS an executable path (executor.TIER_PROFILES maps it to the WORKSPACE profile, whose
# bind-mount seam is real), but `promotion.SIGNER_POLICY` still requires a HUMAN attestation
# to promote one. Having an executor and being auto-promotable are two separate grants, and
# building the first is not permission to hand out the second: the design's eventual
# "Reckoner + canary" target for this tier is an authority widening that only an owner may
# authorize. Until then the canary measures organs a human chose to promote.
SIGNABLE_TIERS: tuple[str, ...] = (PURE, READ_ONLY)

PROMOTER = "promoter"


def promoter_cell_id(principal: str) -> str:
    """The deterministic Cell id of `principal`'s promoter anchor. Deterministic so
    installation is idempotent: re-running provision re-asserts the SAME cell rather than
    accreting a second anchor that would have to be reconciled."""
    return f"promoter:{principal}"


def install_trust_anchors(
    weft: Weft,
    root: str,
    *,
    reckoner: str,
    tiers: tuple[str, ...] = SIGNABLE_TIERS,
) -> dict[str, Any]:
    """Assert the root-authored `promoter` anchor naming `reckoner` for `tiers`.

    MUST be called with `root` = the principal that authors (or has authored) the Weft's
    genesis, and SHOULD be called at provision time, before any other principal writes.
    An anchor asserted by anyone else is folded as an ordinary cell and then filtered out
    at promote time — it confers nothing (fail closed).

    Idempotent: the anchor has a deterministic id, so a second call re-asserts identical
    content. Returns a public summary; no key material, no secret.

    Refuses a tier outside `SIGNABLE_TIERS` — notably `network`, which has no executable
    path, so anchoring a promoter for it would grant authority over something that can
    never run.
    """
    bad = [t for t in tiers if t not in SIGNABLE_TIERS]
    if bad:
        raise ValueError(
            f"refusing to anchor a promoter for un-signable tier(s) {bad}: "
            f"only {list(SIGNABLE_TIERS)} have an executable path "
            "(see decima/services/nona/anchors.py)"
        )
    cell = promoter_cell_id(reckoner)
    model.assert_content(
        weft,
        root,
        cell,
        PROMOTER,
        {"principal": reckoner, "tiers": list(tiers)},
    )
    return {"promoter_cell": cell, "principal": reckoner, "tiers": list(tiers)}


def ensure_trust_anchors(
    weft: Weft,
    *,
    root: str,
    reckoner: str,
    tiers: tuple[str, ...] = SIGNABLE_TIERS,
) -> dict[str, Any]:
    """Install the anchor on a store that may ALREADY EXIST — at store-construction time.

    `install_trust_anchors` above is the provisioning step, and `provision.first_run` is the
    only caller: it runs before any principal has written, so "root" is unambiguous. But the
    API daemon (`server.build_application`) and every per-user store
    (`tenancy.build_user_context`) open Wefts that provisioning never touched, and the fold
    honours a `promoter` anchor ONLY when its author is the store's genesis author. So
    without this function, a Shell-served store has no anchor at all and every promotion
    refuses with "not a trusted promoter" — a dead screen with a confusing error.

    Three cases, and the middle one is the reason this is not a one-liner:

      * **the store is EMPTY** — this assertion becomes the genesis, so `root` becomes the
        genesis author and the anchor is honoured. This is why it must run at construction
        time and not lazily inside the promote handler: a lazy install after the first note
        has been written picks a non-genesis author, confers nothing, and fails closed in a
        way that looks like a bug rather than a policy.
      * **the store's genesis author IS `root`** — re-assert (deterministic id ⇒ idempotent),
        but only if the anchor is not already live and honoured, so a restart does not append
        an event on every boot.
      * **the genesis author is someone else** — write NOTHING. An anchor asserted by a
        non-genesis principal is folded as an ordinary cell and filtered out at promote
        time; writing it anyway would put a cell on the log that LOOKS like authority and
        is not. Report the refusal instead.

    Returns a public summary including `installed` (bool) and `reason`. Never raises for the
    third case: a store nobody can anchor is a legitimate state (it just cannot promote).
    """
    weave = Weave.fold(weft)
    # The same private accessor `trusted_promoters` already leans on: ask the fold what it
    # will honour rather than re-deriving the genesis rule here, where it could disagree
    # with enforcement.
    genesis = weave._genesis_author
    if genesis is not None and genesis != root:
        return {
            "installed": False,
            "reason": (
                "this store's genesis was authored by another principal, so an anchor "
                "asserted here would confer nothing (fail closed)"
            ),
            "principal": reckoner,
            "tiers": list(tiers),
        }
    honoured = trusted_promoters(weave).get(reckoner, [])
    if set(tiers) <= set(honoured):
        return {
            "installed": False,
            "reason": "already anchored and honoured",
            "promoter_cell": promoter_cell_id(reckoner),
            "principal": reckoner,
            "tiers": list(honoured),
        }
    anchor = install_trust_anchors(weft, root, reckoner=reckoner, tiers=tiers)
    return {
        "installed": True,
        "reason": "genesis" if genesis is None else "root",
        **anchor,
    }


def trusted_promoters(weave: Weave) -> dict[str, list[str]]:
    """Fold the LIVE, root-declared promoter anchors: principal → tiers it may sign.

    A pure projection of what the kernel will actually honour — an anchor asserted by a
    non-root principal, or one that has been RETRACTed, does not appear. This is the
    read the Shell and the operator use to answer "who can promote what, right now?"
    without having to trust a config file.
    """
    out: dict[str, list[str]] = {}
    for cid, cell in weave.cells.items():
        if cell.type != PROMOTER or cell.retracted:
            continue
        # Root-authorship is the whole basis of the anchor's trust; ask the fold rather
        # than re-deriving the rule here, so this can never disagree with enforcement.
        principal = cell.content.get("principal")
        if not isinstance(principal, str):
            continue
        honoured = [
            t for t in (cell.content.get("tiers") or []) if weave._is_trusted_promoter(principal, t)
        ]
        if honoured:
            out[principal] = sorted(honoured)
        _ = cid
    return out
