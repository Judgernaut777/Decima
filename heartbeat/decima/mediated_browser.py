"""MEDIATED BROWSER — fetch through the gate; a page is DATA; an action is a PROPOSAL
(Phase 5 · full-surface mediated I/O, the "mediated browser" half).

Decima already has a stub `browser.observe`/`browser.publish` split (specs/BROWSER_WORKER.md,
kernel._boot) and, since Cycle-51, a real egress gate: a live fetch may run ONLY through a
wire-gated transport (`live_wire.gated_*_transport`), constructed from a granted, Morta-
approved egress capability — an unwired/bare path raises `live_wire.NoGatedTransport` before
any socket (`decima/live_wire.py`, `decima/wire.py`). This module is the mediated-browser
composition over that gate: a real page fetch, wired the ONLY sanctioned way, whose result is
governed by the SAME recall-vs-instruct law the browser receipt, quarantine, and memory
already hold (`disposition.py`, `memory.py`, `quarantine.py`) — **untrusted content is DATA,
never instruction** — and whose only path to an outward action is a Morta-gated proposal
(`inbox.ApprovalInbox`) that fires nothing until a human approves.

Three seams, composing PUBLIC APIs only (no core edit, no edit to live_wire/wire/memory/
disposition/redact/inbox):

  fetch(k, agent_cell, cap_id, url, *, transport=None) -> dict
      Retrieve `url` THROUGH the gated transport. `transport` is the same raw-GET shape
      `live_wire.gated_get_raw_transport(k, agent_cell, cap_id)` exposes —
      `transport(url, headers, body-ignored) -> (status, {"body": bytes|str, ...})` — so the
      wire's full rule of egress (allowlist · Morta · a `wire_decision` Cell BEFORE the
      socket) runs on every fetch; a bare/unwired call (`transport=None`) raises
      `live_wire.NoGatedTransport` HERE, first — fail closed, no page is ever stored. The
      fetched body is `redact.scrub`bed (no secret rides the Weft — CRED1's law) and then
      stored as an UNTRUSTED observation via `memory.remember(..., instruction_eligible=
      False)`, grounded by a `browse_fetch` receipt Cell (url + status, DATA, Law 4
      provenance) and linked `about` the url (an entity), so `read()` can find it again.
      This is offline-testable exactly like the wrapped engines: inject a STUB transport (or
      a fake `_open` under `live_wire.gated_get_raw_transport`) and the full contract —
      gate, provenance, storage — runs with no network.

  read(k, url) -> dict
      Recall the stored page for `url` AS DATA, with provenance (`memory.why`) — never as an
      instruction. There is no path from `read` (or `fetch`) to an effect: reading a page,
      however imperative its text, invokes nothing.

  propose_from_page(k, url, action) -> dict
      Turn an action DERIVED from a fetched page into a Morta-gated `ApprovalInbox` item.
      This ALWAYS enqueues — the capability behind it is minted with
      `requires_approval=True` and the item is `enqueue`d directly (never `k.invoke`d) — so
      a page (even one carrying an embedded command) can never auto-enact anything; only an
      explicit human `ApprovalInbox.approve(item_id)` runs the action, through the same
      `capability.authorize`/Morta spine as any other gated effect.

Deterministic; every recorded numeric is an int (`status`); the module mints/grants its OWN
capability through existing kernel APIs (`k._assert_cap` + `k.grant`) and registers its own
hermetic effect (`browse_probe`, never `echo`) — zero ambient authority, authority flows
downhill only. Proof: heartbeat/checks/450_mediated_browser.py.
"""
from decima import executor, live_wire, memory, redact
from decima.hashing import content_id, nfc
from decima.model import assert_content
from decima.inbox import ApprovalInbox

BROWSE_FETCH = "browse_fetch"          # the on-Weft fetch receipt: url + status (no body)
PROBE_EFFECT = "browse_probe"          # this lane's OWN hermetic effect — never 'echo'
ACT_CAP_NAME = "mediated_browser.act"  # the Morta-gated capability an action proposes through


class MediatedBrowserError(Exception):
    """A mediated-browser failure (a non-2xx / unparseable page response). Fail closed:
    no `browse_fetch` receipt and no page observation is recorded."""


def _require_gated(transport):
    """Fail-closed gate check, mirroring every swept engine's `_urllib_transport` default
    (Phase 2 · GO LIVE): a bare/unwired fetch (`transport=None`) refuses HERE, before any
    request is even attempted, naming the ONE sanctioned path to the network."""
    if transport is None:
        raise live_wire.NoGatedTransport(
            "mediated_browser",
            hint="live_wire.gated_get_raw_transport(k, agent_cell, cap_id)")
    return transport


def fetch(k, agent_cell, cap_id, url, *, transport=None) -> dict:
    """Fetch `url` THROUGH the gated egress transport and store the page as UNTRUSTED DATA.

    `transport(url, headers, body) -> (status, {"body": bytes|str, ...})` — inject a real
    `live_wire.gated_get_raw_transport(k, agent_cell, cap_id)` (live) or a STUB (offline
    tests); a bare default is refused by `_require_gated` first. On a 2xx response the body
    is redact-scrubbed (no secret ever lands on the Weft) and recorded:
      - a `browse_fetch` receipt Cell (url, status, redacted classes; instruction_eligible=
        False) — provenance for the observation, never obeyed;
      - a memory claim via `memory.remember(..., instruction_eligible=False)` — the LOAD-
        BEARING line: an observed page is DATA, never an instruction, by construction.
    Returns {"page": <claim id>, "fetch": <receipt id>, "status": int, "redacted": [...]}.
    Raises `MediatedBrowserError` on a non-2xx / unparseable response (no cell recorded)."""
    transport = _require_gated(transport)
    author = k.decima_agent_id

    status, payload = transport(url, {"Accept": "text/html"}, None)
    if not isinstance(payload, dict):
        raise MediatedBrowserError(f"unparseable page response (status {status})")
    if not (200 <= int(status) < 300):
        raise MediatedBrowserError(f"page fetch refused/failed: status {status}")

    body = payload.get("body", b"")
    text = (body.decode("utf-8", errors="replace")
            if isinstance(body, (bytes, bytearray)) else str(body))

    # Scrub BEFORE anything is recorded — a raw secret in the fetched bytes never rides
    # the Weft (CRED1's law), and this also records its own `redaction` provenance Cell.
    screening = redact.screen(text, k, author=author)
    scrubbed = screening.scrubbed

    fid = content_id({"browse_fetch": nfc(url), "status": int(status), "at": k.weft.head})
    assert_content(k.weft, author, fid, BROWSE_FETCH, {
        "url": nfc(url),
        "status": int(status),
        "redacted_classes": screening.classes,
        "instruction_eligible": False,      # a fetch receipt is DATA, never an instruction
    })

    # THE LOAD-BEARING LINE: the fetched page is remembered as UNTRUSTED data — never an
    # instruction. Flip this to instruction_eligible=True and the page becomes obeyable.
    cid = memory.remember(k.weft, author, scrubbed, evidence_src=fid,
                          instruction_eligible=False, about=url)

    return {"page": cid, "fetch": fid, "status": int(status), "redacted": screening.classes}


def read(k, url) -> dict:
    """Recall the most recently fetched page for `url` AS DATA, with provenance — never as
    an instruction. Returns {"found": False, "url": ...} if nothing was ever fetched, else
    {"found": True, "url", "page", "text", "instruction_eligible", "provenance"}. There is
    no path from here to an effect: reading a page invokes nothing, however imperative its
    text reads."""
    w = k.weave()
    eid = memory.entity_id(url)
    edges = w.edges_to(eid, "about")
    if not edges:
        return {"found": False, "url": nfc(url)}
    claim_id = edges[-1]["src"]              # the most recently asserted observation
    cell = w.get(claim_id)
    return {
        "found": True,
        "url": nfc(url),
        "page": claim_id,
        "text": cell.content.get("proposition"),
        "instruction_eligible": cell.content.get("instruction_eligible"),
        "provenance": memory.why(w, k.weft, claim_id),
    }


def _browse_probe_effect(impl, args):
    """The handler for an action proposed from a page. Purely record-keeping — ALL
    enforcement lives in the Morta gate (`requires_approval`) on the capability this effect
    runs through; this function only ever executes after an explicit human approval."""
    return {"out": {"acted_on": args.get("url"), "action": args.get("action")}}


executor.register(PROBE_EFFECT, _browse_probe_effect)


def _act_capability(k, agent_cell) -> str:
    """Mint (via the kernel's own `_assert_cap` + `grant` — zero ambient authority, no new
    mint path) and grant this lane's Morta-gated capability. `requires_approval=True` is
    what makes `propose_from_page` a PROPOSAL and never a direct enactment."""
    cap_id = k._assert_cap(ACT_CAP_NAME, PROBE_EFFECT, caveats={"requires_approval": True})
    k.grant(cap_id, agent_cell.id)
    return cap_id


def propose_from_page(k, url, action) -> dict:
    """Turn an action DERIVED from a fetched page into a Morta-gated inbox proposal. This
    ALWAYS enqueues (never invokes) — a page can never auto-enact anything, however
    imperative its content. Returns {"proposal": <item id>, "status": "pending", "url",
    "action"}. A human must call `ApprovalInbox(k).approve(item_id)` — running the SAME
    ocap/Morta spine as any gated effect — before anything fires."""
    agent_cell = k.weave().get(k.decima_agent_id)
    cap_id = _act_capability(k, agent_cell)
    inbox = ApprovalInbox(k)
    page = read(k, url)
    item_id = inbox.enqueue(
        agent_cell, cap_id, {"url": nfc(url), "action": nfc(str(action))},
        description=f"act on page {nfc(url)}: {nfc(str(action))}",
        provenance=page.get("page"))
    return {"proposal": item_id, "status": "pending", "url": nfc(url), "action": nfc(str(action))}
