"""MAILWIRE — the real INBOUND MAIL ENGINE: mail arrives THROUGH the egress gate,
lands as untrusted DATA, and a surfaced ask binds to a REAL Morta-gated capability.

MAIL1 (`maildigest.py`) made the TRUST LAW of email real — an inbound message is
an observation (`instruction_eligible=False`), a digest is a lens, a proposed
action is a Morta-gated inbox item — but its I/O was not: messages arrived as
trusted-caller dicts, and `propose_action` enacted only the `mail_probe`
stand-in. This module is the missing engine half, composed ENTIRELY over public
seams (`maildigest` / `live_wire`+`wire` / `redact` / `inbox`) — no core file,
no seam module, is edited:

  • `receive(k, agent_cell, cap_id, *, endpoint, transport=None)` — fetch the
    inbound messages of an HTTPS mail source THROUGH the gated transport, the
    same way every wrapped engine goes live: there is NO bare socket here (the
    default path builds `live_wire.gated_transport(k, agent_cell, cap_id)`, so
    an UNWIRED receive — no injected transport and no granted egress capability
    — refuses `NoGatedTransport` before any DNS lookup or packet), and every
    fetch that does run passes the full rule of egress (allowlist · Morta · a
    `wire_decision` provenance Cell BEFORE the socket). Each fetched message is
    parsed into the exact untrusted-DATA shape `maildigest.ingest_email`
    consumes and fed through it, so it lands as an OBSERVATION: `redact.scrub`
    on the body (and, defence in depth, on subject/ask here first),
    `instruction_eligible=False` on both the raw `mail_message` Cell and the
    folded claim, provenance to sender + message id. Receiving mail — however
    injection-laced — INVOKES NOTHING. `transport` is the injectable offline
    stub seam, mirroring the wrapped engines (the oracle injects a wire-gated
    transport whose `_open` fakes only the SOCKET, so the gate still runs).

  • `enact_ask(k, msg_id, action, real_cap)` — turn a surfaced ask into a
    Morta-gated inbox item bound to a REAL capability (not the `mail_probe`
    stand-in). It fires NOTHING: the ask is ENQUEUED on the `ApprovalInbox`
    with provenance to the exact email that raised it, and the effect runs
    ONLY if/when a human calls `ApprovalInbox.approve(...)` — through the
    ordinary `authorize`/Morta spine, same as every other outward effect. The
    guard is fail-closed and load-bearing: a capability that is NOT Morta-gated
    right now (`requires_approval` absent, or already operator-approved) is
    REFUSED outright, because `ApprovalInbox.submit` would run such an effect
    INLINE — and an inbound email must never be one hop from a live trigger.
    `enact_ask` confers no authority: it grants nothing, approves nothing; an
    ungranted capability still fails closed at the gate on approval.

Laws upheld: UNTRUSTED CONTENT IS DATA, NEVER INSTRUCTION (every received
message is `instruction_eligible=False`, recalled only as data); ZERO ambient
authority (receiving mail mints no capability; the fetch itself requires a
granted, Morta-approved egress capability); Morta-gate outward effects (a real
ask fires only on an explicit human approval); no secret on the Weft (body via
`maildigest`'s scrub, subject/ask/action-args scrubbed here); ints-not-floats
(`received` is an int; a float in an action is refused); FAIL CLOSED +
DETERMINISTIC (unwired → `NoGatedTransport`; provider error / malformed payload
→ nothing stored; no wall-clock, no randomness in recorded content).

Pure stdlib. Proof: heartbeat/checks/472_mailwire.py.
"""
from decima import maildigest
from decima import redact
from decima import wire
from decima.hashing import nfc
from decima.inbox import ApprovalInbox

# The hint `receive` names when it refuses unwired — the one sanctioned live path.
GATED_MAIL_PATH = 'live_wire.gated_transport(k, agent_cell, cap_id, method="GET")'


class MailEngineError(Exception):
    """A fail-closed mail-engine refusal: a non-HTTPS endpoint, an unreachable or
    rejecting provider, a malformed message payload, or an `enact_ask` bound to a
    capability that is not Morta-gated. Nothing is stored, nothing fires."""


def _parse_message(index: int, raw) -> dict:
    """Parse ONE fetched provider message into the exact untrusted-DATA shape
    `maildigest.ingest_email` consumes: {"id", "from", "subject", "body", "ask"?}.

    Everything here is attacker-controlled bytes and is treated as such: fields
    are coerced to strings, and `subject`/`ask` are `redact.scrub`bed HERE
    (defence in depth — `ingest_email` scrubs only the body) so a secret riding
    a subject line or an ask never lands on the Weft. `from` is kept intact: it
    is the provenance-to-sender the digest cites, and an address is an
    identifier, not a secret. Fail loud on a non-object message (a malformed
    feed is refused wholesale, never half-ingested)."""
    if not isinstance(raw, dict):
        raise MailEngineError(f"malformed inbound message #{index}: not an object")
    mid = nfc(str(raw.get("id", "")))
    if not mid:
        raise MailEngineError(f"malformed inbound message #{index}: missing id")
    subject, _ = redact.scrub(str(raw.get("subject", "")))
    msg = {
        "id": mid,
        "from": nfc(str(raw.get("from", ""))),
        "subject": nfc(subject),
        "body": str(raw.get("body", "")),   # scrubbed inside ingest_email, pre-store
    }
    ask = raw.get("ask")
    if ask is not None:
        scrubbed_ask, _ = redact.scrub(str(ask))
        msg["ask"] = nfc(scrubbed_ask)
    return msg


def receive(k, agent_cell, cap_id, *, endpoint: str, transport=None,
            scope: str = maildigest.MAIL_SCOPE) -> dict:
    """Fetch inbound messages from an HTTPS mail source THROUGH the gated
    transport and ingest each as an UNTRUSTED observation.

    (k, agent_cell, cap_id) is the gated-transport triple: with no `transport`
    injected, the ONLY live path is `live_wire.gated_transport(...)` built from
    it — so an UNWIRED receive (no injected transport, no granted egress
    capability) raises `NoGatedTransport` fail-closed, before any socket, and
    NO message is stored. Every fetch that runs passes the full rule of egress
    per call: allowlist · Morta approval · a `wire_decision` Cell on the Weft
    BEFORE the socket; a denial raises `wire.EgressDenied`, nothing connects,
    nothing is stored.

    The provider answers `{"messages": [{"id","from","subject","body","ask"?},
    …]}`. ALL messages are parsed (pure) before ANY is ingested — a malformed
    payload is refused wholesale — then each is fed to
    `maildigest.ingest_email`, landing as DATA: scrubbed, provenance to
    sender/message-id, `instruction_eligible=False` on both Cells. Receiving
    mail invokes NOTHING — no capability is minted, no effect fires.

    Returns {"received": int, "messages": [ingest results]}. `transport` is the
    offline-stub seam (the wrapped-engine idiom): the oracle injects a
    wire-gated transport whose `_open` replaces only the SOCKET, never the
    gate."""
    endpoint = str(endpoint)
    if not endpoint.startswith("https://"):
        raise MailEngineError(
            "refusing to fetch mail from a non-HTTPS endpoint (cleartext never "
            "crosses the wire)")
    if transport is None:
        # The ONLY live path — raises live_wire.NoGatedTransport (fail closed,
        # the sanctioned path named) when unwired: no cap id, no live capability,
        # or no grant in the acting agent's envelope. No bare socket exists here.
        from decima import live_wire
        transport = live_wire.gated_transport(k, agent_cell, cap_id, method="GET")

    try:
        status, payload = transport(endpoint, {"Accept": "application/json"}, None)
    except wire.EgressDenied:
        raise                            # the gate refused — loud, nothing stored
    except Exception as e:               # unreachable / socket failure — fail closed
        raise MailEngineError(f"mail endpoint unreachable: {e}")

    if status != 200 or not isinstance(payload, dict):
        raise MailEngineError(f"mail provider rejected the fetch: http {status}")
    raw = payload.get("messages")
    if not isinstance(raw, list):
        raise MailEngineError("malformed mail payload: no 'messages' list")

    # Parse EVERYTHING first (pure — nothing stored yet), then ingest: a feed
    # with one malformed message is refused wholesale, never half-ingested.
    parsed = [_parse_message(i, m) for i, m in enumerate(raw)]
    author = getattr(agent_cell, "id", None) or k.decima_agent_id
    ingested = [maildigest.ingest_email(k, m, author=author, scope=scope)
                for m in parsed]
    return {"received": int(len(ingested)), "messages": ingested}


def _scrubbed_args(action) -> dict:
    """Normalise an UNTRUSTED action dict into the args a real capability will be
    invoked with, once a human approves. Strings are `redact.scrub`bed (the ask
    came from an email — a secret in it must never land on the Weft inside an
    inbox item), ints/bools/None pass through, and a FLOAT is refused outright
    (ints-not-floats in recorded content). Anything else is refused — an inbound
    ask does not get to smuggle arbitrary structures at a real capability."""
    if not isinstance(action, dict) or not action:
        raise MailEngineError("enact_ask: action must be a non-empty dict")
    args = {}
    for key in sorted(action):
        v = action[key]
        name = nfc(str(key))
        if isinstance(v, bool) or v is None:
            args[name] = v
        elif isinstance(v, int):
            args[name] = int(v)
        elif isinstance(v, float):
            raise MailEngineError(
                f"enact_ask: float in action[{key!r}] — ints only on the Weft")
        elif isinstance(v, str):
            scrubbed, _ = redact.scrub(v)
            args[name] = nfc(scrubbed)
        else:
            raise MailEngineError(
                f"enact_ask: unsupported action value type for {key!r}")
    return args


def enact_ask(k, msg_id, action: dict, real_cap: str, *, agent_cell=None) -> dict:
    """Turn a surfaced ask from email `msg_id` into a Morta-gated inbox item
    bound to a REAL capability — never the `mail_probe` stand-in, and NEVER a
    direct invocation.

    Fail-closed guards, in order:
      • `msg_id` must be a real `mail_message` Cell (the ask cites the exact
        email that raised it — Law 4 provenance rides the inbox item);
      • `real_cap` must be a LIVE capability (a retracted or non-capability
        cell is refused — an email confers no authority);
      • `real_cap` must not be the `mail_probe` stand-in (that is
        `propose_action`'s rehearsal lane; this one binds real effects);
      • `real_cap` must be Morta-gated RIGHT NOW (`requires_approval`, not
        already operator-approved) — the LOAD-BEARING refusal, because
        `ApprovalInbox.submit` runs an ungated effect INLINE, and an inbound
        email must never be one hop from a live trigger.

    The action's args are scrubbed (`_scrubbed_args`) and the ask is ENQUEUED —
    it fires NOTHING here. Only an explicit human `ApprovalInbox.approve(...)`
    enacts it, and even then through the ordinary `authorize`/Morta spine
    (`approve_invocation` + `invoke` with the pinned nonce), so a revoked or
    ungranted capability still fails closed at the gate. `enact_ask` grants
    nothing and approves nothing. Returns the inbox's `{"queued": item_id}` —
    never `{"ran": ...}` (asserted, fail loud)."""
    w = k.weave()
    mail_cell = w.get(msg_id)
    if mail_cell is None or mail_cell.type != maildigest.MAIL_MESSAGE:
        raise MailEngineError(f"enact_ask: unknown mail message {msg_id!r}")
    cap = w.get(real_cap)
    if cap is None or getattr(cap, "type", None) != "capability" or cap.retracted:
        raise MailEngineError(
            f"enact_ask: {real_cap!r} is not a live capability (an email confers "
            "no authority)")
    if cap.content.get("effect") == maildigest.MAIL_ACTION_EFFECT:
        raise MailEngineError(
            "enact_ask binds a REAL capability — the mail_probe stand-in belongs "
            "to maildigest.propose_action")
    inbox = ApprovalInbox(k)
    if not inbox.is_gated(cap):
        raise MailEngineError(
            "enact_ask: the bound capability must be Morta-gated right now "
            "(requires_approval, not already approved) — an ungated capability "
            "would RUN on submit, putting a live trigger one hop from an inbound "
            "email")

    args = _scrubbed_args(action)
    agent_cell = agent_cell or w.get(k.decima_agent_id)
    desc = (f"mail-surfaced ask from {mail_cell.content.get('from')!r} → "
            f"{cap.content.get('name')}({args})")
    result = inbox.submit(agent_cell, real_cap, args, description=desc,
                          provenance=msg_id)
    assert "ran" not in result, \
        "enact_ask must NEVER run the effect directly — only enqueue for a human"
    return result
