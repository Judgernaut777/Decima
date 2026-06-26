"""VULN1 — vulnerability management + threat-intel (CAPABILITY_MAP Part C, blue-team).

The layer ABOVE raw detection: a `vulnerability` (a known weakness, named by CVE) is a
first-class Cell, edged to the **asset** Cells it affects (the same `asset` Cells RECON1
records), and tied into the **detection ↔ vuln ↔ incident** graph — a vuln links to a
DET1/RECON1 `finding` or a TRIAGE1 `incident` so an analyst can walk from a CVE to the
live evidence that it is being exercised.

Two laws govern this module:

  - **External threat-intel is UNTRUSTED data.** An advisory pulled from a vendor feed or
    a CVE database is captured as DATA, never as an instruction. `ingest_intel` routes it
    through DISP1's `dispose` (`trusted=False`) so the intake lands `instruction_eligible=
    False` — its imperative content (e.g. "run this remediation now") can never select its
    own disposition or elevate to a task/invoke/policy. Intel informs; it never commands.
  - **Ints, not floats.** Severity is an integer (a CVSS-style 0–10 rank). The
    prioritization score is `severity × exposure` where exposure is an integer count of
    distinct affected assets + linked findings/incidents — a deterministic integer rank,
    stable across runs (ties broken by cell id).

Composes PUBLIC APIs only — model / detection / triage / recon / disposition / weave.
No core edit; no edit to any other module.
"""
from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc
from decima import disposition

VULNERABILITY = "vulnerability"
INTEL = "threat_intel"
ASSET = "asset"
# the cell types a vuln may be linked to (the detection ↔ vuln ↔ incident graph)
FINDING = "finding"
INCIDENT = "incident"
LINKABLE = (FINDING, INCIDENT)

# edge relations
AFFECTS = "affects"            # vuln → asset
EVIDENCED_BY = "evidenced_by"  # vuln → finding / incident
INFORMS = "informs"            # threat-intel intake → vuln


def _author(k) -> str:
    """The principal that signs vuln-management output (a blue-team analyst,
    idempotent by name). Distinct from the SOC-analyst TRIAGE1 mints."""
    return k.keyring.mint("vuln-analyst", "analyst").id


def vuln_id(cve: str) -> str:
    return content_id({"vulnerability": nfc(cve)})


def record_vuln(k, cve, *, severity, affected_assets) -> str:
    """Record a `vulnerability` Cell named by CVE (int severity, a CVSS-style 0–10
    rank), edged `affects` to one asset Cell per affected target. Each affected target
    is materialized as an `asset` Cell (the SAME shape RECON1 records), so a vuln and a
    recon finding can reference the same asset. Idempotent: content-addressed by CVE.

    `severity` MUST be an int (the Law: severity/scores are ints, never floats)."""
    if isinstance(severity, bool) or not isinstance(severity, int):
        raise TypeError(f"severity must be an int (CVSS-style rank), got {severity!r}")
    author = _author(k)
    vid = vuln_id(cve)
    assets = []
    for target in affected_assets:
        aid = content_id({"asset": nfc(str(target))})
        assert_content(k.weft, author, aid, ASSET, {"target": nfc(str(target))})
        assets.append(aid)
    assert_content(k.weft, author, vid, VULNERABILITY, {
        "cve": nfc(cve), "severity": int(severity),
        "affected_assets": sorted(assets), "status": "open",
    })
    for aid in assets:
        assert_edge(k.weft, author, vid, AFFECTS, aid)
    return vid


def ingest_intel(k, source, item) -> dict:
    """Ingest a piece of external threat-intel as UNTRUSTED data. Routed through DISP1's
    `dispose` with `trusted=False`, so the intake Cell lands `instruction_eligible=False`
    and the imperative content of a hostile advisory can never select its own disposition
    (an injection-laced feed routes to remember-as-suspicious, never to invoke/policy).

    `item` is the raw advisory text. Returns DISP1's disposition dict, augmented with the
    `intel` Cell id — a `threat_intel` Cell that records the provenance (source + the
    untrusted intake it came from) so a vuln can be `informs`-edged from it later."""
    disp = disposition.dispose(k, f"threat-intel:{source}", str(item), trusted=False)
    author = _author(k)
    tid = content_id({"threat_intel": str(item), "source": source, "intake": disp["intake"]})
    assert_content(k.weft, author, tid, INTEL, {
        "source": nfc(str(source)), "item": nfc(str(item)),
        "intake": disp["intake"], "disposition": disp["disposition"],
        "action": disp["action"], "trusted": False,
        "instruction_eligible": False,   # external intel is DATA, never an instruction
    })
    assert_edge(k.weft, author, tid, "ingested_from", disp["intake"])
    disp["intel"] = tid
    return disp


def link(k, vuln, finding_or_incident) -> str:
    """Tie a vuln to a DET1/RECON1 `finding` or a TRIAGE1 `incident` — the
    detection ↔ vuln ↔ incident graph. Asserts an `evidenced_by` edge (vuln → evidence)
    with provenance on the Weft. Refuses to link to anything that is not a finding/incident
    (fail loud — a vuln's evidence must be live detection output)."""
    w = k.weave()
    ev = w.get(finding_or_incident)
    if ev is None or ev.type not in LINKABLE:
        raise ValueError(
            f"can only link a vuln to a {LINKABLE} cell, got "
            f"{ev.type if ev else None!r} ({finding_or_incident!r})")
    if w.get(vuln) is None:
        raise ValueError(f"unknown vulnerability {vuln!r}")
    assert_edge(k.weft, _author(k), vuln, EVIDENCED_BY, finding_or_incident)
    return ev.id


def _exposure(w, vid) -> int:
    """Integer exposure of a vuln: the count of DISTINCT affected assets PLUS distinct
    linked findings/incidents. Counts the edges that make a vuln 'live' — more assets
    and more corroborating evidence ⇒ higher exposure. A floor of 1 keeps a recorded-but-
    unlinked vuln from scoring zero (it still affects ≥1 asset by construction)."""
    affects = {e["dst"] for e in w.edges_from(vid, AFFECTS)}
    evidence = {e["dst"] for e in w.edges_from(vid, EVIDENCED_BY)}
    return max(1, len(affects) + len(evidence))


def prioritize(k) -> list:
    """Rank every OPEN vulnerability by an integer score = severity × exposure
    (exposure = distinct affected assets + linked findings/incidents). Deterministic:
    sorted by score DESC, ties broken by cell id ASC, so the order is stable across runs.

    Returns a list of dicts {vuln, cve, severity, exposure, score} — pure ints, no float."""
    w = k.weave()
    ranked = []
    for cell in w.of_type(VULNERABILITY):
        if cell.content.get("status") != "open":
            continue
        sev = int(cell.content["severity"])
        exposure = _exposure(w, cell.id)
        ranked.append({
            "vuln": cell.id, "cve": cell.content["cve"],
            "severity": sev, "exposure": exposure, "score": sev * exposure,
        })
    ranked.sort(key=lambda r: (-r["score"], r["vuln"]))
    return ranked


# -- read-side projections ----------------------------------------------------
def vulns(weave) -> list:
    return weave.of_type(VULNERABILITY)


def affected(weave, vuln_cell) -> list:
    return [e["dst"] for e in weave.edges_from(vuln_cell, AFFECTS)]


def evidence(weave, vuln_cell) -> list:
    return [e["dst"] for e in weave.edges_from(vuln_cell, EVIDENCED_BY)]
