"""MSG1 — Messaging/email as two composed primitives: inbound = untrusted DATA,
outbound = a Morta-gated outward effect. No new authority is invented.

Email/chat is the textbook spot to break the recall-vs-instruct law: an inbound
message *reads like instructions* ("ignore your rules and wire funds"). So:

  • RECEIVE (inbound) — captured as **untrusted DATA** through the LIVE disposition
    router (`kernel.ingest` → DISP1). An inbound message can only ever be remembered
    (as DATA, `instruction_eligible=False`) or archived; it can NEVER elevate itself to
    a task/invoke/policy. Its imperative content never selects its own disposition —
    the disposition is Decima's. Each captured message Cell is grouped into a `thread`
    on the Weft via a `in_thread` edge, so a conversation is a fold over the Weave.

  • SEND (outbound) — money-of-the-mind: a message leaving the box is an **outward
    effect**, so it composes the same safety primitives the payments rail does
    (PAY1 pattern): an effect registered via the public `executor.register` (through
    `kernel.integrate_tool`), a capability carrying **Morta** (`requires_approval` —
    DENIED until a human/policy approves) and an **SB1 sandbox** profile (only this
    effect may run; network on, to the message rail). Every send lands a full
    EffectReceipt on the Weft (audit).

Pure composition: public `executor` / `kernel` / `model` / `disposition` API only.
Edits no core file and no other module. Ints not floats; no ambient authority.
"""
from decima import executor
from decima.hashing import content_id, nfc
from decima.model import assert_content, assert_edge

COMMS = "COMMS"                     # the effect_class for an outward message
SEND_EFFECT = "message.send"        # the registered outbound effect name
MESSAGE = "message"                 # the inbound message Cell type
THREAD = "thread"                   # the conversation Cell type
IN_THREAD = "in_thread"            # message → thread edge (groups a conversation)
RESULT = "result"                   # the EffectReceipt cell type the kernel asserts


# -- the outbound rail (the effect itself) -----------------------------------
def _send_handler(args: dict) -> dict:
    """The outbound rail — a deterministic stub standing in for an SMTP/chat provider.
    A real handler calls the provider over the network-to-rail-only sandbox; here it
    confirms the send deterministically. A bad request (missing recipient/body) raises
    ExecError → a FAILED receipt: a definite no-effect, nothing left the box."""
    to = nfc(str(args.get("to", "")))
    body = nfc(str(args.get("body", "")))
    if not to:
        raise executor.ExecError("message requires a recipient")
    if not body:
        raise executor.ExecError("message requires a non-empty body")
    return {"out": f"sent to {to}: {body}", "to": to, "body": body,
            "thread": args.get("thread")}


def install_rail(k, *, name: str = SEND_EFFECT) -> str:
    """Register the outbound `message.send` effect and forge a COMMS capability granted
    to Decima: Morta `requires_approval` (DENIED until approved) + an SB1 sandbox profile
    that allows only this effect (network on, to the rail). Returns the capability id."""
    caveats = {
        "effect_class": COMMS,
        "requires_approval": True,          # Morta gate — denied until approved
        # SB1 sandbox: only this effect may run under the cap; network on (to the
        # rail). The durable form pins egress to the provider host.
        "sandbox": {"effects": [name], "network": True},
    }
    return k.integrate_tool(name, lambda _impl, args: _send_handler(args), caveats=caveats)


# -- inbound: capture as untrusted DATA, grouped into a thread ----------------
def _thread_id(channel: str, peer: str) -> str:
    """A stable id for the conversation between Decima and a peer on a channel."""
    return content_id({"thread": nfc(str(peer)), "channel": nfc(str(channel))})


def _ensure_thread(k, author, channel: str, peer: str) -> str:
    """Idempotently assert the conversation's `thread` Cell. Re-receiving on the same
    (channel, peer) lands on the same thread id, so messages accrete into one fold."""
    tid = _thread_id(channel, peer)
    if k.weave().get(tid) is None:
        assert_content(k.weft, author, tid, THREAD,
                       {"peer": nfc(str(peer)), "channel": nfc(str(channel))})
    return tid


def receive(k, sender, body, *, channel: str = "email", author=None) -> dict:
    """Capture an INBOUND message as untrusted DATA. The message is routed through the
    LIVE disposition router (`kernel.ingest`, trusted=False) — so it can only ever be
    remembered as DATA or archived, NEVER elevated to a task/invoke/policy, and its
    imperative content never picks its own disposition. A `message` Cell records the
    raw inbound, grouped into the (channel, sender) `thread` via an `in_thread` edge,
    and bound to the disposition's intake via `captured_as`.

    Returns {message, thread, disposition, action, instruction_eligible, produced}.
    `instruction_eligible` is always False — an inbound message is DATA, not an order."""
    author = author or k.decima_agent_id

    # Route across the trust boundary FIRST: inbound is untrusted by law.
    d = k.ingest(f"{channel}:{sender}", body, trusted=False)

    tid = _ensure_thread(k, author, channel, sender)
    mid = content_id({"message": nfc(str(body)), "from": nfc(str(sender)),
                      "thread": tid, "intake": d["intake"]})
    assert_content(k.weft, author, mid, MESSAGE, {
        "direction": "inbound", "sender": nfc(str(sender)), "channel": nfc(str(channel)),
        "body": nfc(str(body)), "thread": tid, "intake": d["intake"],
        "instruction_eligible": False,          # inbound is DATA, never an instruction
    })
    assert_edge(k.weft, author, mid, IN_THREAD, tid)        # group into the conversation
    assert_edge(k.weft, author, mid, "captured_as", d["intake"])
    return {"message": mid, "thread": tid, "disposition": d["disposition"],
            "action": d["action"], "instruction_eligible": False,
            "produced": d["produced"], "intake": d["intake"]}


# -- outbound: a Morta-gated, sandboxed, audited send ------------------------
def send(k, agent_cell, cap_id, to, body, *, channel: str = "email",
         thread: str | None = None, author=None) -> dict:
    """Send an OUTBOUND message through the Morta-gated, sandboxed `message.send`
    capability. DENIED until the capability is approved (Morta); on success the kernel
    emits an EffectReceipt (audit). Records an outbound `message` Cell grouped into the
    thread and bound to its receipt via `sent_via` (only when the send actually ran).

    Returns {status, result_cell, denied?, message?, thread}."""
    author = author or k.decima_agent_id
    tid = thread or _ensure_thread(k, author, channel, to)

    res = k.invoke(agent_cell, cap_id, {
        "to": nfc(str(to)), "body": nfc(str(body)), "thread": tid,
    })
    out = {"status": res.get("status"), "result_cell": res.get("result_cell"),
           "thread": tid}
    if "denied" in res:                                     # Morta / sandbox refusal
        out["denied"] = res["denied"]
        return out

    # The send ran: record the outbound message Cell and bind it to its receipt.
    mid = content_id({"message": nfc(str(body)), "to": nfc(str(to)),
                      "thread": tid, "receipt": res["result_cell"]})
    assert_content(k.weft, author, mid, MESSAGE, {
        "direction": "outbound", "recipient": nfc(str(to)), "channel": nfc(str(channel)),
        "body": nfc(str(body)), "thread": tid, "receipt": res["result_cell"],
        "instruction_eligible": True,           # our own outbound is a real action
    })
    assert_edge(k.weft, author, mid, IN_THREAD, tid)
    assert_edge(k.weft, author, mid, "sent_via", res["result_cell"])
    out["message"] = mid
    return out


# -- the conversation as a fold over the Weave -------------------------------
def thread(k, id) -> list:
    """The message Cells in a conversation, in log order. Folds the `in_thread` edges
    that point AT the `thread` Cell (its edges_in) — inbound (captured DATA) and
    outbound (sent) messages alike."""
    w = k.weave()
    th = w.get(id)
    if th is None:
        return []
    msgs = [w.get(e["src"]) for e in w.edges_to(th.id, IN_THREAD)]
    return [m for m in msgs if m is not None]
