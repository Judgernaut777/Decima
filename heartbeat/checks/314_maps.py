"""Real geocoding engine — WRAP the maps provider, offline contract (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for externals whose
answer is grounded in the physical world — recreating a planet-scale gazetteer is the
liability. MAPS1 keeps places / local routing as deterministic workers; `maps_engine.py`
asks a REAL Google Maps / Mapbox-style HTTPS provider to resolve an address, over stdlib
`urllib` (zero deps). This check drives it entirely OFFLINE via an injected fake transport
(the real `urllib` transport is never called), so the oracle stays deterministic and
network-free while proving the full contract:

  - success: an injected 200 with lat/lng → a `geocode` cell carrying the provider's
    lat_udeg / lng_udeg / provider_ref; the coordinates are ints (INT microdegrees) with
    the right degrees×1e6 conversion, and NO float appears anywhere on the cell;
  - fail closed: a provider zero-results / 4xx → {"denied": ...} and NO `geocode` cell;
  - HTTPS-only: a non-`https://` endpoint is refused BEFORE any request (the fake
    transport is never called) — the API key never rides a cleartext wire;
  - dispense-don't-disclose: the raw API key never appears in any event payload on the
    Weft — CRED1 applies it inside the broker.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import maps_engine, secrets

API_KEY = "gk_live_GOOGLEMAPS_SUPER_SECRET_KEY"
ENDPOINT = "https://maps.googleapis.com/maps/api/geocode/json"

# The provider returns lat/lng as FLOAT degrees; we assert the µ° conversion.
LAT_DEG = 37.4224764
LNG_DEG = -122.0842499
LAT_UDEG = round(LAT_DEG * 1_000_000)     # 37_422_476
LNG_UDEG = round(LNG_DEG * 1_000_000)     # -122_084_250


def _transport(calls, response):
    """A fake geocoding transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def _has_float(v) -> bool:
    """True if any float lurks anywhere in a (possibly nested) content value."""
    if isinstance(v, bool):
        return False
    if isinstance(v, float):
        return True
    if isinstance(v, dict):
        return any(_has_float(x) for x in v.values())
    if isinstance(v, (list, tuple)):
        return any(_has_float(x) for x in v)
    return False


def _decima(kk):
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== REAL GEOCODING ENGINE (wrapped provider, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("googlemaps", API_KEY, service="googlemaps")
    handle = broker.issue("googlemaps", _decima(kk), "geocode an address")

    query = "1600 Amphitheatre Parkway, Mountain View, CA"

    # 1. SUCCESS — provider resolves the address; we record it (int µ°) on the Weft. ──────
    calls = []
    ok_resp = (200, {"status": "OK", "results": [{
        "formatted_address": "1600 Amphitheatre Pkwy, Mountain View, CA 94043, USA",
        "place_id": "ChIJ2eUgeAK6j4ARbn5u_wAGqWA",
        "geometry": {"location": {"lat": LAT_DEG, "lng": LNG_DEG},
                     "location_type": "ROOFTOP"}}]})
    res = maps_engine.locate(kk, endpoint=ENDPOINT, query=query, credential_handle=handle,
                             broker=broker, agent_cell=_decima(kk),
                             transport=_transport(calls, ok_resp))
    assert "geocode" in res, res
    assert res["lat_udeg"] == LAT_UDEG and res["lng_udeg"] == LNG_UDEG, res
    assert res["provider_ref"] == "ChIJ2eUgeAK6j4ARbn5u_wAGqWA", res
    assert len(calls) == 1 and calls[0]["url"].startswith(ENDPOINT), calls
    cell = kk.weave().get(res["geocode"]).content
    assert cell["lat_udeg"] == LAT_UDEG and cell["lng_udeg"] == LNG_UDEG, cell
    assert cell["provider_ref"] == "ChIJ2eUgeAK6j4ARbn5u_wAGqWA", cell
    for fld in ("lat_udeg", "lng_udeg"):                     # ints only in signed content
        assert isinstance(cell[fld], int) and not isinstance(cell[fld], bool), (fld, cell[fld])
    assert not _has_float(cell), ("no float may appear in the recorded cell", cell)
    line("  success: injected 200 → geocode cell with the provider's lat_udeg / lng_udeg / "
         "provider_ref; coordinates are ints (INT microdegrees, degrees×1e6); no float on the cell ✓")

    # 2. HTTPS-only — a non-HTTPS endpoint is refused before any request. ─────────────────
    http_calls = []
    bad = maps_engine.locate(kk, endpoint="http://maps.googleapis.com/maps/api/geocode/json",
                             query=query, credential_handle=handle, broker=broker,
                             agent_cell=_decima(kk), transport=_transport(http_calls, ok_resp))
    assert "denied" in bad and "HTTPS" in bad["denied"], bad
    assert http_calls == [], "a non-HTTPS endpoint must be refused before any request"
    line("  HTTPS-only: a non-HTTPS endpoint is refused before the key is sent "
         "(transport never called) ✓")

    # 3. FAIL CLOSED — zero results / 4xx → denied, NO geocode cell recorded. ─────────────
    n_before = len(maps_engine.locations(kk))
    zero_calls = []
    empty = maps_engine.locate(kk, endpoint=ENDPOINT, query="nowhere at all",
                               credential_handle=handle, broker=broker, agent_cell=_decima(kk),
                               transport=_transport(zero_calls, (200, {"status": "ZERO_RESULTS",
                                                                       "results": []})))
    assert "denied" in empty and "maps_engine" in empty["denied"], empty
    assert len(zero_calls) == 1, "the request was made, but zero-results must fail closed"

    err_calls = []
    declined = maps_engine.locate(kk, endpoint=ENDPOINT, query=query, credential_handle=handle,
                                  broker=broker, agent_cell=_decima(kk),
                                  transport=_transport(err_calls, (400, {"error": "bad request"})))
    assert "denied" in declined and "maps_engine" in declined["denied"], declined
    assert len(err_calls) == 1, "the request was made, but the 4xx must fail closed"
    assert len(maps_engine.locations(kk)) == n_before, "no geocode cell on zero-results / error"
    line("  fail closed: provider zero-results / 4xx → {denied} and NO geocode cell recorded ✓")

    # 4. DISPENSE-DON'T-DISCLOSE — the raw API key never on the Weft. ─────────────────────
    payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert API_KEY not in payloads, "the raw maps API key must never be written to the Weft"
    line("  no raw API key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → geocoding is wrapped, not reinvented: a real provider (over stdlib urllib, zero "
         "deps) resolves the address; Decima records INT microdegrees on the Weft, holds the "
         "key in CRED1, refuses cleartext, and fails closed.")
