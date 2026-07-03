"""Real weather engine — WRAP the provider, never fabricate the forecast (data policy).

Decima's policy: recreate the design in pure stdlib, but for EXTERNAL FACTS about the
world — the current weather — ask a REAL provider rather than invent the number. WEATHER1
(`weather.py`) stays a capture-and-stub lane: it ingests a caller-supplied observation as
UNTRUSTED data and serves a deterministic stub forecast. This module COMPLEMENTS it by
asking a REAL weather provider (an OpenWeather / Tomorrow.io-style HTTPS API) for the
actual current reading of a location. The provider is just an HTTPS API, so the real
engine rides stdlib `urllib` with ZERO pip dependencies: real engine, still pure-stdlib.

GUARDRAILS (mirroring the tax engine / OIDC engine):
  - **HTTPS-only** — `fetch` refuses to put the API key on a non-`https://` endpoint
    BEFORE any request is made (never leak the key in cleartext, even in a query string).
  - **key via CRED1** — the provider API key lives in the secrets broker; `reading` calls
    `broker.use_secret`, which applies the key INSIDE the broker (never returned, never
    logged, never on the Weft). The raw key never appears in a `weather_reading` cell.
  - **numeric fields as INTS — no floats** — the provider speaks Celsius / m·s⁻¹ floats,
    but the Weft forbids floats (WEFT §4/§7). Temperature is stored as INT tenths of a
    degree C (`temp_dc`), humidity/precip as INT percent, wind as INT km/h; no float ever
    enters a value that lands on the Weft.
  - **fail closed** — a provider 4xx / declared error, an unreachable endpoint, or a
    denied credential records NO `weather_reading` cell and returns `{"denied": reason}`.
  - **transport seam** — `fetch` takes a `transport(url, headers) -> (status, json)`; the
    default is a real `urllib` GET; tests inject a fake, so the offline oracle exercises
    the full contract with NO network.

Composes public secrets / model / kernel APIs only. No core edit; does not touch
weather.py.
"""
import json
from urllib.parse import urlencode

from decima.model import assert_content
from decima.hashing import content_id, nfc

WEATHER_READING = "weather_reading"   # the on-Weft record of a provider reading (no key)
UDEG = 1_000_000                      # micro-degrees per degree (lat/lng ints → query)


class WeatherEngineError(Exception):
    """A weather-engine failure — no `weather_reading` may be recorded (fail closed).
    Covers a non-HTTPS endpoint, an unreachable/timed-out endpoint, and a provider
    4xx/error or an unparseable/float-hostile response."""


def _urllib_transport(url: str, headers: dict):
    """(Phase 2 · GO LIVE) FAIL-CLOSED default — the bare stdlib socket default is
    GONE: the armed wire guard (decima/wire.py) refuses ungated egress anyway, so
    `transport=None` on the live path now refuses HERE, first, with the sanctioned
    path named. Build the wire-gated transport via
    `live_wire.gated_get_transport(k, agent_cell, cap_id)`
    (a granted, Morta-approved egress capability) and inject it as `transport=`.
    Injected fake transports (the offline oracle, every test-mode path) never
    resolve to this default and are unaffected."""
    from decima import live_wire
    raise live_wire.NoGatedTransport(
        "weather_engine", hint='live_wire.gated_get_transport(k, agent_cell, cap_id)')


def _require_int(name: str, v):
    """Guard that a value the engine will sign is an int (never a float/bool)."""
    if not isinstance(v, int) or isinstance(v, bool):
        raise WeatherEngineError(f"{name} must be an int (tenths / percent / kph), got {v!r}")
    return int(v)


def _num(name: str, v):
    """A provider number (may be a float) that we are about to CONVERT to an int. Bools
    and non-numbers are rejected (a payload string is not a temperature)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise WeatherEngineError(f"{name} was not a number in the provider response: {v!r}")
    return v


def _dig(resp, *path, default=None):
    """Safely dig a nested value out of an OpenWeather-style response (dict/list path)."""
    cur = resp
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and isinstance(p, int) and 0 <= p < len(cur):
            cur = cur[p]
        else:
            return default
    return default if cur is None else cur


def _norm_location(location):
    """Normalise an UNTRUSTED location into a dict. Accepts a place string or a dict
    carrying lat_udeg/lng_udeg ints (and/or a place)."""
    if isinstance(location, str):
        return {"place": location}
    if isinstance(location, dict):
        return dict(location)
    raise WeatherEngineError("location must be a place string or a {lat_udeg,lng_udeg} dict")


def _with_endpoint(location, endpoint: str) -> dict:
    loc = _norm_location(location)
    loc["endpoint"] = endpoint
    return loc


def location_label(location) -> str:
    """A stable DATA label for the location to record on the cell (never trusted)."""
    loc = _norm_location(location)
    place = loc.get("place")
    if place:
        return nfc(str(place))
    if "lat_udeg" in loc and "lng_udeg" in loc:
        return f"{_require_int('lat_udeg', loc['lat_udeg'])},{_require_int('lng_udeg', loc['lng_udeg'])}"
    raise WeatherEngineError("location needs a place or lat_udeg/lng_udeg")


def _udeg_deg(name: str, udeg) -> str:
    """Render a micro-degree int as a decimal-degree query string (URL only, never
    signed content — a float here never reaches the Weft)."""
    return f"{_require_int(name, udeg) / UDEG:.6f}"


def fetch(secret_key: str, location, *, transport=None) -> dict:
    """Fetch the current reading for a location from the REAL provider.

    `location` is a dict carrying `endpoint` (the provider's HTTPS weather URL) plus the
    place — either lat_udeg/lng_udeg (ints) OR a `place` string (UNTRUSTED). GETs the
    reading over stdlib `urllib` and returns the provider's answer converted to INTS:
    {temp_dc:int (tenths of °C), humidity_pct:int, wind_kph:int, precip_pct:int,
    condition, provider_ref, observed_at:int}. No float is ever returned in a field the
    caller will sign.

    HTTPS-only: a non-`https://` endpoint is refused BEFORE the key touches the wire
    (the key rides the query string, so cleartext is never acceptable). Raises
    `WeatherEngineError` on a non-HTTPS endpoint, an unreachable endpoint, or a definite
    provider error (4xx / error body) — the caller (`reading`) fails closed."""
    transport = transport or _urllib_transport

    loc = _norm_location(location)
    endpoint = str(loc.get("endpoint", ""))
    if not endpoint.startswith("https://"):
        # Never put the API key on the wire in cleartext (it rides the query string).
        raise WeatherEngineError("refusing to send the API key to a non-HTTPS weather endpoint")

    params: dict = {}
    place = loc.get("place")
    if place:
        params["q"] = str(place)                             # untrusted place string
    elif "lat_udeg" in loc and "lng_udeg" in loc:
        params["lat"] = _udeg_deg("lat_udeg", loc["lat_udeg"])
        params["lon"] = _udeg_deg("lng_udeg", loc["lng_udeg"])
    else:
        raise WeatherEngineError("location needs a place or lat_udeg/lng_udeg")
    params["units"] = "metric"
    params["appid"] = secret_key                             # applied here, never returned

    url = endpoint + ("&" if "?" in endpoint else "?") + urlencode(params)
    headers = {"Accept": "application/json"}
    try:
        status, resp = transport(url, headers)
    except Exception as e:                                    # network/timeout — unreachable
        raise WeatherEngineError(f"weather endpoint unreachable: {e}")

    if not isinstance(resp, dict):
        raise WeatherEngineError(f"unparseable weather response (status {status})")

    temp_c = _dig(resp, "main", "temp")
    if temp_c is None:
        temp_c = resp.get("temp")
    if status == 200 and temp_c is not None:
        # The provider speaks floats; we cross the boundary into ints here (WEFT §4/§7):
        # tenths of a degree, whole percent, whole km/h — never a float on the Weft.
        humidity = _dig(resp, "main", "humidity", default=resp.get("humidity", 0))
        wind_ms = _dig(resp, "wind", "speed", default=resp.get("wind_ms", 0))
        pop = resp.get("pop", _dig(resp, "clouds", "all", default=0))
        condition = (_dig(resp, "weather", 0, "main")
                     or _dig(resp, "weather", 0, "description")
                     or resp.get("condition"))
        return {
            "temp_dc": int(round(_num("temp", temp_c) * 10)),
            "humidity_pct": int(round(_num("humidity", humidity))),
            "wind_kph": int(round(_num("wind", wind_ms) * 3.6)),   # m·s⁻¹ → km·h⁻¹
            "precip_pct": int(round(_num("precip", pop) * (100 if pop is not None and pop <= 1 else 1))),
            "condition": condition,
            "provider_ref": resp.get("id") or resp.get("name") or _dig(resp, "sys", "id"),
            "observed_at": int(resp.get("dt") or resp.get("observed_at") or 0),
        }
    err = resp.get("message") or resp.get("error") or f"http {status}"
    raise WeatherEngineError(f"provider rejected the weather request: {err}")   # definite error


def reading(k, *, endpoint: str, location, credential_handle: str, broker,
            agent_cell, transport=None) -> dict:
    """Get a REAL provider weather reading and record it on the Weft (fail closed).

    Resolves the provider API key via CRED1 (`broker.use_secret`, which applies the key
    INSIDE the broker and never discloses it), runs `fetch` for `location` against the
    HTTPS `endpoint`, and on success asserts a `weather_reading` cell carrying
    location/temp_dc/humidity_pct/wind_kph/precip_pct/condition/provider_ref/observed_at
    (temperature as INT tenths of °C, humidity/precip as INT percent, wind as INT km/h —
    NEVER the key; condition/location are DATA). Returns
    {weather_reading: <cell id>, temp_dc, condition, provider_ref}.

    On a denied credential (revoked/unauthorized/over-budget) or any engine error
    (non-HTTPS, unreachable, provider 4xx) it records NO cell and returns
    {"denied": reason}."""
    loc = _with_endpoint(location, endpoint)
    try:
        r = broker.use_secret(
            agent_cell, credential_handle,
            lambda key: fetch(key, loc, transport=transport))
    except WeatherEngineError as e:
        return {"denied": f"weather_engine: {e}"}            # fail closed — no reading cell
    if "denied" in r:
        return {"denied": r["denied"]}                       # credential handle denied
    result = r["ok"]

    content = {
        "location": location_label(location),
        "temp_dc": _require_int("temp_dc", result["temp_dc"]),
        "humidity_pct": _require_int("humidity_pct", result["humidity_pct"]),
        "wind_kph": _require_int("wind_kph", result["wind_kph"]),
        "precip_pct": _require_int("precip_pct", result["precip_pct"]),
        "condition": result.get("condition"),
        "provider_ref": result.get("provider_ref"),
        "observed_at": _require_int("observed_at", result["observed_at"]),
        "instruction_eligible": False,   # observed from outside → DATA, never an instruction
    }
    # Content-addressed by the reading body so the same observation keeps one identity.
    cid = content_id({"weather_reading": content})
    assert_content(k.weft, k.decima_agent_id, cid, WEATHER_READING, content)
    return {
        "weather_reading": cid,
        "temp_dc": content["temp_dc"],
        "condition": content["condition"],
        "provider_ref": content["provider_ref"],
    }


def readings(k) -> list:
    """All folded `weather_reading` cells on the Weft."""
    return list(k.weave().of_type(WEATHER_READING))
