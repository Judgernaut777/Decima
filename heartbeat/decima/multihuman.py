"""MULTI-HUMAN — one Decima, many human principals, each with their own scoped authority.

The law this lane keeps: AUTHORITY ISOLATION BETWEEN CO-TENANT HUMANS. Today
`identity.py` logs one human in with a fixed grant set; nothing says what happens
when TWO humans share one Decima. Without a wall, "the human" is an ambient role:
whoever reaches the inbox approves everything, whoever types recalls everything.
This module makes each human a first-class, self-certifying principal whose
authority is exactly their own attenuated envelope — and nothing else:

  • `register_human(k, subject, *, grants, scope)` — enroll a human as a DISTINCT
    principal. The identity is SELF-CERTIFYING (`keyring.mint_keyed`: the pid is a
    hash of the public key, so it is content-derived and collision-free with zero
    name coordination). Every capability they receive is ATTENUATED DOWNHILL from a
    realm capability Decima already holds (`capability.attenuate` — narrows, never
    widens; a Morta `requires_approval` floor survives the attenuation, it is
    unstrippable). Enrollment is a Cell on the Weft; it confers NOTHING beyond the
    named grants — no ambient authority.

  • `acting_as` / `invoke_as` / `holds` — the acting-as seam. `invoke_as` resolves
    the ACTOR's own agent cell and signs with the ACTOR's own key; if the named
    capability was granted to another human, the SAME ocap gate every INVOKE runs
    (`envelope_holds` → grantee → `verify_delegation` → caveats, in
    `capability.authorize`) denies it. Human A acting with human B's grant is a
    structural impossibility, not a policy.

  • `enqueue_gated` / `approve_as` / `deny_as` — approval BOUND TO THE APPROVER.
    A Morta-gated action enqueued for a human (via the existing `ApprovalInbox`)
    carries that human's principal on the item. Only THAT principal's approval
    enacts it: `approve_as` refuses a cross-principal approval (the refusal is a
    Weft Cell, the item stays pending, the effect never fires) and, on a match,
    asserts the invocation-scoped approval AUTHORED BY THE APPROVER'S OWN
    PRINCIPAL (reusing `capability.op_bind`/`approval_id`) before enacting through
    the full kernel gate. The inbox stays a carrier of a decision, never a grant.

  • `write_claim_as` / `recall_as` / `view_of` — the scoped view. Each human's
    claims live in THEIR scope (plus an explicit shared realm scope); recall is
    scope-filtered (`memory.recall`), so human A's recall never returns human B's
    scoped-private claims. A human's text is content observed from OUTSIDE the
    trust boundary: every claim is written `instruction_eligible=False` — recalled
    and cited as DATA, never obeyed as an instruction. A projection confers no
    authority.

Composes ONLY public seam APIs (identity-style enrollment over kernel principals,
capability, inbox, memory, model); touches no core file. Ints not floats in every
recorded content; deterministic (no clock, no unseeded randomness in hashed
content); fail closed on unknown humans, missing realm caps, decided items.
"""
from decima.capability import (attenuate, attenuation_valid, envelope_holds,
                               APPROVAL, approval_id, op_bind)
from decima.inbox import ApprovalInbox, DECISION
from decima.model import assert_content, assert_edge
from decima.weft import ASSERT, INVOKE
from decima.hashing import content_id, nfc
from decima import memory


class MultiHumanError(Exception):
    """A fail-closed multi-human refusal: unknown human, duplicate enrollment,
    missing realm capability, already-decided item, non-int confidence."""


# Cell types this module authors on the Weft.
ENROLLMENT = "human_enrollment"    # subject ↔ principal ↔ agent ↔ grants ↔ scope
REFUSAL = "approval_refusal"       # a cross-principal approval attempt, refused + audited

# The explicit shared scope both humans may see (memory's realm default). A
# human's private scope must be distinct from it.
SHARED_SCOPE = memory.DEFAULT_SCOPE


# ── realm capabilities: what enrollments attenuate DOWN from ────────────────

def mint_realm_capability(k, name, effect, *, gated=False, caveats=None) -> str:
    """Mint a realm-level capability through the EXISTING kernel APIs
    (`k._assert_cap` + `k.grant`) and hand it to the Decima orchestrator — the
    single root every human grant is attenuated downhill from. `gated=True`
    stamps the Morta `requires_approval` caveat, which attenuation can never
    strip (caveats are downhill-only)."""
    cv = dict(caveats or {})
    if gated:
        cv["requires_approval"] = True
    cap_id = k._assert_cap(nfc(name), effect, caveats=cv)
    k.grant(cap_id, k.decima_agent_id)
    return cap_id


def _realm_cap(w, k, name):
    """The live realm capability named `name` that DECIMA holds (grantee is the
    orchestrator's principal) — the only lawful parent for a human grant."""
    for c in w.of_type("capability"):
        if (c.content.get("name") == name
                and c.content.get("grantee") == k.decima.id):
            return c
    return None


# ── enrollment: a distinct, self-certifying principal per human ─────────────

def enrollment_of(k, who):
    """The live enrollment Cell for `who` (a subject name or a principal id),
    or None. Folded from the Weft — enrollment is state, not a session bit."""
    who = nfc(who)
    for c in k.weave().of_type(ENROLLMENT):
        if c.content.get("subject") == who or c.content.get("principal") == who:
            return c
    return None


def register_human(k, subject, *, grants, scope=None) -> dict:
    """Enroll `subject` as a DISTINCT human principal with their OWN authority.

    - identity: `keyring.mint_keyed` — the pid is blake2b(public key), so it is
      SELF-CERTIFYING (content-derived, verifiable from the key alone) and two
      humans can never collide;
    - authority: for each name in `grants`, ATTENUATE the realm capability of
      that name downhill to this principal (`capability.attenuate` +
      `attenuation_valid`; the granter signs the grant event, exactly as
      `kernel.spawn` does). Nothing ambient: the envelope is exactly these
      grants;
    - scope: the human's private memory scope (defaults to `human:<subject>`),
      never the shared realm scope;
    - record: the enrollment is a Cell on the Weft, linked to the agent.

    Fails CLOSED on a duplicate subject or a realm capability Decima does not
    hold. Returns {enrollment, subject, principal, agent, caps, scope}."""
    subject = nfc(subject)
    if enrollment_of(k, subject) is not None:
        raise MultiHumanError(f"{subject!r} is already enrolled (one principal per human)")
    scope = nfc(scope) if scope else f"human:{subject}"
    if scope == SHARED_SCOPE:
        raise MultiHumanError("a human's private scope may not be the shared realm scope")

    principal = k.keyring.mint_keyed(f"human:{subject}", "human")
    w = k.weave()
    envelope, caps = [], {}
    for raw in grants:
        name = nfc(raw)
        parent = _realm_cap(w, k, name)
        if parent is None:
            raise MultiHumanError(
                f"no realm capability {name!r} held by Decima to attenuate from (fail closed)")
        granter = parent.content.get("grantee")          # downhill: granter HOLDS the parent
        att = attenuate(parent.content, {"human": subject}, parent.id,
                        grantee=principal.id, granter=granter)
        ok, why = attenuation_valid(att, parent.content)
        if not ok:
            raise MultiHumanError(f"grant of {name!r} is not downhill: {why}")
        cap_id = content_id({"mh_grant": name, "to": principal.id})
        k.weft.append(granter, ASSERT,
                      {"cell": cap_id, "type": "capability", "content": att})
        envelope.append(cap_id)
        caps[name] = cap_id

    agent_id = content_id({"mh_agent": subject})
    assert_content(k.weft, k.decima.id, agent_id, "agent", {
        "principal": principal.id,
        "objective": f"human {subject}, acting in their own authority",
        "envelope": envelope,
        "sandbox": False,
    })
    eid = content_id({"human_enrollment": subject})
    assert_content(k.weft, k.decima.id, eid, ENROLLMENT, {
        "subject": subject, "principal": principal.id, "agent": agent_id,
        "scope": scope, "caps": caps,
        "public_key": k.keyring.public_key(principal.id),   # the id COMMITS to this key
        "disclosed": False,
    })
    assert_edge(k.weft, k.decima.id, eid, "enrolls", agent_id)
    return {"enrollment": eid, "subject": subject, "principal": principal.id,
            "agent": agent_id, "caps": caps, "scope": scope}


# ── acting-as: a human acts as THEMSELF, and only with their own grants ─────

def acting_as(k, who) -> dict:
    """Resolve `who` to their principal, their agent cell, and the caps they —
    and only they — hold: each cap must be live, sit in THEIR envelope
    (`envelope_holds`), and name THEIR principal as grantee. Fails closed on an
    unknown human."""
    ent = enrollment_of(k, who)
    if ent is None:
        raise MultiHumanError(f"no enrolled human {who!r} (fail closed)")
    w = k.weave()
    agent = w.get(ent.content["agent"])
    principal = ent.content["principal"]
    held = {}
    for name, cap_id in ent.content.get("caps", {}).items():
        cap = w.get(cap_id)
        if cap is None or cap.retracted:
            continue                                     # revoked authority folds out
        if cap.content.get("grantee") != principal:
            continue                                     # theirs, and only theirs
        if not envelope_holds(w, agent, cap_id):
            continue
        held[name] = cap_id
    return {"subject": ent.content["subject"], "principal": principal,
            "agent": agent, "caps": held, "scope": ent.content["scope"]}


def holds(k, who, cap_id) -> bool:
    """The envelope test: does `who` themself hold `cap_id`? True only if the
    grant sits in THEIR envelope AND names THEIR principal as grantee — knowing
    a public cap id (even another human's) buys nothing."""
    act = acting_as(k, who)
    w = k.weave()
    cap = w.get(cap_id)
    if cap is None or cap.type != "capability" or cap.retracted:
        return False
    return (envelope_holds(w, act["agent"], cap_id)
            and cap.content.get("grantee") == act["principal"])


def invoke_as(k, who, cap_id, args) -> dict:
    """INVOKE `cap_id` as human `who`. The actor acts as THEMSELF: the invoke is
    signed by their own key through their own agent cell, so a capability
    granted to another human is DENIED at the ocap gate (`envelope_holds` /
    grantee / `authorize` inside `kernel.invoke`) — no cross-principal
    authority, and this module adds no bypass around that gate."""
    act = acting_as(k, who)
    return k.invoke(act["agent"], cap_id, args)


# ── approval binding: only the human an item is FOR may enact it ────────────

def enqueue_gated(k, who, cap_id, args, *, description=None, provenance=None) -> str:
    """Enqueue a Morta-gated action FOR human `who` on the existing
    `ApprovalInbox`. The item is enqueued through THEIR agent cell, so it is
    born carrying THEIR principal — the binding `approve_as` enforces. Fails
    closed if `who` does not themself hold `cap_id` (you cannot queue work on
    someone else's authority)."""
    act = acting_as(k, who)
    if not holds(k, who, cap_id):
        raise MultiHumanError(
            f"{act['subject']!r} does not hold capability {str(cap_id)[:8]} (fail closed)")
    return ApprovalInbox(k).enqueue(
        act["agent"], cap_id, args,
        description=description or f"gated action for {act['subject']}",
        provenance=provenance)


def _pending_item(k, who, item_id):
    """Resolve (actor, inbox, item) for a decision, failing CLOSED on an unknown
    or already-decided item. Shared by approve_as / deny_as."""
    act = acting_as(k, who)
    ib = ApprovalInbox(k)
    st = ib.inspect(item_id)               # raises InboxError on an unknown item
    if st["status"] != "pending":
        raise MultiHumanError(
            f"item {str(item_id)[:8]} is already decided (fail closed, decided once)")
    return act, ib, st["item"]


def _refuse(k, act, item, verb) -> dict:
    """Record a cross-principal decision attempt as a REFUSAL Cell (audited on
    the Weft, authored by the refused approver's own principal). The item stays
    pending; the effect never fires; no decision lands."""
    approver, bound = act["principal"], item.content.get("principal")
    rid = content_id({"approval_refusal": item.id, "by": approver,
                      "verb": verb, "n": k.weft.lamport})
    assert_content(k.weft, approver, rid, REFUSAL, {
        "item": item.id, "verb": verb, "approver": approver, "bound_to": bound,
        "refused": True,
        "reason": "the decision is bound to the principal the item was enqueued for",
    })
    assert_edge(k.weft, approver, rid, "refused_decision_on", item.id)
    return {"refused": f"item {item.id[:8]} is bound to principal {str(bound)[:8]}; "
                       f"{act['subject']} is {approver[:8]} — not yours to {verb}",
            "refusal": rid}


def approve_as(k, who, item_id) -> dict:
    """Carry human `who`'s approval to the gate — IF AND ONLY IF the item is
    bound to their principal. A cross-principal approval is REFUSED (a REFUSAL
    Cell lands, the item stays pending, nothing fires). On a match, the
    invocation-scoped approval (`op_bind`/`approval_id`, exactly the kernel's
    shape) is asserted AUTHORED BY THE APPROVER'S OWN PRINCIPAL — the approval
    is bound to THEIR identity, not to an ambient 'the human' — and the pinned
    operation is enacted through the full kernel ocap/Morta gate, which still
    fails closed on a revoked or ungranted capability."""
    act, ib, item = _pending_item(k, who, item_id)
    approver, bound = act["principal"], item.content.get("principal")
    # LOAD-BEARING: the approval is bound to the APPROVER'S principal. Only the
    # human this item was enqueued for may carry a decision to the gate — human
    # A approving human B's pending item is refused, so A can never enact B's
    # gated action (nor have B's authority fire under A's say-so).
    if approver != bound:
        return _refuse(k, act, item, "approve")
    cap_id, args, nonce = (item.content["capability"], item.content["args"],
                           item.content["nonce"])
    ob = op_bind(INVOKE, {"cap": cap_id, "args": args}, nonce)
    aid = approval_id(cap_id, ob)
    k.weft.append(approver, ASSERT, {                     # the APPROVER signs the approval
        "cell": aid, "type": APPROVAL,
        "content": {"capability": cap_id, "scope": "invocation", "op": ob,
                    "approver": approver}})
    agent = k.weave().get(item.content["agent"])
    res = k.invoke(agent, cap_id, args, nonce=nonce)      # the SAME gate as any invoke
    if "ok" not in res:
        return res                    # gate refused (revoked/ungranted) — item stays pending
    did = content_id({"mh_approved": item.id, "invoke": res.get("invoke_event")})
    assert_content(k.weft, approver, did, DECISION, {
        "item": item.id, "decision": "approved", "approver": approver,
        "capability": cap_id, "nonce": nonce, "ran": True,
        "invoke": res.get("invoke_event"), "result_cell": res.get("result_cell"),
    })
    assert_edge(k.weft, approver, did, "decides", item.id)
    return res


def deny_as(k, who, item_id, reason="") -> dict:
    """Deny a pending item — same principal binding as `approve_as`: only the
    human the item is bound to may deny it (a stranger's deny is refused and
    audited, the item stays pending for its owner). The effect never runs."""
    act, ib, item = _pending_item(k, who, item_id)
    approver, bound = act["principal"], item.content.get("principal")
    if approver != bound:
        return _refuse(k, act, item, "deny")
    did = content_id({"mh_denied": item.id, "at": k.weft.head})
    assert_content(k.weft, approver, did, DECISION, {
        "item": item.id, "decision": "denied", "approver": approver,
        "capability": item.content.get("capability"), "reason": nfc(reason),
        "ran": False,
    })
    assert_edge(k.weft, approver, did, "decides", item.id)
    return {"denied_item": item.id, "decision": did}


# ── scoped view: each human sees their own claims (plus the shared scope) ───

def write_claim_as(k, who, text, *, evidence=None, shared=False,
                   confidence=memory.FULL_CONFIDENCE) -> str:
    """Record a claim AS human `who`, in THEIR private scope (or the explicit
    shared realm scope with `shared=True`), authored by their own principal and
    grounded in their enrollment Cell by default. A human's text is content
    observed from OUTSIDE the trust boundary: it is written
    `instruction_eligible=False` — to every other principal it is DATA to
    recall/cite, never an instruction to obey. Confidence is an int (millionths);
    a float is refused at the door."""
    if not isinstance(confidence, int) or isinstance(confidence, bool):
        raise MultiHumanError(f"confidence must be an int, got {confidence!r}")
    act = acting_as(k, who)
    scope = SHARED_SCOPE if shared else act["scope"]
    src = evidence or enrollment_of(k, who).id
    return memory.remember_semantic(
        k.weft, act["principal"], text, src,
        instruction_eligible=False,            # observed = data, never obeyed
        confidence=confidence, scope=scope)


def recall_as(k, who, query) -> list:
    """Scope-filtered recall for human `who`: their OWN scope plus the shared
    realm scope — never another human's private scope. Hits come back as DATA
    (`memory.recall` honors `recallable`; nothing recalled is an instruction).
    A projection: it confers no authority."""
    act = acting_as(k, who)
    w = k.weave()
    hits = memory.recall_semantic(w, query, scope=act["scope"])
    seen = {c.id for c in hits}
    hits += [c for c in memory.recall_semantic(w, query, scope=SHARED_SCOPE)
             if c.id not in seen]
    return hits


def view_of(k, who) -> dict:
    """The per-human projection: their principal, their scope, the caps they
    hold, the pending inbox items bound to THEM, and the claim ids in their
    private scope. Purely a fold — a view grants nothing."""
    act = acting_as(k, who)
    pending = [c.id for c in ApprovalInbox(k).pending()
               if c.content.get("principal") == act["principal"]]
    claims = [c.id for c in k.weave().of_type(memory.SEMANTIC)
              if c.content.get("scope") == act["scope"]]
    return {"subject": act["subject"], "principal": act["principal"],
            "scope": act["scope"], "caps": dict(act["caps"]),
            "pending": pending, "claims": claims}
