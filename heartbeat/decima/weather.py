"""WEATHER1 — a weather capability: an external data source, UNTRUSTED like the web.

A weather observation comes from OUTSIDE Decima. The outside world is never an
instruction, so this module invents no new trust path. `fetch` routes every
observation straight through the kernel's public `ingest(source, text,
trusted=False)` (INTAKE1 → DISP1): the disposition router captures it as DATA
(`instruction_eligible=False`) — an injection-laced payload dressed up as a
weather report is detected as data and remembered (flagged), never obeyed. The
item never selects its own disposition; Decima (not the payload) decides.

On top of that untrusted capture WEATHER1 records a typed `weather_reading` Cell
linked to its `weather_location` with an `of_location` edge (provenance on the
Weft, not a side table), and links the reading back to the intake that grounds
it. Readings carry `instruction_eligible=False` like any observed datum, and all
temperatures are INTEGER units (WEFT §4/§7: never a float). No ambient authority:
this lane grants nothing and proposes nothing — readings can only be DATA.

  - fetch(k, location, observation) → ingest an observation as untrusted DATA and
    store it as a `weather_reading` (ints), linked to the location;
  - current(k, location) → the latest reading for a location;
  - forecast(k, location, days) → a deterministic stub forecast (ints).

Public model / kernel API only — no core edit.
"""
from __future__ import annotations

from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc

WEATHER_LOCATION = "weather_location"
WEATHER_READING = "weather_reading"
OF_LOCATION = "of_location"          # reading —of_location→ weather_location
FROM_INTAKE = "from_observation"     # reading —from_observation→ intake (provenance)


def location_id(location: str) -> str:
    """Content-address a location by name (nfc) so re-fetching the same place keeps
    one identity and registration is idempotent."""
    return content_id({"weather_location": nfc(location)})


def reading_id(location: str, seq: int) -> str:
    """A reading is identified by its location and the Weft position it was folded
    at — append-only, so each observation is a distinct Cell in order."""
    return content_id({"weather_reading": nfc(location), "seq": int(seq)})


def add_location(k, location: str) -> str:
    """Register a `weather_location` Cell and return its id. The location carries NO
    trust of its own — observations fetched for it are still ingested untrusted."""
    lid = location_id(location)
    assert_content(k.weft, k.decima_agent_id, lid, WEATHER_LOCATION,
                   {"name": nfc(location), "origin": "weather"})
    return lid


def _coerce_int(value, default: int = 0) -> int:
    """Temps are INTEGER units, never floats (WEFT §4/§7). Coerce a payload number
    to int at the boundary; a non-numeric payload (e.g. an injection string) falls
    back to the default rather than crashing the capture."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _observation_text(location: str, observation: dict) -> str:
    """Flatten an observation to the text the disposition router classifies AS DATA.
    Includes any free-text the payload carries (e.g. a `summary`) so an injection
    hidden there is seen and flagged by the router — never executed."""
    temp = observation.get("temp_c")
    summary = (observation.get("summary") or "").strip()
    head = f"weather observation for {nfc(location)}"
    if temp is not None:
        head += f": {_coerce_int(temp)} degrees"
    return (head + "\n" + nfc(summary)).strip() if summary else head


def fetch(k, location: str, observation: dict) -> dict:
    """Ingest a weather observation as UNTRUSTED data and store it as a reading.

    The observation is routed through the PUBLIC `kernel.ingest(source=
    f"weather:{location}", text, trusted=False)`: the disposition router captures
    it as DATA (`instruction_eligible=False`) and, if the payload smuggles an
    injection, remembers it flagged — it can never become a task/invoke/policy.

    On top of that capture a typed `weather_reading` Cell is folded (temps coerced
    to INTEGER units, `instruction_eligible=False`), linked to its location with an
    `of_location` edge and back to the intake with a `from_observation` edge.
    Returns {location, reading, intake, disposition, action, temp_c}.
    """
    lid = add_location(k, location)

    text = _observation_text(location, observation)
    # UNTRUSTED: the kernel auto-disposes. trusted defaults to False; we are explicit
    # because that default IS the law here — a weather payload is never an instruction.
    disp = k.ingest(f"weather:{location}", text, trusted=False)

    seq = k.weft.count() + 1
    rid = reading_id(location, seq)
    temp_c = _coerce_int(observation.get("temp_c"))
    content = {
        "location": nfc(location),
        "temp_c": temp_c,
        "humidity": _coerce_int(observation.get("humidity")),
        "wind_kph": _coerce_int(observation.get("wind_kph")),
        "summary": nfc((observation.get("summary") or "").strip()),
        "seq": seq,
        "instruction_eligible": False,   # observed from outside → DATA, never an instruction
        "trusted": False,
    }
    assert_content(k.weft, k.decima_agent_id, rid, WEATHER_READING, content)
    assert_edge(k.weft, k.decima_agent_id, rid, OF_LOCATION, lid)
    assert_edge(k.weft, k.decima_agent_id, rid, FROM_INTAKE, disp["intake"])

    return {
        "location": lid,
        "reading": rid,
        "intake": disp["intake"],
        "disposition": disp["disposition"],
        "action": disp["action"],
        "temp_c": temp_c,
    }


def readings(k, location: str) -> list:
    """All `weather_reading` Cells for a location, oldest-first (walks the
    `of_location` edges into the location — provenance on the log)."""
    w = k.weave()
    lid = location_id(location)
    loc = w.get(lid)
    if loc is None or loc.type != WEATHER_LOCATION:
        return []
    cells = [w.get(e["src"]) for e in w.edges_to(lid, OF_LOCATION)]
    cells = [c for c in cells if c is not None and c.type == WEATHER_READING]
    return sorted(cells, key=lambda c: int(c.content.get("seq", 0)))


def current(k, location: str):
    """The latest reading Cell for a location, or None if none has been fetched."""
    rs = readings(k, location)
    return rs[-1] if rs else None


def forecast(k, location: str, days: int) -> list:
    """A DETERMINISTIC stub forecast for the next `days`, as integer temps.

    Pure and reproducible: seeded by the location name and (if present) the latest
    reading's temperature, with an integer-only daily drift. No network, no model,
    no floats — same inputs always yield the same forecast. The forecast is DATA
    (`instruction_eligible=False`) like every other observation here.
    """
    days = max(0, int(days))
    cur = current(k, location)
    base = int(cur.content.get("temp_c", 15)) if cur is not None else 15
    seed = sum(ord(ch) for ch in nfc(location)) % 7        # deterministic per-location
    out = []
    for d in range(1, days + 1):
        # integer-only deterministic drift: a small triangle wave around the base.
        drift = ((seed + d) % 5) - 2
        out.append({
            "day": d,
            "location": nfc(location),
            "temp_c": int(base + drift),
            "instruction_eligible": False,
        })
    return out
