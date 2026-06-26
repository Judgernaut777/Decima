"""MAPS1 — geospatial / maps (heartbeat/decima/maps.py).

Proves the maps laws (CAPABILITY_MAP B4 — local routing=worker, live geocoding=
GATED EGRESS):

  - PLACES are Cells with INTEGER microdegree coordinates (no floats in signed
    content — a float coordinate is refused);
  - ROUTE between two places is a deterministic LOCAL worker: an INTEGER distance
    + a fixed step list, identical across recomputation (no network);
  - NEARBY filters places by an INTEGER radius over the integer distance metric;
  - GEOCODE resolves an address via the GATED EGRESS capability — the response is
    UNTRUSTED DATA (a candidate, instruction_eligible=False, never obeyed), and a
    non-allowlisted geocoder host fails closed.

Runs on the shared kernel; composes PUBLIC maps/egress/knowledge/kernel APIs.
Contract: run(k, line). Fail loud.
"""
from decima import maps, egress


def run(k, line):
    line("\n== MAPS (places=int coords · route=local worker · geocode=gated egress DATA) ==")

    # ── (1) places are Cells with INTEGER microdegree coordinates ────────────
    home = maps.add_place(k, "Home", 37_422_000, -122_084_000, kind="home")
    office = maps.add_place(k, "Office", 37_484_000, -122_148_000, kind="office")
    cafe = maps.add_place(k, "Cafe", 37_426_000, -122_080_000, kind="landmark")
    far = maps.add_place(k, "FarCity", 40_712_000, -74_006_000, kind="city")
    hc = k.weave().get(home)
    assert hc.type == maps.PLACE, hc.type
    assert isinstance(hc.content["lat_udeg"], int) and not isinstance(hc.content["lat_udeg"], bool)
    assert isinstance(hc.content["lon_udeg"], int), hc.content
    assert hc.content["lat_udeg"] == 37_422_000, hc.content
    assert hc.content["instruction_eligible"] is False, "a place is DATA"
    line(f"  added 4 places · Home=({hc.content['lat_udeg']},{hc.content['lon_udeg']}) "
         f"int microdegrees · instruction_eligible=False ✓")

    # a FLOAT coordinate must be refused — no floats in signed content
    try:
        maps.add_place(k, "BadFloat", 37.422, -122.084)
        raise AssertionError("a float coordinate must be REFUSED")
    except TypeError as e:
        assert "float" in str(e).lower() or "int" in str(e).lower(), e
    line("  float coordinate (37.422) → REFUSED (no floats in signed content) ✓")

    # ── (2) route is a deterministic LOCAL worker: INTEGER distance, no network ─
    r1 = maps.route(k, home, office)
    r2 = maps.route(k, home, office)
    assert r1 == r2, "route must be deterministic (same cell id on recompute)"
    rc = k.weave().get(r1)
    expect = abs(37_484_000 - 37_422_000) + abs(-122_148_000 - -122_084_000)  # 62000+64000
    assert rc.content["distance_udeg"] == expect, (rc.content["distance_udeg"], expect)
    assert isinstance(rc.content["distance_udeg"], int), "distance is an INTEGER"
    assert rc.content["metric"] == "manhattan_udeg", rc.content
    assert len(rc.content["steps"]) == 4 and rc.content["steps"][0]["op"] == "depart"
    assert rc.content["steps"][-1]["op"] == "arrive", rc.content["steps"]
    # endpoints tied by edges on the Weft
    assert any(e["dst"] == office for e in k.weave().edges_from(r1, maps.ROUTE_TO)), "route_to edge"
    assert any(e["dst"] == home for e in k.weave().edges_from(r1, maps.ROUTE_FROM)), "route_from edge"
    line(f"  route Home→Office: distance={rc.content['distance_udeg']} udeg (int, deterministic) "
         f"· {len(rc.content['steps'])} steps · endpoints on Weft ✓")

    # ── (3) nearby filters by INTEGER radius over the integer metric ─────────
    near = maps.nearby(k, home, 50_000)        # Cafe is ~6000 udeg away; Office/FarCity far
    near_ids = [n["place"].id for n in near]
    assert cafe in near_ids, "Cafe must be within 50000 udeg of Home"
    assert office not in near_ids, "Office must be OUTSIDE 50000 udeg radius"
    assert far not in near_ids, "FarCity must be outside the radius"
    assert all(isinstance(n["distance_udeg"], int) for n in near), "distances are ints"
    # deterministic ascending order
    assert near == sorted(near, key=lambda n: (n["distance_udeg"], n["place"].id))
    # a float radius is refused too
    try:
        maps.nearby(k, home, 50_000.0)
        raise AssertionError("a float radius must be REFUSED")
    except TypeError:
        pass
    line(f"  nearby(Home, r=50000): {[n['place'].content['name'] for n in near]} "
         f"(Cafe in, Office/FarCity out) ✓")

    # ── (4) geocode via the GATED EGRESS capability — response is DATA ───────
    cap_id, hosts = egress.install(k, allowlist=["geocoder.maps.example"])
    agent = k.weave().get(k.decima_agent_id)   # re-read post-grant so envelope holds cap

    g = maps.geocode(k, agent, "1600 Amphitheatre Pkwy", egress_cap=cap_id)
    assert g["ok"] and g["host"] == "geocoder.maps.example", g
    assert g["instruction_eligible"] is False, "geocode response must be DATA"
    cand = k.weave().get(g["candidate"])
    assert cand.type == maps.GEOCODE_CANDIDATE, cand.type
    assert cand.content["instruction_eligible"] is False, "candidate is DATA, never an instruction"
    assert cand.content["untrusted"] is True, cand.content
    # provenance: the candidate derives from the audited egress fetch receipt
    prov = maps.candidate_provenance(k, g["candidate"])
    assert g["receipt"] in prov, "candidate must point at its egress fetch receipt"
    # the canned egress body carries an EMBEDDED imperative — it stays DATA, never obeyed
    assert "ignore your instructions" in g["body"].lower(), g["body"]
    line(f"  geocode '1600 Amphitheatre Pkwy' via gated egress → candidate "
         f"{g['candidate'][:10]} (DATA, instruction_eligible=False, never obeyed) ✓")

    # a non-allowlisted geocoder host fails closed (the request never leaves the box)
    cap2, _ = egress.install(k, allowlist=["other.maps.example"], name="egress.fetch.maps2")
    agent2 = k.weave().get(k.decima_agent_id)
    bad = maps.geocode(k, agent2, "somewhere", egress_cap=cap2)
    assert bad["ok"] is False and bad["refused"] is True, bad
    assert "candidate" not in bad, "a refused geocode records NO candidate"
    line(f"  geocode with non-allowlisted host → REFUSED (fail closed, no candidate) ✓")
