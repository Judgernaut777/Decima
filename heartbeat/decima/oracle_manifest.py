"""Oracle freeze — the conformance oracle's own check set, pinned (Batch D, lane 3).

`smoke.py` IS the conformance oracle: the thing a Rust port must pass (VISION.md →
"a pure-stdlib reference whose job is to be correct and to be the conformance oracle
the eventual Rust port must pass"). But it discovers what to run by GLOB —
`sorted(checks_dir.glob("[0-9]*.py"))` — so the oracle's strength is whatever happens
to be on disk at the moment it runs. Three silent-weakening paths follow from that,
and none of them makes the run go red:

  • a check file is DELETED or RENAMED → the suite runs fewer checks and still prints
    `heartbeat: alive. ✓`;
  • a check is GUTTED (file still present, `run()` body replaced with `pass`) → same;
  • a check is never registered anywhere, so the "228 checks" figure quoted in
    BACKLOG/PR/release text is an unverified claim about a directory listing.

While the port is being built that is the difference between "the port passes the
oracle" and "the port passes *a* oracle". Batch D's third lane closes it: this module
pins the oracle's shape as a committed manifest (`protocol/oracle_manifest.json`) and
`verify()` reports every way the live tree has drifted from it. Check 514 fails the
suite loud on any drift, so the oracle can only get weaker DELIBERATELY — in a diff, in
a commit, under review — never by accident or by a mutation that removes a witness.

This is the same discipline `decima/vectors.py` applies to the reference's observable
BYTES, applied to the reference's observable COVERAGE. Vectors freeze what the port
must reproduce; this freezes what gets to ask.

Drift kinds (all three fail the check — see the tradeoff note below):

  missing       in the manifest, absent from disk — coverage silently lost
  unregistered  on disk, absent from the manifest — a new check must be recorded, so
                the documented count stays a fact rather than a guess
  modified      content digest differs — catches the gutted-body case a set-only
                comparison cannot see

TRADEOFF, stated plainly: making `modified` fatal means any deliberate edit to a check
(or to `smoke.py`) must be followed by regenerating the manifest. That is friction, and
it is the intended friction of a freeze — the reference is entering port mode, where a
change to the oracle is exactly the kind of thing that must be visible in review rather
than absorbed silently. The remedy is one command and lands in the same commit as the
edit that motivated it:

    cd heartbeat && python3 -m decima.oracle_manifest

Digests use the reference's OWN content addressing (`hashing.blob_id` — BLAKE2b-128,
domain-separated, per PROFILE.md), so the oracle is addressed by the very primitive it
exists to prove. Pure stdlib; deterministic; no wall-clock, no randomness.
"""
from __future__ import annotations

import json
import pathlib

from decima import hashing

_HEARTBEAT = pathlib.Path(__file__).resolve().parents[1]

CHECKS_DIR = _HEARTBEAT / "checks"
SMOKE_PATH = _HEARTBEAT / "smoke.py"
MANIFEST_PATH = _HEARTBEAT / "protocol" / "oracle_manifest.json"

# The glob smoke.py itself uses to discover checks — kept identical on purpose: the
# manifest must pin exactly the set the oracle would run, not a near-miss of it.
CHECK_GLOB = "[0-9]*.py"


def file_digest(path: pathlib.Path) -> str:
    """Content-address a file's raw bytes with the reference's own blob addressing."""
    return hashing.blob_id(path.read_bytes())


def scan(checks_dir: pathlib.Path | None = None,
         smoke_path: pathlib.Path | None = None) -> dict:
    """The LIVE oracle shape, read from disk: the driver's digest plus every check the
    glob would pick up, keyed by filename. Sorted for a stable, diffable artifact."""
    checks_dir = checks_dir or CHECKS_DIR
    smoke_path = smoke_path or SMOKE_PATH
    checks = {p.name: file_digest(p) for p in sorted(checks_dir.glob(CHECK_GLOB))}
    return {
        "driver": {"smoke.py": file_digest(smoke_path)},
        "checks": checks,
        "check_count": len(checks),
    }


def build() -> dict:
    """The manifest to commit: the live shape plus the contract it asserts."""
    live = scan()
    return {
        "what": "The frozen shape of the heartbeat conformance oracle — the check set a "
                "Rust port must be measured against. Regenerate ONLY as a deliberate, "
                "reviewed act: cd heartbeat && python3 -m decima.oracle_manifest",
        "digest": "BLAKE2b-128, domain-separated (decima.hashing.blob_id) over raw file bytes",
        "glob": CHECK_GLOB,
        **live,
    }


def dumps(manifest: dict | None = None) -> str:
    return json.dumps(manifest if manifest is not None else build(),
                      indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def load() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def verify(manifest: dict | None = None, live: dict | None = None) -> list[dict]:
    """Every way `live` has drifted from `manifest`. Empty list == the oracle is frozen.

    Both arguments are injectable so the drift detector can be driven against SYNTHETIC
    tree states (check 514 proves it catches a deletion, a rename, and a gutted body
    without ever mutating the real checks directory).
    """
    manifest = manifest if manifest is not None else load()
    live = live if live is not None else scan()

    findings: list[dict] = []

    pinned_driver = manifest.get("driver", {})
    live_driver = live.get("driver", {})
    for name, want in sorted(pinned_driver.items()):
        got = live_driver.get(name)
        if got is None:
            findings.append({"kind": "missing", "file": name,
                             "detail": "the oracle driver itself is gone"})
        elif got != want:
            findings.append({"kind": "modified", "file": name,
                             "detail": f"driver digest {want} → {got}"})

    pinned = manifest.get("checks", {})
    current = live.get("checks", {})
    for name, want in sorted(pinned.items()):
        got = current.get(name)
        if got is None:
            findings.append({"kind": "missing", "file": name,
                             "detail": "pinned check is absent — coverage silently lost"})
        elif got != want:
            findings.append({"kind": "modified", "file": name,
                             "detail": f"digest {want} → {got} (a gutted or edited check)"})
    for name in sorted(set(current) - set(pinned)):
        findings.append({"kind": "unregistered", "file": name,
                         "detail": "new check is not in the manifest — register it so the "
                                   "documented check count stays a fact"})

    want_count = manifest.get("check_count")
    if want_count is not None and want_count != live.get("check_count"):
        findings.append({"kind": "count", "file": "(check set)",
                         "detail": f"pinned check_count {want_count} != "
                                   f"{live.get('check_count')} discovered"})
    return findings


def describe(findings: list[dict]) -> str:
    """A loud, actionable failure message — what drifted and the one way to resolve it."""
    lines = [f"  {f['kind']:<12} {f['file']} — {f['detail']}" for f in findings]
    return ("the conformance oracle has drifted from its frozen manifest:\n"
            + "\n".join(lines)
            + "\n  if this change is INTENDED, regenerate the manifest in the same commit:"
              "\n    cd heartbeat && python3 -m decima.oracle_manifest")


def main() -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    manifest = build()
    MANIFEST_PATH.write_text(dumps(manifest), encoding="utf-8")
    print(f"wrote {MANIFEST_PATH}")
    print(f"  pinned {manifest['check_count']} checks + the smoke.py driver")


if __name__ == "__main__":
    main()
