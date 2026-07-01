"""Real geocoding engine — WRAP the maps provider, never roll your own geocoder.

Decima's policy: recreate the design in pure stdlib, but for externals where the answer
is grounded in the physical world WRAP THE REAL ENGINE rather than reimplement it — a
geocoder is a planet-scale gazetteer of streets, buildings, and administrative regions
that changes constantly, so recreating it is the liability. MAPS1 (`maps.py`) keeps
places / deterministic local routing as workers and treats *live* geocoding as GATED
EGRESS returning an UNTRUSTED candidate; this module COMPLEMENTS it by asking a REAL
maps provider (a Google Maps / Mapbox-style HTTPS geocoding API) to resolve an address.
The provider is just an HTTPS API, so the real engine rides stdlib `urllib` with ZERO
pip dependencies: real engine, still pure-stdlib.

GUARDRAILS (mirroring the tax engine / OIDC engine):
  - **HTTPS-only** — `geocode` refuses to send the API key to a non-`https://` endpoint
    before any request is made (never leak the key in cleartext).
  - **key via CRED1** — the provider API key lives in the secrets broker; `locate` calls
    `broker.use_secret`, which applies the key INSIDE the broker (never returned, never
    logged, never on the Weft). The raw key never appears in a `geocode` cell or audit.
  - **fail closed** — a provider 4xx / zero-results, an unreachable endpoint, or a denied
    credential records NO `geocode` cell and returns `{"denied": reason}`.
  - **coordinates as INT microdegrees — no floats** — the provider returns lat/lng as
    floats, but the Weft forbids floats (non-associative under reordering, ambiguous
    serialization → two folds could disagree). Coordinates are stored as signed INT
    microdegrees (degrees × 1e6, `round(lat * 1_000_000)`); no float ever lands on a cell.
  - **untrusted data** — the resolved address is the outside world's answer, treated as
    DATA (the query text is untrusted too), never an instruction.
  - **transport seam** — `geocode` takes a `transport(url, headers, body) -> (status,
    json)`; the default is a real `urllib` GET; tests inject a fake, so the offline
    oracle exercises the full contract with NO network.

Composes public secrets / model / kernel APIs only. No core edit; does not touch maps.py.
"""
import json
from urllib.parse import urlencode

from decima.model import assert_content
from decima.hashing import content_id, nfc

GEOCODE = "geocode"           # the on-Weft record of a provider-resolved location (no key)
MICRODEGREES = 1_000_000      # degrees → microdegrees (37.422° == 37_422_000 µ°)


class MapsEngineError(Exception):
    """A geocoding failure — no `geocode` may be recorded (fail closed). Covers a
    non-HTTPS endpoint, an unreachable/timed-out endpoint, and a provider 4xx /
    zero-results answer."""


def _urllib_transport(url: str, headers: dict, body: str):
    """The real transport: a stdlib `urllib` GET (no pip dep). Returns
    (status_code, parsed_json). A 4xx/5xx surfaces as (code, error-json) rather than
    raising, so `geocode` decides success vs. definite error. A transport-level
    failure (DNS, timeout, TLS) raises — `geocode` maps that to `MapsEngineError`
    (unreachable). Never used by the offline oracle (tests inject a fake transport)."""
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:                       # 4xx/5xx carry a JSON body
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": f"http {e.code}"}


def _to_udeg(name: str, v) -> int:
    """Convert a provider coordinate (a float degree value) to a signed INT microdegree.
    A bool is refused (a bool is not a coordinate); anything not int/float is refused.
    `round(...)` returns an int — no float ever reaches a cell."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise MapsEngineError(f"{name} must be a numeric degree value, got {v!r}")
    return int(round(v * MICRODEGREES))


def _require_udeg(name: str, v) -> int:
    """Guard that an already-computed microdegree value is a plain int (never a
    float/bool) before it is signed onto a cell — no float in signed content."""
    if not isinstance(v, int) or isinstance(v, bool):
        raise MapsEngineError(f"{name} must be an int microdegree, got {v!r}")
    return int(v)


def geocode(secret_key: str, query: str, *, endpoint: str, transport=None) -> dict:
    """Resolve a free-text address `query` to coordinates by asking the REAL provider.

    GETs the address against the provider's HTTPS geocoding `endpoint` over stdlib
    `urllib` (the key applied in the Authorization header, never returned) and parses the
    top result. Returns
    {lat_udeg:int, lng_udeg:int, formatted, provider_ref (place id), match_quality} —
    coordinates as signed INT microdegrees (degrees × 1e6), NEVER floats.

    HTTPS-only: a non-`https://` endpoint is refused BEFORE the key touches the wire.
    Raises `MapsEngineError` on a non-HTTPS endpoint, an unreachable endpoint, or a
    definite provider error (4xx / zero results) — the caller (`locate`) fails closed."""
    transport = transport or _urllib_transport

    if not str(endpoint).startswith("https://"):
        # Never put the API key on the wire in cleartext. Refuse before sending.
        raise MapsEngineError("refusing to send the API key to a non-HTTPS geocoding endpoint")

    # The address is UNTRUSTED text — URL-encode it as an opaque query parameter.
    url = endpoint + ("&" if "?" in endpoint else "?") + urlencode({"address": str(query)})
    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Accept": "application/json",
    }
    try:
        status, resp = transport(url, headers, None)
    except Exception as e:                                    # network/timeout — unreachable
        raise MapsEngineError(f"geocoding endpoint unreachable: {e}")

    if not isinstance(resp, dict):
        raise MapsEngineError(f"unparseable geocoding response (status {status})")
    results = resp.get("results")
    if status == 200 and isinstance(results, list) and results:
        top = results[0]
        loc = (top.get("geometry") or {}).get("location") or {}
        lat = loc.get("lat", top.get("lat"))
        lng = loc.get("lng", top.get("lng"))
        if lat is None or lng is None:
            raise MapsEngineError("provider result carried no lat/lng")
        return {
            "lat_udeg": _to_udeg("lat", lat),
            "lng_udeg": _to_udeg("lng", lng),
            "formatted": top.get("formatted_address") or top.get("formatted"),
            "provider_ref": top.get("place_id") or top.get("id"),
            "match_quality": top.get("location_type") or resp.get("status"),
        }
    # zero results or an explicit error → a definite failure (fail closed).
    err = (resp.get("error_message") or resp.get("error")
           or resp.get("status") or f"http {status}")
    raise MapsEngineError(f"provider returned no usable location: {err}")


def locate(k, *, endpoint: str, query: str, credential_handle: str, broker,
           agent_cell, transport=None) -> dict:
    """Resolve an address with the REAL provider and record it on the Weft (fail closed).

    Resolves the provider API key via CRED1 (`broker.use_secret`, which applies the key
    INSIDE the broker and never discloses it), runs `geocode` on `query` against the HTTPS
    `endpoint`, and on success asserts a `geocode` cell carrying
    query/lat_udeg/lng_udeg/formatted/provider_ref (coordinates as INT microdegrees —
    NEVER floats, NEVER the key; the query is recorded as untrusted DATA). Returns
    {geocode: <cell id>, lat_udeg, lng_udeg, provider_ref}.

    On a denied credential (revoked/unauthorized/over-budget) or any engine error
    (non-HTTPS, unreachable, provider 4xx / zero-results) it records NO cell and returns
    {"denied": reason}."""
    try:
        r = broker.use_secret(
            agent_cell, credential_handle,
            lambda key: geocode(key, query, endpoint=endpoint, transport=transport))
    except MapsEngineError as e:
        return {"denied": f"maps_engine: {e}"}               # fail closed — no geocode cell
    if "denied" in r:
        return {"denied": r["denied"]}                       # credential handle denied
    result = r["ok"]

    content = {
        # the query is UNTRUSTED text — recorded as DATA, never an instruction.
        "query": nfc(str(query)),
        "lat_udeg": _require_udeg("lat_udeg", result["lat_udeg"]),   # already ints; re-guarded
        "lng_udeg": _require_udeg("lng_udeg", result["lng_udeg"]),
        "formatted": result.get("formatted"),
        "provider_ref": result.get("provider_ref"),
        "match_quality": result.get("match_quality"),
        "instruction_eligible": False,
        "untrusted": True,
    }
    # Content-addressed by the geocode body (re-resolving identical inputs is idempotent
    # and a location keeps one identity on the Log).
    cid = content_id({"geocode": content})
    assert_content(k.weft, k.decima_agent_id, cid, GEOCODE, content)
    return {
        "geocode": cid,
        "lat_udeg": content["lat_udeg"],
        "lng_udeg": content["lng_udeg"],
        "provider_ref": content["provider_ref"],
    }


def locations(k) -> list:
    """All folded `geocode` cells on the Weft."""
    return list(k.weave().of_type(GEOCODE))
