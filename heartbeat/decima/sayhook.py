"""FORGE / REFUSED SAY-HOOK SURFACING (Batch U) — an operator-facing READ over the
weave that answers: "what did my last turn(s) want to do but couldn't, and what
does the catalog suggest activating instead?"

The gap this closes: when `kernel.say` runs an agentic turn, `_delegate` can
REFUSE a step — the delegator does not hold the capability (`status: "ungranted"`),
Decima's own recorded governance bans it (`status: "governance_denied"`), or its
learned org policy distrusts a bad track record (`status: "refused"`) — and
`discovery.discover` can submit a `catalog.activate:<name>` suggestion to the
ApprovalInbox. Both are already recorded on the Weft (task Cells; inbox_item
Cells), but nothing SURFACES them to a human as one readable view. This module is
that surface, and NOTHING ELSE:

  - it folds/reads the SAME weave/inbox every other read-only report does
    (`k.weave()`, `k.weft.events()`, `ApprovalInbox.pending()`) — public seams only;
  - it MINTS NOTHING: no Weft append, no ASSERT, no approval, no activation. A
    human still approves (or denies) an activation via the ORDINARY ApprovalInbox
    spine (`inbox.approve`/`inbox.deny`) — this module only makes the pending
    decision, and the prior refusal, VISIBLE;
  - every string this module returns is DATA. A refused capability name, an
    objective, a governance reason — all of it may echo untrusted content (a
    catalog manifest, a user utterance); every dict this module returns carries
    `instruction_eligible: False` so nothing here can steer a brain's reasoning,
    only inform a human;
  - ints only, no wall-clock: ordering uses the Weft's own logical `lamport`
    counter (WEFT §2), never a timestamp.

Pure stdlib. No core file touched — this composes `kernel.weave()`, `weft.events()`,
`inbox.ApprovalInbox`, and `discovery.ACTIVATE_PREFIX` only.
"""
from decima.inbox import ApprovalInbox
from decima.weft import ASSERT

# The three task statuses `kernel._delegate` records for a step it REFUSED rather
# than spawning a worker for (kernel.py ~600-648): the delegator does not hold the
# capability ("ungranted"), Decima's own recorded governance bans it
# ("governance_denied"), or the learned org policy distrusts a bad track record
# ("refused"). A worker that was spawned and later denied at invoke-time carries a
# different status ("denied") and is NOT a say-hook refusal — it already ran.
REFUSED_STATUSES = ("ungranted", "governance_denied", "refused")


def _cell_lamports(k) -> dict:
    """cell id -> the highest lamport of any ASSERT event that touched it.

    A pure read over `k.weft.events()` (which re-verifies id + sig per event —
    Law 1/4 — so a tampered log fails loud here rather than silently). Cells are
    not stamped with their own lamport (Law 5: state is a fold), so this recovers
    "when" a cell was last asserted from the log's own logical clock — an int,
    never a wall-clock read."""
    lam: dict = {}
    for ev in k.weft.events():
        if ev.verb != ASSERT or not isinstance(ev.body, dict):
            continue
        cid = ev.body.get("cell")
        if not cid:
            continue
        if ev.lamport > lam.get(cid, 0):
            lam[cid] = ev.lamport
    return lam


def refused_outcomes(k, *, agent=None, limit=None) -> list[dict]:
    """Every `task` Cell `kernel._delegate` recorded as REFUSED — status in
    `REFUSED_STATUSES` — as an operator-readable DATA dict.

    `agent`, if given, keeps only rows whose delegator OR intended worker is named
    `agent` (a plain string match on `delegator_name`/`worker_name`); `limit`, if
    given, keeps only the first `limit` rows after ordering.

    Ordering is NEWEST-RELEVANT first: descending by the cell's own lamport (an
    int on the Weft's logical clock, WEFT §2 — never a wall-clock), tie-broken by
    the task cell id for a stable, deterministic order.

    Each row: `task` (cell id), `status`, `capability` (what was requested),
    `reason` (the reason class/text `_delegate` recorded — `result` on the task
    cell), `delegator`/`worker` (names, may be None), `objective`, `governance`
    (the banned-action rule cell id, only for `governance_denied`), `evidence`
    (supporting evidence cell ids, only for `governance_denied`), `depth`,
    `lamport` (int), and `instruction_eligible: False` (this is DATA a human
    reads — a refused capability name is untrusted content and must never be fed
    back into a brain as an instruction)."""
    w = k.weave()
    lam = _cell_lamports(k)
    rows = []
    for t in w.of_type("task"):
        c = t.content
        status = c.get("status")
        if status not in REFUSED_STATUSES:
            continue
        delegator_name, worker_name = c.get("delegator_name"), c.get("worker_name")
        if agent is not None and agent not in (delegator_name, worker_name):
            continue
        rows.append({
            "task": t.id,
            "status": status,
            "capability": c.get("capability"),
            "reason": c.get("result"),
            "delegator": delegator_name,
            "worker": worker_name,
            "objective": c.get("objective"),
            "governance": c.get("governance"),
            "evidence": c.get("evidence"),
            "depth": c.get("depth"),
            "lamport": int(lam.get(t.id, 0)),
            "instruction_eligible": False,
        })
    rows.sort(key=lambda r: (-r["lamport"], r["task"]))
    if limit is not None:
        rows = rows[: int(limit)]
    return rows


def activation_suggestions(k, *, limit=None) -> list[dict]:
    """Every PENDING `catalog.activate:<name>` ApprovalInbox item — a `discover()`
    "use" suggestion the human has not yet decided — as an operator-readable DATA
    dict, with its manifest provenance.

    A pure read via `ApprovalInbox.pending()` (itself a fold, no mutation): this
    NEVER approves, denies, or installs anything — the item stays exactly as
    pending as it was before this call. `limit`, if given, keeps only the first
    `limit` rows after ordering (same newest-first-by-lamport rule as
    `refused_outcomes`).

    Each row: `item` (the inbox item cell id), `name` (the catalog capability
    name), `description` (the human-readable proposal text), `manifest` (the
    provenance cell id of the catalog manifest that was found — read straight off
    the item, matching what `discovery.submit_activation` recorded), `args`, `agent`
    (who raised the suggestion), `lamport` (int), and `instruction_eligible: False`
    (a queued item DESCRIBES a proposed effect for a human — `inbox.enqueue`
    already stamps it untrusted; this surface just relays that same stamp)."""
    from decima import discovery as D          # lazy: avoid an import-time cycle
    ib = ApprovalInbox(k)
    lam = _cell_lamports(k)
    rows = []
    for item in ib.pending():
        cap_name = item.content.get("capability_name") or ""
        if not cap_name.startswith(D.ACTIVATE_PREFIX):
            continue                            # only catalog-activation items, not any gated op
        rows.append({
            "item": item.id,
            "name": cap_name[len(D.ACTIVATE_PREFIX):],
            "description": item.content.get("description"),
            "manifest": item.content.get("provenance"),
            "args": item.content.get("args"),
            "agent": item.content.get("agent"),
            "lamport": int(lam.get(item.id, 0)),
            "instruction_eligible": False,
        })
    rows.sort(key=lambda r: (-r["lamport"], r["item"]))
    if limit is not None:
        rows = rows[: int(limit)]
    return rows


def surface(k, *, limit=None) -> dict:
    """The combined operator view: `{"refused": [...], "suggestions": [...]}`.

    This SURFACES, it never mints or approves: calling it appends NOTHING to the
    Weft (`k.weft.count()` before and after is identical) and decides nothing in
    the ApprovalInbox — a human still approves/denies via the ORDINARY inbox
    spine (`ApprovalInbox.approve`/`.deny`), and a refused delegation stays
    refused until a real re-delegation (or a policy/governance change) runs it
    again. Every string returned is DATA (`instruction_eligible: False`
    throughout) — this view informs a human, it does not instruct an agent."""
    return {
        "refused": refused_outcomes(k, limit=limit),
        "suggestions": activation_suggestions(k, limit=limit),
    }
