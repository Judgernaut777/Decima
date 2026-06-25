"""DISP1 — Disposition routing: GTD for the machine age.

Every intake — an observation, an inbound message, a tool result — is **untrusted data**.
Decima captures it and resolves it to a first-class **disposition**: `archive` (a deterministic
noise filter), `remember` (a memory write), `task` (open a task), `invoke` (propose an effect —
still subject to authorize/Morta), or `policy` (a governance update). The split the OODA /
Infinite-Brain piece calls for: **deterministic filtering separated from model/orientation
analysis** (D4.2). It generalizes the browser→memory ingestion: any inbound event is captured
and routed.

The law that makes this safe — the same recall-vs-instruct law the browser receipt and B4
governance obey: **the disposition is Decima's decision, never the intake's instruction.**
- **Untrusted** intake can only ever be `remember`d (as DATA, `instruction_eligible=False`) or
  `archive`d. It can never elevate itself to a task, an invoke, or a policy.
- An **injection-laced** untrusted intake is detected *as data* and routed to `remember`
  (flagged suspicious) — its imperative content never selects its own disposition.
- `task`/`invoke`/`policy` are reserved for **trusted** intakes, and an Orientation (OR1) veto
  still holds them for review if they conflict with a standing rule.

Public `memory`/`model`/`weave`/`orientation` API only — no core edit.
"""
import re

from decima.model import assert_content, assert_edge
from decima import memory
from decima.hashing import content_id, nfc

INTAKE = "intake"
DISPOSITION = "disposition"
# disposition actions
ARCHIVE = "archive"
REMEMBER = "remember"
TASK = "task"
INVOKE = "invoke"
POLICY = "policy"

# Deterministic noise filter (NO model): channels/patterns that are never actionable.
_NOISE_SOURCES = {"notification", "spam", "newsletter", "automated"}
_NOISE_RE = re.compile(r"\b(unsubscribe|no[- ]?reply|out of office|delivery status)\b", re.I)
# Injection patterns — matched against untrusted text AS DATA, so an injection routes to
# remember(suspicious), never to invoke/policy. (The browser receipt obeys the same law.)
_INJECTION_RE = re.compile(
    r"ignore (your |all |the |previous )?(instructions|rules|prompt)|disregard|"
    r"you are now|new instructions:|system:|\bexfil", re.I)


def _record_intake(k, source, text, trusted, author):
    iid = content_id({"intake": text, "source": source, "at": k.weft.head})
    assert_content(k.weft, author, iid, INTAKE, {
        "source": source, "text": nfc(text), "trusted": bool(trusted),
        "instruction_eligible": bool(trusted),       # untrusted intake is DATA
    })
    return iid


def _classify(source, text, trusted, kind, orientation):
    """The analysis seam (a real model/orientation plugs in here). Returns
    (action, reason, stage). Decima decides from the intake-as-DATA + trust + orientation —
    never by obeying imperative content in the payload."""
    # 1. deterministic noise filter — split from analysis, runs first, any source.
    if source in _NOISE_SOURCES or _NOISE_RE.search(text):
        return ARCHIVE, "deterministic noise filter", "deterministic"
    # 2. UNTRUSTED intake can never elevate — only remember (DATA) or archive.
    if not trusted:
        if _INJECTION_RE.search(text):
            return REMEMBER, "injection detected — stored as suspicious DATA, not obeyed", "analysis"
        return REMEMBER, "untrusted intake → memory (DATA)", "analysis"
    # 3. TRUSTED: an Orientation veto (a rule the owner set earlier) still holds it for review.
    if orientation is not None and orientation.blocked:
        return REMEMBER, f"{orientation.refusal()} → held for review (DATA)", "analysis"
    # 4. trust-gated dispositions by the channel's kind hint.
    if kind == "directive":
        return POLICY, "trusted directive → governance update", "analysis"
    if kind in ("request", "actionable"):
        return TASK, "trusted actionable request → task", "analysis"
    if kind == "command":
        return INVOKE, "trusted command → invoke proposal (still authorize/Morta-gated)", "analysis"
    # 5. default: a trusted note is remembered (instruction-eligible); empty is archived.
    if text.strip():
        return REMEMBER, "trusted note → memory", "analysis"
    return ARCHIVE, "empty intake", "deterministic"


def _open_task(k, author, objective, intake_id):
    # A `todo` Cell — a queued actionable item, distinct from a delegation `task` cell
    # (which the kernel's task-tree owns); executing it still goes through authorize/Morta.
    tid = content_id({"todo": objective, "from": intake_id})
    assert_content(k.weft, author, tid, "todo", {
        "objective": nfc(objective), "status": "open",
        "source": intake_id, "origin": "disposition"})
    return tid


def _propose_invoke(k, author, detail, intake_id):
    pid = content_id({"invoke_proposal": detail, "from": intake_id})
    assert_content(k.weft, author, pid, "proposal", {
        "proposes": "invoke", "detail": nfc(detail),
        "source": intake_id, "status": "pending"})   # a PROPOSAL — still authorize/Morta-gated
    return pid


def dispose(k, source, text, *, trusted=False, kind=None, target=None,
            author=None, agent_cell=None, scope=None) -> dict:
    """Capture an intake and resolve it to a disposition. Records an `intake` Cell (untrusted
    data) and a `disposition` Cell with a `disposed_as` edge between them, carries the chosen
    action out at the DATA level (remember/task/policy/archive) or PROPOSES it (invoke), and
    returns {intake, disposition, action, reason, produced}. The action is Decima's — never the
    payload's instruction."""
    author = author or k.decima_agent_id
    iid = _record_intake(k, source, text, trusted, author)

    orientation = None
    if agent_cell is not None:
        from decima import orientation as orient_mod
        orientation = orient_mod.orient(k.weave(), agent_cell, text, scope=scope)

    action, reason, stage = _classify(source, text, trusted, kind, orientation)

    produced = None
    if action == REMEMBER:
        produced = memory.remember(k.weft, author, text, evidence_src=iid,
                                   instruction_eligible=bool(trusted))
    elif action == TASK:
        produced = _open_task(k, author, text, iid)
    elif action == POLICY:
        produced = memory.remember_governance(k.weft, author, memory.BANNED_ACTION,
                                              target=(target or text), reason=reason,
                                              evidence_src=iid)
    elif action == INVOKE:
        produced = _propose_invoke(k, author, text, iid)
    # ARCHIVE: nothing produced — the intake is captured and dropped.

    did = content_id({"disposition": iid, "action": action})
    assert_content(k.weft, author, did, DISPOSITION, {
        "intake": iid, "action": action, "reason": reason, "by": stage,
        "produced": produced, "trusted": bool(trusted),
    })
    assert_edge(k.weft, author, iid, "disposed_as", did)
    return {"intake": iid, "disposition": did, "action": action, "reason": reason,
            "produced": produced, "by": stage}
