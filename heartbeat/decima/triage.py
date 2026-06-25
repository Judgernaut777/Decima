"""Blue-team triage / SIEM over the signed Weft (TRIAGE1, CAPABILITY_MAP Part C).

DET1 emits `finding` Cells; the Weft already IS a tamper-evident, append-only,
signed event store — i.e. a SIEM. TRIAGE1 is the layer above raw detection: it
**correlates** findings into **`incident`** Cells (grouped by rule / source within a
time window), scores severity (with a volume bump — many findings are worse than
one), links each incident to its findings (`includes` edges, full provenance), and
**proposes a response** — a remediation task, or a Morta-gated action proposal for
anything high/critical. A lone benign/low finding does NOT escalate.

Everything lands on the Weft, signed by a SOC-analyst principal, so the triage
itself is auditable and time-travelable. Reads DET1 findings through the public
`weave` API; does not edit `detection.py` or any core file.
"""
from decima.model import assert_content, assert_edge
from decima.hashing import content_id

INCIDENT = "incident"
RESPONSE = "incident_response"
FINDING = "finding"

# Severity lattice. DET1 severities are free strings; map them to a rank so an
# incident's severity is the worst of its findings (with a volume bump).
SEV_RANK = {"info": 1, "low": 1, "medium": 2, "moderate": 2, "high": 3, "critical": 4}
RANK_LABEL = {1: "low", 2: "medium", 3: "high", 4: "critical"}
ESCALATE_FLOOR = 4       # a LONE finding escalates only if critical; otherwise it takes ≥2 correlated
RESPONSE_GATE = 3        # an incident at/above this severity gets a Morta-gated action proposal


def _rank(severity: str) -> int:
    return SEV_RANK.get(str(severity).lower(), 2)


def _author(k) -> str:
    """The SOC-analyst principal that signs triage output (idempotent by name)."""
    return k.keyring.mint("soc-analyst", "analyst").id


def _seq_index(weft) -> dict:
    """event id -> seq, so a finding can be placed in time for windowing."""
    return {ev.id: ev.seq for ev in weft.events()}


def _finding_seq(cell, seq_of) -> int:
    for eid in reversed(cell.provenance):
        if eid in seq_of:
            return seq_of[eid]
    return 0


def _group(findings, group_by: str, window):
    """Cluster findings by `group_by` (rule|source), splitting a key's findings into
    separate clusters when a time gap exceeds `window` (None = no time split). The
    window is what stops two unrelated bursts hours apart from merging into one
    incident."""
    keyed: dict = {}
    for f in findings:
        k = f["rule"] if group_by == "rule" else f["source"]
        keyed.setdefault(k, []).append(f)
    clusters = []
    for key, fs in keyed.items():
        fs.sort(key=lambda f: f["seq"])
        cur = [fs[0]]
        for prev, nxt in zip(fs, fs[1:]):
            if window is not None and (nxt["seq"] - prev["seq"]) > window:
                clusters.append((key, cur))
                cur = [nxt]
            else:
                cur.append(nxt)
        clusters.append((key, cur))
    return clusters


def _escalates(members) -> bool:
    """An incident forms from ≥2 correlated findings, or a single finding whose
    severity is at/above the escalation floor. A lone low/medium finding does not."""
    return len(members) >= 2 or any(_rank(m["severity"]) >= ESCALATE_FLOOR for m in members)


def _severity(members) -> tuple[int, str]:
    score = max(_rank(m["severity"]) for m in members)
    if len(members) >= 3:                       # volume bump: a campaign is worse than a hit
        score = min(4, score + 1)
    return score, RANK_LABEL[score]


def _propose_response(k, author, incident_id, score, sources) -> str:
    """Propose a response for an incident. High/critical → a Morta-gated action
    proposal (it must clear approval before it acts); otherwise a remediation task.
    Recorded as a Cell with a `proposes` edge from the incident."""
    if score >= RESPONSE_GATE:
        kind, requires_approval = "action_proposal", True
        action = f"isolate {len(sources)} source(s) and open remediation (approval required)"
    else:
        kind, requires_approval = "task", False
        action = "open a remediation task"
    rid = content_id({"response": incident_id})
    assert_content(k.weft, author, rid, RESPONSE, {
        "incident": incident_id, "kind": kind, "action": action,
        "requires_approval": requires_approval, "status": "proposed",
    })
    assert_edge(k.weft, author, incident_id, "proposes", rid)
    return rid


def correlate(k, *, group_by: str = "rule", window=None, min_count: int = 2) -> list:
    """Correlate the Weave's `finding` Cells into `incident` Cells. Returns the
    incident ids. Idempotent: an incident's id is content-addressed by its members,
    so re-running with the same findings re-asserts the same incident."""
    w = k.weave()
    author = _author(k)
    seq_of = _seq_index(k.weft)
    findings = [{"id": c.id, "rule": c.content.get("rule"),
                 "detection": c.content.get("detection"),
                 "severity": c.content.get("severity"), "source": c.content.get("source"),
                 "seq": _finding_seq(c, seq_of)}
                for c in w.of_type(FINDING)]

    incidents = []
    for key, members in _group(findings, group_by, window):
        if len(members) < min_count and not _escalates(members):
            continue                            # a lone benign finding does not escalate
        if not _escalates(members):
            continue
        score, label = _severity(members)
        member_ids = sorted(m["id"] for m in members)
        sources = sorted({m["source"] for m in members})
        rules = sorted({m["detection"] for m in members})
        inc_id = content_id({"incident": key, "findings": member_ids})
        assert_content(k.weft, author, inc_id, INCIDENT, {
            "group_by": group_by, "key": key, "severity": label, "score": score,
            "finding_count": len(members), "findings": member_ids,
            "sources": sources, "rules": rules,
            "window": [min(m["seq"] for m in members), max(m["seq"] for m in members)],
            "status": "open",
        })
        for fid in member_ids:                  # provenance: incident → its findings
            assert_edge(k.weft, author, inc_id, "includes", fid)
        _propose_response(k, author, inc_id, score, sources)
        incidents.append(inc_id)
    return incidents


# -- read-side projections ----------------------------------------------------
def incidents(weave) -> list:
    return weave.of_type(INCIDENT)


def includes(weave, incident_id) -> list:
    return [e["dst"] for e in weave.edges_from(incident_id, "includes")]


def response_of(weave, incident_id):
    edges = weave.edges_from(incident_id, "proposes")
    return weave.get(edges[0]["dst"]) if edges else None
