"""Nona — the self-extension engine (the organ that makes organs).

`VISION.md` / `KERNEL.md`: an agent ASSERTs a new Capability Cell plus its
implementation; it is born QUARANTINED (`sandbox_only`, no outward effects); the
Reckoner evaluates it; a TRUSTED principal ATTESTs a promotion that lifts the
quarantine; the next agent holding it INVOKEs it. The set of things Decima can do is
therefore itself a fold over the Weft, and it only ever grows — with every step a
recorded, retractable event.

WHAT THIS PACKAGE IS FOR. The kernel already implements the *fold* half of that loop:
`weave.py` records root-declared `promoter` anchors, and a promote-`ATTEST` lifts a
capability's quarantine ONLY when its author is a trusted promoter for the candidate's
declared tier (`Weave._is_trusted_promoter`). What has been missing is everything that
makes that machinery reachable in the shipping product — the principals, the anchors,
the candidate lifecycle, the evaluation gate. This package is that connective tissue,
built in waves (see `docs/design/nona-self-extension.md`).

WAVE N1 (this module set): the trust anchors and the sandbox principal — the
foundation every later wave stands on, and the first code to exercise the kernel's
promotion path at all.

WHERE THE AUTHORITY LIVES. Trust is DATA on the Weft, not configuration: a `promoter`
Cell says "this principal may sign promotions for these tiers", and the fold honours it
ONLY if the CONSTITUTIONAL ROOT asserted it. Nona holds no ambient power — it is a
principal with an envelope like any other (Law 2), and the anchors below are the exact,
auditable statement of what it may promote.
"""
