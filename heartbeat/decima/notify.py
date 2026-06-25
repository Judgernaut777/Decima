"""NOTIFY1 — notifications / alerting over the signed Weft (a NEW capability).

An alert is the moment Decima wants to TELL someone something. The law splits in two
on the box boundary:

  - an IN-BOX notification is just a Cell. Turning a TRIAGE1 `incident` or a WATCH1
    `trigger` into a `notification` never leaves the box, so it needs no outward
    authority at all — it is content + a provenance edge to its source, signed on the
    Weft like any other state. Deduped by source: one source ⇒ one live notification.

  - SENDING a notification OUTWARD (an email, a page, a webhook) LEAVES THE BOX. That
    is an irreversible outward effect, so it composes the same primitives PAY1/the
    browser.publish split already use: a Morta-gated capability (`requires_approval`)
    in a sandbox profile that allows only the `notify.send` effect. `send` is DENIED
    until a human/policy approves the capability; the attempt and the (approved) send
    both land as `result` EffectReceipts on the Weft — audited, never ambient.

Pure composition. It READS triage/watch through their public projection APIs
(`triage.incidents`, `watch`'s `trigger` cells) and never edits them; it registers
its outbound effect through the public `kernel.integrate_tool`; it touches no core
file. Ints, not floats; provenance on the Weft.
"""
from decima import executor, triage, watch
from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc

NOTIFICATION = "notification"
NOTIFIES = "notifies"          # edge: notification → its source cell (provenance)
SENT = "notified"              # edge: notification → its send receipt (audit handle)

SEND_EFFECT = "notify.send"    # the OUTWARD effect — Morta-gated, sandboxed, audited

# Priority lattice. A notification's urgency orders the in-box queue (highest first);
# signed content carries an int rank, not a float, and an unknown priority is a refusal,
# never a silent default that would mis-order a critical alert.
PRIORITY_RANK = {"low": 1, "medium": 2, "high": 3, "urgent": 4}
RANK_PRIORITY = {1: "low", 2: "medium", 3: "high", 4: "urgent"}

# Map a source incident/trigger's own severity onto a notification priority, so a
# critical incident becomes an urgent alert without the caller restating it.
_SEVERITY_PRIORITY = {"low": "low", "medium": "medium", "moderate": "medium",
                      "high": "high", "critical": "urgent"}


def _rank(priority: str) -> int:
    r = PRIORITY_RANK.get(str(priority).lower())
    if r is None:
        raise ValueError(
            f"notify: unknown priority {priority!r}; expected one of {sorted(PRIORITY_RANK)}")
    return r


def _author(k) -> str:
    """The principal that signs notification cells (idempotent by name)."""
    return k.keyring.mint("notifier", "notifier").id


# -- in-box: a notification is just a Cell -----------------------------------
def notify(k, source_cell, title, *, priority="medium", author=None):
    """Raise an IN-BOX `notification` Cell linked to its source (e.g. a TRIAGE1
    incident or a WATCH1 trigger). This stays inside the box, so it needs no outward
    authority — it is content + a `notifies` provenance edge to the source, signed on
    the Weft.

    `source_cell` is a cell id OR a Cell. Deduped BY SOURCE: the notification id is
    content-addressed over the source id, so re-notifying the same source re-asserts
    the same cell (one live notification per source), never a duplicate alert.

    Returns the notification cell id."""
    author = author or _author(k)
    src_id = source_cell if isinstance(source_cell, str) else source_cell.id
    rank = _rank(priority)

    src = k.weave().get(src_id)
    if src is None:
        raise ValueError(f"notify: no such source cell {src_id!r}")

    nid = content_id({"notification": src_id})          # dedupe key = the source
    assert_content(k.weft, author, nid, NOTIFICATION, {
        "title": nfc(str(title)),
        "source": src_id,
        "source_type": src.type,
        "priority": RANK_PRIORITY[rank],
        "priority_rank": rank,
        "status": "unread",
        "sent": False,                                  # not yet pushed outward
    })
    assert_edge(k.weft, author, nid, NOTIFIES, src_id)  # provenance: notification → source
    return nid


def from_incidents(k, *, author=None) -> list:
    """Turn every existing TRIAGE1 `incident` Cell into an in-box notification, priority
    derived from the incident's severity. Composes `triage.incidents` (read-only) — does
    not edit triage. Deduped by source, so re-running re-asserts (no duplicates).
    Returns the notification ids (highest priority first)."""
    out = []
    for inc in triage.incidents(k.weave()):
        sev = inc.content.get("severity", "medium")
        title = (f"{str(sev).upper()} incident: {inc.content.get('key')} "
                 f"({inc.content.get('finding_count')} finding(s))")
        out.append(notify(k, inc.id, title,
                          priority=_SEVERITY_PRIORITY.get(str(sev).lower(), "medium"),
                          author=author))
    return order(k, ids=out)


def from_triggers(k, *, author=None) -> list:
    """Turn every existing WATCH1 `trigger` Cell into an in-box notification. A watcher
    firing is a thing worth surfacing; the trigger names the watcher + the matched cell.
    Composes the public `trigger` projection — does not edit watch. Deduped by source.
    Returns the notification ids (highest priority first)."""
    out = []
    for trg in k.weave().of_type(watch.TRIGGER):
        title = (f"watcher fired: {trg.content.get('watcher_name')} "
                 f"→ {trg.content.get('action')}")
        out.append(notify(k, trg.id, title, priority="high", author=author))
    return order(k, ids=out)


# -- read-side projections ---------------------------------------------------
def notifications(weave) -> list:
    """Every live notification Cell."""
    return weave.of_type(NOTIFICATION)


def source_of(weave, notification_id):
    """The source cell a notification was raised from (via its `notifies` edge)."""
    edges = weave.edges_from(notification_id, NOTIFIES)
    return weave.get(edges[0]["dst"]) if edges else None


def order(k, *, ids=None) -> list:
    """Order notifications highest-priority-first (urgent → low). Restricted to `ids`
    when given (so a batch keeps its own ordering), else the whole inbox. A stable
    secondary sort on the cell id keeps the order deterministic at equal priority."""
    w = k.weave()
    cells = ([w.get(i) for i in ids] if ids is not None else notifications(w))
    cells = [c for c in cells if c is not None]
    cells.sort(key=lambda c: (-c.content.get("priority_rank", 0), c.id))
    return [c.id for c in cells]


# -- outbound: SENDING leaves the box → Morta-gated, sandboxed, audited ------
def _send_handler(channel: str, args: dict) -> dict:
    """The outward channel itself — a deterministic stub standing in for email / a
    pager / a webhook. Reaching here means the Morta gate was cleared (the capability
    was approved). A bad request (missing notification or recipient) raises ExecError →
    a FAILED receipt: a definite no-effect, nothing left the box."""
    notification = nfc(str(args.get("notification", "")))
    recipient = nfc(str(args.get("recipient", "")))
    if not notification:
        raise executor.ExecError("notify.send requires a notification")
    if not recipient:
        raise executor.ExecError("notify.send requires a recipient")
    return {"out": f"sent notification {notification[:8]} to {recipient} via {channel}",
            "notification": notification, "recipient": recipient, "channel": channel}


def install_send(k, *, name: str = SEND_EFFECT, channel: str = "stub-channel") -> str:
    """Register the OUTWARD `notify.send` effect and forge a capability granted to
    Decima: Morta `requires_approval` (a send leaves the box, so a human/policy must
    approve) and a sandbox profile that allows ONLY this effect. Returns the cap id.

    Mirrors PAY1's outward-effect pattern: the registry says what the effect DOES, the
    capability gates WHO may run it AND that approval is required first."""
    caveats = {
        "effect_class": "OUTWARD",
        "requires_approval": True,                       # Morta gate — a send leaves the box
        "sandbox": {"effects": [name], "network": True},  # only this effect; net to the channel
    }
    return k.integrate_tool(name, lambda _impl, args: _send_handler(channel, args),
                            caveats=caveats)


def send(k, agent_cell, cap_id, notification, *, recipient, author=None) -> dict:
    """Push a notification OUTWARD through the Morta-gated `notify.send` capability.
    DENIED until the capability is approved (it leaves the box); the attempt and the
    (approved) send both land as `result` EffectReceipts on the Weft — audited.

    On success, flips the notification's `sent` flag and records a `notified` edge to
    the send receipt (the audit handle). Returns the kernel invoke result, extended
    with the notification id."""
    author = author or _author(k)
    nid = notification if isinstance(notification, str) else notification.id
    n = k.weave().get(nid)
    if n is None:
        raise ValueError(f"send: no such notification {nid!r}")

    res = k.invoke(agent_cell, cap_id, {"notification": nid, "recipient": nfc(str(recipient))})
    res["notification"] = nid
    if res.get("status") == executor.SUCCEEDED:
        cur = k.weave().get(nid)
        assert_content(k.weft, author, nid, NOTIFICATION,
                       {**cur.content, "sent": True, "status": "sent"})
        assert_edge(k.weft, author, nid, SENT, res["result_cell"])  # audit handle
    return res
