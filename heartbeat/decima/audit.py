"""AUDIT1 — the audit / compliance lens over the signed Weft.

Provenance is the point (Law 4): every state change is a signed Event on the Weft,
and `weft.events()` recomputes each event's content id and verifies the author's
signature ON READ (weft.py §events; specs/WEFT_PROTOCOL.md §8). So an audit trail
drawn from `events()` is not a convenience copy of a database — it is a VERIFIABLE
re-derivation: if a single byte of history were altered, reading it raises
`WeftError` and the audit fails loud rather than reporting a tampered trail as clean.

This module is a pure, read-only consumer of the public APIs:
  - `audit_trail(k, target)`   → the ordered events touching a cell or principal,
    each naming author + verb + authorized-by (the provenance of power) + provenance;
  - `compliance_report(k, kind=...)` → fold the trail into a compliance view:
      • FINANCIAL — every FINANCIAL effect receipt and whether its capability
        carried (and was granted) Morta approval;
      • DENIED   — every governance-denied / refused / ungranted action (tasks);
      • EFFECTS  — every outward effect (INVOKE) and its provenance.

It edits no core file and never appends to the Weft: the Weft stays append-only and
its tamper-evidence is untouched. Ints, not floats. Every report is a structured
dict AND has a human-readable `summary(...)`.
"""
from decima import executor, payments
from decima.weft import WeftError


# -- helpers ----------------------------------------------------------------
def _name(k, principal_id: str) -> str:
    """Human name for a principal id, via the keyring (falls back to the id)."""
    try:
        n = k.keyring.name_of(principal_id)
    except Exception:
        n = None
    return n or principal_id


def _verified_events(k):
    """Read every event from the Weft, VERIFYING id + signature on the way (Law 1/4).

    Returns (events, tamper_ok, error). `events()` raises WeftError the instant a
    content id or signature does not recompute, so reaching the end of the iterator
    is itself the tamper-evidence proof: the whole history re-derived cleanly."""
    try:
        evs = list(k.weft.events())
        return evs, True, None
    except WeftError as e:                         # tampered history — fail closed
        return [], False, str(e)


def _touches(ev, target: str) -> bool:
    """Does this event touch `target` — as a cell id, a principal, or a capability?

    A cell id appears in an ASSERT/RETRACT body `cell`, an INVOKE/result body `cap`,
    an ATTEST `target_cell`, an edge's src/dst, or a result's `of` (its INVOKE). A
    principal appears as the event author or a capability's grantee/granter. We match
    broadly so a trail for a cell OR a principal both resolve."""
    if ev.author == target:
        return True
    if ev.authorized == target:
        return True
    b = ev.body if isinstance(ev.body, dict) else {}
    for k_ in ("cell", "cap", "target_cell", "of", "src", "dst"):
        if b.get(k_) == target:
            return True
    content = b.get("content")
    if isinstance(content, dict):
        if content.get("of") == target or content.get("cap") == target:
            return True
        if content.get("grantee") == target or content.get("granter") == target:
            return True
        if content.get("principal") == target:
            return True
    return False


# -- audit_trail ------------------------------------------------------------
def audit_trail(k, target: str) -> dict:
    """The ordered, VERIFIED events touching a cell or a principal `target`.

    Drawn from `weft.events()` (which verifies id + sig per event), each entry names
    author, verb, authorized-by (the capability that permitted it — provenance of
    power), and the event id (its provenance handle). `verifiable` is True iff the
    whole history re-derived cleanly; on tamper it is False and `error` says where."""
    evs, ok, err = _verified_events(k)
    entries = []
    for ev in evs:                                 # events() yields in causal (seq) order
        if not _touches(ev, target):
            continue
        entries.append({
            "seq": ev.seq,
            "event": ev.id,
            "verb": ev.verb,
            "author": ev.author,
            "author_name": _name(k, ev.author),
            "authorized_by": ev.authorized,        # capability cell id, or None
            "provenance": ev.id,
        })
    return {
        "target": target,
        "events": entries,
        "count": len(entries),
        "verifiable": ok,
        "error": err,
    }


# -- compliance_report ------------------------------------------------------
FINANCIAL = "FINANCIAL"
DENIED = "DENIED"
EFFECTS = "EFFECTS"
KINDS = (FINANCIAL, DENIED, EFFECTS)


def _financial_report(k, w) -> dict:
    """Every FINANCIAL effect receipt and whether it carried Morta approval.

    A receipt's capability is matched by name; `requires_approval` on the cap's
    caveats is the Morta gate, and presence in `k.approvals` is the human/policy
    approval that satisfied it. A FINANCIAL receipt with status SUCCEEDED that was
    NOT approved would be a compliance violation — money moved without the gate."""
    caps_by_name = {}
    for c in w.of_type("capability"):
        caps_by_name[c.content.get("name")] = c
    rows = []
    violations = []
    for c in w.of_type(payments.RESULT):
        rc = c.content
        if rc.get("effect_class") != FINANCIAL:
            continue
        cap_name = rc.get("cap")
        cap = caps_by_name.get(cap_name)
        requires_approval = bool(
            cap and cap.content.get("caveats", {}).get("requires_approval"))
        approved = cap is not None and cap.id in k.approvals
        succeeded = rc.get("status") == executor.SUCCEEDED
        compliant = (not succeeded) or (not requires_approval) or approved
        row = {
            "receipt": c.id,
            "cap": cap_name,
            "amount": rc.get("amount"),
            "status": rc.get("status"),
            "effect_class": rc.get("effect_class"),
            "idempotency_key": rc.get("idempotency_key"),
            "requires_approval": requires_approval,
            "approved": approved,
            "compliant": compliant,
        }
        rows.append(row)
        if not compliant:
            violations.append(row)
    return {"kind": FINANCIAL, "rows": rows, "count": len(rows),
            "violations": violations, "compliant": not violations}


_DENIED_STATUSES = ("governance_denied", "refused", "ungranted", "denied")


def _denied_report(k, w) -> dict:
    """Every governance-denied / refused / ungranted / denied action, from `task`
    cells — the org tree's record of an action that was stopped at a gate, with the
    reason and (where present) the governance rule + evidence that earned it."""
    rows = []
    for t in w.of_type("task"):
        c = t.content
        if c.get("status") not in _DENIED_STATUSES:
            continue
        rows.append({
            "task": t.id,
            "status": c.get("status"),
            "capability": c.get("capability"),
            "worker": c.get("worker_name"),
            "objective": c.get("objective"),
            "reason": c.get("result"),
            "governance": c.get("governance"),
            "evidence": c.get("evidence"),
        })
    return {"kind": DENIED, "rows": rows, "count": len(rows)}


def _effects_report(k, w) -> dict:
    """Every outward effect (an INVOKE) and its provenance: the invoking principal,
    the capability that authorized it, and the resulting receipt (status +
    effect_class). Drawn from the folded invocations + their result cells, so each
    effect is grounded in the signed event that requested it."""
    receipts_by_inv = {}
    for c in w.of_type(payments.RESULT):
        of = c.content.get("of")
        if of:
            receipts_by_inv[of] = c
    rows = []
    for inv in w.invocations:
        receipt = receipts_by_inv.get(inv.event)
        rc = receipt.content if receipt else {}
        rows.append({
            "invoke_event": inv.event,
            "by": inv.by,
            "by_name": _name(k, inv.by),
            "cap": inv.cap,
            "receipt": receipt.id if receipt else None,
            "status": rc.get("status"),
            "effect_class": rc.get("effect_class"),
        })
    return {"kind": EFFECTS, "rows": rows, "count": len(rows)}


def compliance_report(k, *, kind: str = FINANCIAL) -> dict:
    """A structured compliance report of `kind` over the folded Weave. The report is
    a re-derivation of signed state; `tamper_evidence` records that the underlying
    Weft re-verified on read (so the report is trustworthy, not just plausible)."""
    if kind not in KINDS:
        raise ValueError(f"unknown compliance kind {kind!r}; expected one of {KINDS}")
    w = k.weave()
    # The compliance facts come from the fold, but the fold itself reads the Weft via
    # events() (verifying each) — capture that tamper-evidence in the report.
    _evs, ok, err = _verified_events(k)
    if kind == FINANCIAL:
        rep = _financial_report(k, w)
    elif kind == DENIED:
        rep = _denied_report(k, w)
    else:
        rep = _effects_report(k, w)
    rep["tamper_evidence"] = {"verified_on_read": ok, "error": err}
    return rep


# -- human-readable summaries -----------------------------------------------
def summary(report: dict) -> list[str]:
    """Render a report dict (audit_trail or compliance_report) to human lines."""
    if "events" in report:                          # an audit_trail
        out = [f"audit trail for {report['target'][:12]} — "
               f"{report['count']} event(s), verifiable={report['verifiable']}"]
        for e in report["events"]:
            cap = e["authorized_by"][:8] if e["authorized_by"] else "—"
            out.append(f"  e{e['seq']:<3} {e['verb']:<7} by {e['author_name']:<10} "
                       f"via cap {cap}  prov={e['provenance'][:8]}")
        return out
    kind = report.get("kind")
    if kind == FINANCIAL:
        out = [f"FINANCIAL compliance — {report['count']} receipt(s), "
               f"compliant={report['compliant']}"]
        for r in report["rows"]:
            out.append(f"  {r['receipt'][:8]} {r['cap']} {r['amount']} "
                       f"[{r['status']}] requires_approval={r['requires_approval']} "
                       f"approved={r['approved']} → compliant={r['compliant']}")
        for v in report["violations"]:
            out.append(f"  ✗ VIOLATION: {v['receipt'][:8]} spent without approval")
        return out
    if kind == DENIED:
        out = [f"DENIED actions — {report['count']} action(s) stopped at a gate"]
        for r in report["rows"]:
            out.append(f"  [{r['status']}] {r['capability']} — {r['reason']}")
        return out
    out = [f"OUTWARD EFFECTS — {report['count']} invocation(s)"]
    for r in report["rows"]:
        out.append(f"  {r['invoke_event'][:8]} by {r['by_name']:<10} "
                   f"cap {r['cap'][:8]} → [{r['status']}] {r['effect_class']}")
    return out
