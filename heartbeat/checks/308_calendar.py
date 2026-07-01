"""Real calendar engine — WRAP the provider, offline contract (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for EXTERNAL
side-effecting systems — a booking is only real when it lands in the calendar the other
party watches. SCHED1 stays an internal planner over the Weft; `calendar_engine.py` asks
a REAL Google Calendar / Cal.com-style HTTPS provider to CREATE an event, over stdlib
`urllib` (zero deps). This check drives it entirely OFFLINE via an injected fake transport
(the real `urllib` transport is never called), so the oracle stays deterministic and
network-free while proving the full contract:

  - success: a valid event + an injected 200 → a `calendar_event` cell carrying the
    provider's provider_ref and the start/end window; start/end on the cell are ints;
  - invalid window: end <= start is refused BEFORE any request (the fake transport is
    never called) — no cell recorded;
  - HTTPS-only: a non-`https://` endpoint is refused BEFORE any request (the fake
    transport is never called) — the API key never rides a cleartext wire;
  - fail closed: a provider 4xx / error → {"denied": ...} and NO `calendar_event` cell;
  - dispense-don't-disclose: the raw API key never appears in any event payload on the
    Weft — CRED1 applies it inside the broker.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import calendar_engine, secrets

API_KEY = "cal_live_CALENDAR_SUPER_SECRET_KEY"
ENDPOINT = "https://api.cal.com/v2/bookings"

START = 1_800_000_000        # epoch seconds
END = 1_800_003_600          # +1 hour


def _transport(calls, response):
    """A fake calendar-provider transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def _decima(kk):
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== REAL CALENDAR ENGINE (wrapped provider, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("calcom", API_KEY, service="calcom")
    handle = broker.issue("calcom", _decima(kk), "create calendar events")

    event = {
        "title": "Design review with Acme",
        "start": START, "end": END,
        "attendees": ["mailto:alice@acme.example", "mailto:bob@acme.example"],
        "location": "https://meet.example/room-42",
    }

    # 1. SUCCESS — provider creates the event; we record it (ints) on the Weft. ──────────
    calls = []
    ok_resp = (201, {"id": "cal_evt_abc123", "status": "confirmed",
                     "start": START, "end": END})
    res = calendar_engine.schedule(kk, endpoint=ENDPOINT, event=event, credential_handle=handle,
                                   broker=broker, agent_cell=_decima(kk),
                                   transport=_transport(calls, ok_resp))
    assert "calendar_event" in res and res["provider_ref"] == "cal_evt_abc123", res
    assert res["start"] == START and res["end"] == END, res
    assert len(calls) == 1 and calls[0]["url"] == ENDPOINT, calls
    cell = kk.weave().get(res["calendar_event"]).content
    assert cell["provider_ref"] == "cal_evt_abc123" and cell["title"] == event["title"], cell
    assert cell["start"] == START and cell["end"] == END and cell["end"] > cell["start"], cell
    for fld in ("start", "end"):                              # ints only in signed content
        assert isinstance(cell[fld], int) and not isinstance(cell[fld], bool), (fld, cell[fld])
    assert cell["attendees_are_instructions"] is False, cell   # attendee refs are DATA
    assert cell["attendees"] == event["attendees"], cell
    line("  success: injected 200/201 → calendar_event cell with the provider's "
         "provider_ref and start/end window; times are epoch-second ints; attendees "
         "carried as non-instruction data ✓")

    # 2. INVALID WINDOW — end <= start refused BEFORE any request. ─────────────────────
    events_before = len(calendar_engine.events(kk))
    win_calls = []
    bad_event = {**event, "start": END, "end": START}        # end < start
    bad_win = calendar_engine.schedule(kk, endpoint=ENDPOINT, event=bad_event,
                                       credential_handle=handle, broker=broker,
                                       agent_cell=_decima(kk),
                                       transport=_transport(win_calls, ok_resp))
    assert "denied" in bad_win and "window" in bad_win["denied"], bad_win
    assert win_calls == [], "an invalid time window must be refused before any request"
    assert len(calendar_engine.events(kk)) == events_before, "no cell on an invalid window"
    line("  invalid window: end <= start is refused before any request "
         "(transport never called), no cell ✓")

    # 3. HTTPS-only — a non-HTTPS endpoint is refused before any request. ──────────────
    http_calls = []
    bad = calendar_engine.schedule(kk, endpoint="http://api.cal.com/v2/bookings", event=event,
                                   credential_handle=handle, broker=broker,
                                   agent_cell=_decima(kk),
                                   transport=_transport(http_calls, ok_resp))
    assert "denied" in bad and "HTTPS" in bad["denied"], bad
    assert http_calls == [], "a non-HTTPS endpoint must be refused before any request"
    line("  HTTPS-only: a non-HTTPS endpoint is refused before the key is sent "
         "(transport never called) ✓")

    # 4. FAIL CLOSED — a provider 4xx / error → denied, NO calendar_event recorded. ────
    events_before = len(calendar_engine.events(kk))
    err_calls = []
    declined = calendar_engine.schedule(kk, endpoint=ENDPOINT, event=event,
                                        credential_handle=handle, broker=broker,
                                        agent_cell=_decima(kk),
                                        transport=_transport(err_calls, (409, {"error": "slot taken"})))
    assert "denied" in declined and "calendar_engine" in declined["denied"], declined
    assert len(err_calls) == 1, "the request was made, but the 4xx must fail closed"
    assert len(calendar_engine.events(kk)) == events_before, "no calendar_event cell on a provider error"
    line("  fail closed: provider 4xx → {denied} and NO calendar_event cell recorded ✓")

    # 5. DISPENSE-DON'T-DISCLOSE — the raw API key never on the Weft. ──────────────────
    payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert API_KEY not in payloads, "the raw calendar API key must never be written to the Weft"
    line("  no raw API key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → scheduling is wrapped, not reinvented: a real provider (over stdlib urllib, "
         "zero deps) creates the booking; Decima records epoch ints on the Weft, holds the "
         "key in CRED1, treats attendees as data, refuses cleartext, and fails closed.")
