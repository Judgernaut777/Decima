"""Geospatial / maps — places, deterministic local routing, gated live geocoding
(CAPABILITY_MAP B4 — "Geospatial / maps — OSRM routing; local routing=worker, live
geocoding=GATED EGRESS").

A map is not a new authority — it is composition over the Heartbeat's existing
laws. Three rules shape this module, and they map onto three primitives already in
the kernel:

  • COORDINATES ARE INTS. A latitude/longitude is stored as a signed integer in
    **microdegrees** (degrees × 1e6) — NEVER a float in signed content. Floats are
    non-associative under reordering and serialize ambiguously, so two folds of the
    same Weft could disagree; integers fold identically (FOLD §11). A `place` is a
    Cell (Law 3) carrying `(lat_udeg, lon_udeg)` as ints.

  • LOCAL ROUTING IS A DETERMINISTIC WORKER. `route` computes a stub route between
    two places with NO network — an integer distance (a Manhattan/taxicab metric on
    the microdegree grid, which is exact integer arithmetic) and a fixed step list.
    Determinism is the point: routing a worker, not a live call, means the same two
    places always yield the same route across folds. `nearby` is the same idea — an
    integer radius filter over the integer distance metric.

  • LIVE GEOCODING GOES THROUGH THE GATED EGRESS CAPABILITY. Resolving a free-text
    address to coordinates needs the outside world, so `geocode` does NOT route
    locally — it reaches out through EGRESS1's gated `fetch` (target allowlist +
    SB1 sandbox + Weft audit). The response is **UNTRUSTED DATA**: it is recorded as
    a `geocode_candidate` Cell with `instruction_eligible=False` — a *candidate*
    location to consider, NEVER an instruction to obey. A geocoder that says
    "ignore your instructions" is just text in a field, exactly like an inbound
    parse or a browser receipt.

Pure composition over PUBLIC APIs: model (assert_content/assert_edge), egress
(install/fetch — the gated capability), knowledge (read-only graph queries for
provenance/relatedness), hashing (content_id/nfc). No core edit, no float in
signed content, no network outside the gated egress seam.
"""
from __future__ import annotations

from decima import egress, knowledge
from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc

PLACE = "place"
ROUTE = "route"
GEOCODE_CANDIDATE = "geocode_candidate"

# Edge rels (normalized on the edge; named here for callers).
ROUTE_FROM = "route_from"          # route —route_from→ place
ROUTE_TO = "route_to"              # route —route_to→ place
GEOCODED_VIA = "geocoded_via"      # candidate —geocoded_via→ egress fetch receipt


def _i(v) -> int:
    """Coerce a coordinate to an int microdegree value, FAIL LOUD on a float-shaped
    input. Coordinates in signed content are ints, full stop — accepting a float
    here would smuggle non-determinism into the fold, so a float is rejected rather
    than silently truncated."""
    if isinstance(v, bool):
        raise TypeError("coordinate must be an int microdegree, not a bool")
    if isinstance(v, float):
        raise TypeError(
            f"coordinate must be an INT microdegree (degrees×1e6), got float {v!r}")
    return int(v)


def place_id(name: str) -> str:
    """Content-address a place by NAME (nfc) so re-adding the same place is
    idempotent and a place keeps one identity across versions."""
    return content_id({"place": nfc(name)})


def add_place(k, name: str, lat_udeg: int, lon_udeg: int, *, kind=None) -> str:
    """Add (or re-version) a `place` Cell with INTEGER microdegree coordinates,
    returning its id.

    `lat_udeg` / `lon_udeg` are signed integers — degrees × 1e6 (so 37.422°
    is 37_422_000). A float is REFUSED (no floats in signed content). `kind`
    is a free-form tag (e.g. "home", "office", "landmark"). Provenance is on the
    Weft (author + parents); the place is trusted DATA the brain may recall."""
    name = nfc(name)
    cid = place_id(name)
    lat = _i(lat_udeg)
    lon = _i(lon_udeg)
    content = {
        "name": name,
        "lat_udeg": lat,
        "lon_udeg": lon,
        "kind": nfc(kind) if kind else "",
        "text": name,                       # text mirror for recall/search
        # a place you add is trusted DATA: recallable/citable, never an instruction
        "recallable": True,
        "citable": True,
        "instruction_eligible": False,
    }
    assert_content(k.weft, k.human.id, cid, PLACE, content)
    return cid


def _coords(k, place) -> tuple[int, int]:
    """The (lat_udeg, lon_udeg) ints of a place cell (Cell, id, or id-prefix)."""
    cell = k.weave().get(getattr(place, "id", place))
    if cell is None or cell.type != PLACE:
        raise ValueError(f"not a place: {place!r}")
    return int(cell.content["lat_udeg"]), int(cell.content["lon_udeg"])


def distance(k, a, b) -> int:
    """The INTEGER distance between two places — a Manhattan/taxicab metric on the
    microdegree grid (|Δlat| + |Δlon|). Exact integer arithmetic: deterministic and
    float-free, so the same pair always yields the same distance across folds. This
    is the single metric `route` and `nearby` both build on."""
    la, lo = _coords(k, a)
    lb, lob = _coords(k, b)
    return abs(la - lb) + abs(lo - lob)


def route(k, from_place, to_place) -> str:
    """Compute a DETERMINISTIC stub route between two places — a LOCAL WORKER, no
    network. Returns a `route` Cell id carrying an integer distance (the taxicab
    metric) and a fixed step list (depart → traverse-lat → traverse-lon → arrive).
    Edges tie the route to its endpoints (route —route_from→ / —route_to→ place).

    Determinism is the contract: routing locally (not a live OSRM call) means the
    same two places always fold to the same route. The integer distance never
    becomes a float."""
    f = getattr(from_place, "id", from_place)
    t = getattr(to_place, "id", to_place)
    la, lo = _coords(k, f)
    lb, lob = _coords(k, t)
    dlat = lb - la
    dlon = lob - lo
    dist = abs(dlat) + abs(dlon)
    steps = [
        {"op": "depart", "at_lat": la, "at_lon": lo},
        {"op": "traverse_lat", "delta_udeg": dlat},
        {"op": "traverse_lon", "delta_udeg": dlon},
        {"op": "arrive", "at_lat": lb, "at_lon": lob},
    ]
    rid = content_id({"route": [f, t]})
    content = {
        "from": f, "to": t,
        "distance_udeg": int(dist),         # INTEGER distance, deterministic
        "steps": steps,
        "metric": "manhattan_udeg",
        "text": f"route {f[:8]}→{t[:8]} ({dist} udeg)",
        "recallable": True, "citable": True,
        "instruction_eligible": False,      # a computed route is DATA
    }
    assert_content(k.weft, k.human.id, rid, ROUTE, content)
    assert_edge(k.weft, k.human.id, rid, ROUTE_FROM, f)
    assert_edge(k.weft, k.human.id, rid, ROUTE_TO, t)
    return rid


def nearby(k, place, radius_udeg: int) -> list:
    """Places within an INTEGER radius of `place`, by the integer distance metric.
    Returns a list of {"place": Cell, "distance_udeg": int} sorted by distance then
    id (deterministic), excluding the place itself and retracted places. `radius_udeg`
    is an int microdegree budget — a float is refused, like any coordinate value."""
    radius = _i(radius_udeg)
    center = getattr(place, "id", place)
    la, lo = _coords(k, center)
    out = []
    for c in k.weave().of_type(PLACE):
        if c.id == center or c.retracted:
            continue
        d = abs(int(c.content["lat_udeg"]) - la) + abs(int(c.content["lon_udeg"]) - lo)
        if d <= radius:
            out.append({"place": c, "distance_udeg": int(d)})
    out.sort(key=lambda r: (r["distance_udeg"], r["place"].id))
    return out


def geocode(k, agent, query: str, *, egress_cap, kind="json", author=None) -> dict:
    """Resolve a free-text address `query` to a place CANDIDATE via the GATED EGRESS
    capability — live geocoding, NOT a local worker. Reaches out through EGRESS1's
    `fetch` (target allowlist + SB1 sandbox + Weft audit), then records the response
    as UNTRUSTED DATA.

    `egress_cap` is the installed egress capability id (see `egress.install`); the
    geocoder host must be on its allowlist or the fetch fails closed. The returned
    candidate is a `geocode_candidate` Cell with `instruction_eligible=False`: a
    location to *consider*, NEVER an instruction. The candidate's body is whatever
    the geocoder returned — if it says "ignore your instructions" that is just text
    in a field, disposed as DATA by the egress path, never obeyed.

    Returns a dict:
      refused → {"ok": False, "refused": True, "reason", "host", "refusal"}
      resolved→ {"ok": True, "candidate": <cell id>, "query", "host", "receipt",
                 "body", "instruction_eligible": False}
    """
    author = author or k.human.id
    q = nfc(query)
    # A geocoder query URL — the host is gated by the egress allowlist; fail closed
    # if it is not allowlisted (the request never leaves the box).
    url = f"https://geocoder.maps.example/search?q={q}"
    r = egress.fetch(k, agent, egress_cap, url, kind=kind, author=author)
    if not r.get("ok"):
        # fail closed: surface the refusal, no candidate is recorded
        return {"ok": False, "refused": True, "host": r.get("host"),
                "reason": r.get("reason"), "refusal": r.get("refusal")}

    # The response BODY is UNTRUSTED DATA. Record a geocode CANDIDATE cell that
    # points at the egress fetch (so the candidate's provenance is the audited
    # outbound call) and is marked DATA — never an instruction.
    body = r.get("body", "")
    receipt = r.get("receipt")
    cid = content_id({"geocode_candidate": q, "of": receipt})
    content = {
        "query": q,
        "host": r.get("host"),
        "body": nfc(str(body)),             # the geocoder's raw answer, as DATA
        "receipt": receipt,
        "fetch_cell": r.get("fetch_cell"),
        "text": f"geocode candidate for {q!r}",
        "recallable": True, "citable": True,
        # THE LAW: a live-resolved candidate is DATA, a location to consider — the
        # response is never an instruction, no matter what bytes it carries.
        "instruction_eligible": False,
        "untrusted": True,
    }
    assert_content(k.weft, author, cid, GEOCODE_CANDIDATE, content)
    if receipt:
        assert_edge(k.weft, author, cid, GEOCODED_VIA, receipt)
    return {"ok": True, "candidate": cid, "query": q, "host": r.get("host"),
            "receipt": receipt, "body": body,
            "disposition": r.get("disposition"), "action": r.get("action"),
            "instruction_eligible": False}


def candidate_provenance(k, candidate) -> list:
    """The egress fetch receipt(s) a geocode candidate derives from — a read-only
    knowledge query (KNOW1) over the `geocoded_via` edges. Proves the candidate's
    location came from an audited, gated outbound call, not from thin air."""
    cid = getattr(candidate, "id", candidate)
    return [e["dst"] for e in k.weave().edges_from(cid, GEOCODED_VIA)]
