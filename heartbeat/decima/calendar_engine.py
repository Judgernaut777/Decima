"""Real calendar engine — WRAP the provider, never roll your own scheduling (dep policy).

Decima's policy: recreate the design in pure stdlib, but for EXTERNAL side-effecting
systems WRAP THE REAL ENGINE rather than reimplement it — a booking is only real when it
lands in the actual calendar the other party watches. SCHED1 (`scheduling.py`) stays an
INTERNAL planner over the Weft (proposing/holding slots on the Log); this module
COMPLEMENTS it by asking a REAL calendar provider (a Google Calendar / Cal.com-style
HTTPS API) to CREATE an event/booking in the real calendar. The provider is just an
HTTPS API, so the real engine rides stdlib `urllib` with ZERO pip dependencies: real
engine, still pure-stdlib.

GUARDRAILS (mirroring the tax engine / OIDC engine):
  - **HTTPS-only** — `create_event` refuses to send the API key to a non-`https://`
    endpoint before any request is made (never leak the key in cleartext).
  - **key via CRED1** — the provider API key lives in the secrets broker; `schedule`
    calls `broker.use_secret`, which applies the key INSIDE the broker (never returned,
    never logged, never on the Weft). The raw key never appears in a `calendar_event`
    cell or audit.
  - **end > start invariant** — start/end are epoch-second INTS validated (end > start)
    BEFORE any request; a bad window is refused without touching the wire.
  - **attendees are UNTRUSTED DATA** — attendee refs are carried as data, never as
    instructions, and are marked non-instruction on the recorded cell.
  - **fail closed** — a provider 4xx / declared error, an unreachable endpoint, or a
    denied credential records NO `calendar_event` cell and returns `{"denied": reason}`.
  - **ints only in signed content** — start/end are epoch-second ints; no float ever
    enters a value that lands on the Weft.
  - **transport seam** — `create_event` takes a `transport(url, headers, body) ->
    (status, json)`; the default is a real `urllib` POST; tests inject a fake, so the
    offline oracle exercises the full contract with NO network.

Composes public secrets / model / kernel APIs only. No core edit; does not touch
scheduling.py.
"""
import json

from decima.model import assert_content
from decima.hashing import content_id

CALENDAR_EVENT = "calendar_event"    # the on-Weft record of a provider-created event (no key)


class CalendarEngineError(Exception):
    """A calendar-engine failure — no `calendar_event` may be recorded (fail closed).
    Covers a non-HTTPS endpoint, an invalid time window (end <= start), an
    unreachable/timed-out endpoint, and a provider 4xx/error."""


def _urllib_transport(url: str, headers: dict, body: str):
    """The real transport: a stdlib `urllib` POST (no pip dep). Returns
    (status_code, parsed_json). A 4xx/5xx surfaces as (code, error-json) rather than
    raising, so `create_event` decides success vs. definite error. A transport-level
    failure (DNS, timeout, TLS) raises — `create_event` maps that to
    `CalendarEngineError` (unreachable). Never used by the offline oracle (tests inject
    a fake transport)."""
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:                       # 4xx/5xx carry a JSON body
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": f"http {e.code}"}


def _require_epoch(name: str, v):
    """Guard that a time value the engine will sign is an epoch-second int (never a
    float/bool)."""
    if not isinstance(v, int) or isinstance(v, bool):
        raise CalendarEngineError(f"{name} must be an int (epoch seconds), got {v!r}")
    return int(v)


def create_event(secret_key: str, event: dict, *, transport=None) -> dict:
    """Create an event/booking in the REAL calendar by asking the provider.

    `event` describes the booking — `endpoint` (the provider's HTTPS calendar URL),
    `title`/`summary`, `start` (int, epoch seconds), `end` (int, epoch seconds, > start),
    `attendees` (UNTRUSTED refs, carried as data), and `location`. POSTs it over stdlib
    `urllib` and returns the provider's answer: {provider_ref, status, start:int,
    end:int}. Times are epoch-second ints (no floats).

    The end > start invariant and the int-ness of start/end are validated BEFORE any
    request. HTTPS-only: a non-`https://` endpoint is refused BEFORE the key touches the
    wire. Raises `CalendarEngineError` on a bad window, a non-HTTPS endpoint, an
    unreachable endpoint, or a definite provider error (4xx / error body) — the caller
    (`schedule`) fails closed."""
    transport = transport or _urllib_transport

    endpoint = str(event.get("endpoint", ""))
    if not endpoint.startswith("https://"):
        # Never put the API key on the wire in cleartext. Refuse before sending.
        raise CalendarEngineError("refusing to send the API key to a non-HTTPS calendar endpoint")

    # end > start invariant — validated BEFORE any request touches the wire.
    start = _require_epoch("start", event.get("start"))
    end = _require_epoch("end", event.get("end"))
    if end <= start:
        raise CalendarEngineError(f"invalid window: end ({end}) must be > start ({start})")

    title = str(event.get("title") or event.get("summary") or "")
    # Attendees are UNTRUSTED DATA — carried verbatim as data, never instructions.
    attendees = [str(a) for a in event.get("attendees", [])]

    payload = {
        "summary": title,
        "start": int(start),
        "end": int(end),
        "attendees": attendees,
        "location": event.get("location"),
    }
    body = json.dumps(payload)
    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        status, resp = transport(endpoint, headers, body)
    except Exception as e:                                    # network/timeout — unreachable
        raise CalendarEngineError(f"calendar endpoint unreachable: {e}")

    if not isinstance(resp, dict):
        raise CalendarEngineError(f"unparseable calendar response (status {status})")
    if status in (200, 201) and (resp.get("id") or resp.get("provider_ref")):
        # The provider created the event — fold the returned window as epoch ints.
        return {
            "provider_ref": resp.get("provider_ref") or resp.get("id"),
            "status": str(resp.get("status", "confirmed")),
            "start": _require_epoch("start", resp.get("start", start)),
            "end": _require_epoch("end", resp.get("end", end)),
        }
    err = resp.get("error_description") or resp.get("error") or f"http {status}"
    raise CalendarEngineError(f"provider rejected the event: {err}")   # definite error


def schedule(k, *, endpoint: str, event: dict, credential_handle: str, broker,
             agent_cell, transport=None) -> dict:
    """Create a REAL calendar event and record it on the Weft (fail closed).

    Resolves the provider API key via CRED1 (`broker.use_secret`, which applies the key
    INSIDE the broker and never discloses it), runs `create_event` on `event` against the
    HTTPS `endpoint`, and on success asserts a `calendar_event` cell carrying
    title/start/end/provider_ref/attendees (start/end as epoch-second ints, attendees
    marked non-instruction DATA — NEVER the key). Returns
    {calendar_event: <cell id>, provider_ref, start, end}.

    On a denied credential (revoked/unauthorized/over-budget) or any engine error
    (invalid window, non-HTTPS, unreachable, provider 4xx) it records NO cell and returns
    {"denied": reason}."""
    ev = {**event, "endpoint": endpoint}
    try:
        r = broker.use_secret(
            agent_cell, credential_handle,
            lambda key: create_event(key, ev, transport=transport))
    except CalendarEngineError as e:
        return {"denied": f"calendar_engine: {e}"}           # fail closed — no event cell
    if "denied" in r:
        return {"denied": r["denied"]}                       # credential handle denied
    result = r["ok"]

    # Attendees are UNTRUSTED refs — carried as data, flagged non-instruction.
    attendees = [str(a) for a in ev.get("attendees", [])]
    content = {
        "title": str(ev.get("title") or ev.get("summary") or ""),
        "start": _require_epoch("start", result["start"]),
        "end": _require_epoch("end", result["end"]),
        "provider_ref": result.get("provider_ref"),
        "status": result.get("status"),
        "location": ev.get("location"),
        "attendees": attendees,
        "attendees_are_instructions": False,     # attendee refs are DATA, never instructions
    }
    # Content-addressed by the event body (re-creating identical inputs is idempotent and
    # an event keeps one identity on the Log).
    cid = content_id({"calendar_event": content})
    assert_content(k.weft, k.decima_agent_id, cid, CALENDAR_EVENT, content)
    return {
        "calendar_event": cid,
        "provider_ref": content["provider_ref"],
        "start": content["start"],
        "end": content["end"],
    }


def events(k) -> list:
    """All folded `calendar_event` cells on the Weft."""
    return list(k.weave().of_type(CALENDAR_EVENT))
