"""Engagement / Session — a scoped, ROE-governed unit of orchestrated work.

From the Method platform's design ("sessions orchestrated at scale; clear rules of
engagement dictate when user input is needed; structured data provides context"): an
ENGAGEMENT is a single scoped session that carries (1) an objective, (2) structured
CONTEXT — a plain dict, treated purely as DATA, never as instructions — and (3) a
Rules-of-Engagement policy (an `roe` Cell). Every proposed action is consulted against
the ROE, which DICTATES WHEN THE HUMAN DECIDES:

  - "proceed" → the engagement runs the action autonomously;
  - "approve" → the engagement records a PENDING action and stops — a human must
                approve it before it runs;
  - "refuse"  → the engagement records a refused action and never runs it.

The engagement folds its OWN audit trail: each act appends a typed action record and
re-asserts the engagement Cell (LWW), so `status` folds the session's outcome directly
from the Weave — deterministic and time-travelable.

ROE is POLICY, not authority. It only decides human-involvement; it COMPOSES with —
never replaces — the kernel's `authorize`/Morta/lease gates, which independently gate
every real INVOKE. A "proceed" verdict grants nothing: if the action invokes a
Morta-gated capability, the kernel still denies it until the capability is approved.
Ints not floats in signed content.

Public `roe`/`kernel`/`model`/`hashing` API only — no core edit.
"""
from decima import roe as R
from decima.model import assert_content
from decima.hashing import content_id, nfc

ENGAGEMENT = "engagement"

# The three action outcomes an engagement folds over (mirror the ROE verdicts, but
# from the ENGAGEMENT's point of view: it either acted, is waiting on a human, or
# refused outright).
ACTED = "acted"
PENDING = "pending_approval"
REFUSED = "refused"


def open_engagement(k, objective, *, roe, context=None, author=None) -> str:
    """Open a scoped, ROE-governed session. Records an `engagement` Cell =
    {objective, roe (policy cell id), context (structured dict — data), status:"open",
    actions:[]} and returns its cell id. `context` is carried verbatim as structured
    DATA (never interpreted as instructions)."""
    author = author or k.decima_agent_id
    cid = content_id({"engagement": nfc(str(objective)), "roe": roe,
                      "at": k.weft.lamport}, kind="cell")
    content = {
        "objective": nfc(str(objective)),
        "roe": roe,
        "context": dict(context or {}),        # structured context — treated as DATA
        "status": "open",
        "actions": [],
    }
    assert_content(k.weft, author, cid, ENGAGEMENT, content)
    return cid


def _load(k, engagement_id):
    cell = k.weave().get(engagement_id)
    if cell is None or cell.type != ENGAGEMENT:
        raise ValueError(f"not an engagement: {engagement_id}")
    return cell


def _record(k, engagement_id, actions, *, author=None):
    """Re-assert the engagement Cell with an updated action list (LWW), so the
    engagement folds its own audit trail."""
    author = author or k.decima_agent_id
    cell = _load(k, engagement_id)
    content = {**cell.content, "actions": list(actions)}
    assert_content(k.weft, author, engagement_id, ENGAGEMENT, content)


def _action_record(action, verdict):
    """A typed, signed-content-safe action record (subset of the ROE match keys plus
    the verdict and a mutable status/result the engagement folds)."""
    return {
        "capability": action.get("capability"),
        "effect_class": action.get("effect_class"),
        "archetype": action.get("archetype"),
        "target": action.get("target"),
        "args": dict(action.get("args", {})),
        "verdict": verdict["verdict"],
        "reason": verdict["reason"],
        "rule": verdict["rule"],
        "status": None,
        "result": None,
    }


def _perform(k, record, *, agent_cell, run):
    """The single run/invoke path shared by `act` (proceed) and `approve_action`.
    Uses the injected `run` callable if given, else the kernel's gated `invoke`.
    NOTE: the kernel's authorize/Morta STILL gate this invoke independently of ROE."""
    if run is not None:
        return run(k, {"capability": record["capability"],
                       "effect_class": record.get("effect_class"),
                       "archetype": record.get("archetype"),
                       "target": record.get("target"),
                       "args": dict(record.get("args", {}))})
    if agent_cell is None:
        raise ValueError("act/approve_action needs either a `run` callable or an `agent_cell`")
    return k.invoke(agent_cell, record["capability"], dict(record.get("args", {})))


def act(k, engagement_id, action, *, agent_cell=None, run=None) -> dict:
    """Propose an `action` = {capability, effect_class?, archetype?, target?, args?}
    within the engagement. Consults the ROE and DICTATES the human-involvement:

      - "refuse"  → record a refused action; return {status:"refused", reason}.
      - "approve" → record a PENDING action WITHOUT running it (a human must approve
                    per ROE); return {status:"pending_approval", reason, idx}.
      - "proceed" → run it (via `run` or `k.invoke`); record the action + outcome;
                    return {status:"acted", result, idx}.

    Each call appends one action record and re-asserts the engagement (LWW audit
    trail). A "proceed" is POLICY only — the kernel's authorize/Morta still gate the
    real invoke, so a proceed on a Morta-gated capability comes back denied until the
    capability is approved."""
    cell = _load(k, engagement_id)
    verdict = R.evaluate(k, cell.content["roe"], action)
    record = _action_record(action, verdict)
    actions = list(cell.content.get("actions", []))
    idx = len(actions)

    if verdict["verdict"] == "refuse":
        record["status"] = REFUSED
        actions.append(record)
        _record(k, engagement_id, actions)
        return {"status": REFUSED, "reason": verdict["reason"], "idx": idx}

    if verdict["verdict"] == "approve":
        record["status"] = PENDING
        actions.append(record)
        _record(k, engagement_id, actions)
        return {"status": PENDING, "reason": verdict["reason"], "idx": idx}

    # proceed — run it (the kernel's gates still apply independently).
    result = _perform(k, record, agent_cell=agent_cell, run=run)
    record["status"] = ACTED
    record["result"] = result
    actions.append(record)
    _record(k, engagement_id, actions)
    return {"status": ACTED, "result": result, "idx": idx}


def approve_action(k, engagement_id, idx, *, agent_cell=None, run=None) -> dict:
    """A human approves a previously-PENDING action (ROE said the human must decide).
    Now runs it via the SAME run/invoke path and records it acted. Returns
    {status:"acted", result, idx}. Fails loud if the action is not pending."""
    cell = _load(k, engagement_id)
    actions = list(cell.content.get("actions", []))
    if not (0 <= idx < len(actions)):
        raise ValueError(f"no such action index {idx} on engagement {engagement_id}")
    record = dict(actions[idx])
    if record.get("status") != PENDING:
        raise ValueError(f"action {idx} is not pending_approval (status={record.get('status')!r})")
    result = _perform(k, record, agent_cell=agent_cell, run=run)
    record["status"] = ACTED
    record["result"] = result
    actions[idx] = record
    _record(k, engagement_id, actions)
    return {"status": ACTED, "result": result, "idx": idx}


def status(k, engagement_id) -> dict:
    """Fold the engagement's actions by status — the session summary. Returns
    {objective, roe, context, total, acted, pending_approval, refused, actions}.
    Deterministic (a pure fold over the engagement Cell)."""
    cell = _load(k, engagement_id)
    actions = list(cell.content.get("actions", []))
    counts = {ACTED: 0, PENDING: 0, REFUSED: 0}
    for a in actions:
        st = a.get("status")
        if st in counts:
            counts[st] += 1
    return {
        "objective": cell.content.get("objective"),
        "roe": cell.content.get("roe"),
        "context": dict(cell.content.get("context", {})),
        "total": len(actions),
        ACTED: counts[ACTED],
        PENDING: counts[PENDING],
        REFUSED: counts[REFUSED],
        "actions": actions,
    }
