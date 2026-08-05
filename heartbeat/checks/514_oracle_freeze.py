"""ORACLE FREEZE — the conformance oracle's own check set is pinned (Batch D, lane 3).

Batch D's first two lanes froze what a Rust port must REPRODUCE (`vectors.py`, check
510) and hardened the seam a port will FOSSILIZE (`isolation.py`, check 512). This is
the third: freezing the oracle that does the measuring.

`smoke.py` discovers its checks by GLOB, so before this lane the oracle's strength was
whatever sat on disk when it ran. Delete a check, rename it, or gut its `run()` body,
and the suite quietly runs less and still prints `heartbeat: alive. ✓`. During a port
that is the gap between "the port passes the oracle" and "the port passes *a* oracle" —
and it is also the perfect hiding place for a regression, since the witness that would
have caught it is the very thing removed.

`decima/oracle_manifest.py` pins the oracle's shape (every check + the `smoke.py`
driver, content-addressed with the reference's own `hashing.blob_id`) into the
committed `protocol/oracle_manifest.json`. This check enforces it, and proves the
enforcement is real rather than nominal:

  (1) THE REAL TREE IS FROZEN — `verify()` against the live checks directory returns
      ZERO findings. Any deletion, rename, gutting, or unregistered addition fails the
      suite HERE, loud, naming the file and the one deliberate remedy.

  (2) THE DETECTOR ACTUALLY DETECTS — driven against SYNTHETIC tree states (never
      touching the real directory), each silent-weakening path is caught and correctly
      classified: a DELETED check → `missing`; a RENAMED check → `missing` +
      `unregistered` (the rename is not laundered into a no-op); a GUTTED check, file
      still present → `modified` (the case a set-only comparison structurally cannot
      see); a NEW unregistered check → `unregistered`; a tampered `smoke.py` driver →
      `modified`. A clean synthetic state returns nothing, so the detector is not
      trivially always-red.

  (3) THE MANIFEST PINS WHAT ACTUALLY RUNS — the manifest's glob is asserted to be the
      exact literal `smoke.py` uses to discover checks. Without this, the manifest could
      faithfully pin a set the oracle never runs (or miss files it does), and (1) would
      be a tautology over the wrong directory listing.

  (4) THE FREEZE COVERS ITS OWN WITNESS — this check file is itself in the manifest, so
      removing the freeze-enforcer is exactly as loud as removing anything it guards.

MUTATION → RED (the property this guards): make `verify()` return `[]` unconditionally,
drop the `missing` branch, or compare only filename SETS instead of digests, and (2)
fails — the synthetic deletion/gutting stops being reported. Point the manifest's glob
at a different pattern than `smoke.py`'s and (3) fails. Delete any check file without
regenerating the manifest and (1) fails, which is the entire point. Verified by hand
while drafting: stubbing `verify()` to `return []` flips (2) red immediately; reverted
before landing.

Reads only; mutates nothing on disk; touches no shared kernel state.

Contract: run(k, line). Fail loud (assert).
"""
from decima import oracle_manifest


def run(k, line):
    line("\n== ORACLE FREEZE — the check set is pinned (Batch D, lane 3) ==")

    manifest = oracle_manifest.load()

    # (1) THE REAL TREE IS FROZEN.
    findings = oracle_manifest.verify(manifest=manifest)
    assert not findings, oracle_manifest.describe(findings)
    line(f"  live tree matches the frozen manifest: {manifest['check_count']} checks + "
         f"the smoke.py driver, content-addressed ✓")

    # (3) THE MANIFEST PINS WHAT ACTUALLY RUNS — same glob smoke.py discovers with.
    smoke_src = oracle_manifest.SMOKE_PATH.read_text(encoding="utf-8")
    assert f'glob("{oracle_manifest.CHECK_GLOB}")' in smoke_src, (
        f"the manifest pins glob {oracle_manifest.CHECK_GLOB!r} but smoke.py does not "
        "discover checks with that exact pattern — the freeze would be pinning a set "
        "the oracle never runs")
    assert manifest.get("glob") == oracle_manifest.CHECK_GLOB, (
        "the committed manifest's glob disagrees with the module's")
    line(f"  the pinned glob {oracle_manifest.CHECK_GLOB!r} is the literal smoke.py "
         f"discovers with — the freeze covers exactly what runs ✓")

    # (4) THE FREEZE COVERS ITS OWN WITNESS.
    assert "514_oracle_freeze.py" in manifest["checks"], (
        "the freeze-enforcer must itself be pinned, or removing it is silent")

    # (2) THE DETECTOR ACTUALLY DETECTS — synthetic states, real detector, no real files
    #     touched. `live` is shaped exactly like scan()'s output.
    pinned = dict(manifest["checks"])
    driver = dict(manifest["driver"])
    victim = "510_reference_vectors.py"
    assert victim in pinned, "expected the vectors check to be pinned"

    def live_of(checks):
        return {"driver": dict(driver), "checks": checks, "check_count": len(checks)}

    def kinds(checks=None, drv=None):
        live = live_of(checks if checks is not None else dict(pinned))
        if drv is not None:
            live["driver"] = drv
        return {(f["kind"], f["file"]) for f in oracle_manifest.verify(manifest, live)}

    # a clean synthetic state is NOT reported (the detector is not always-red)
    assert kinds() == set(), "an unchanged tree must produce zero findings"

    # DELETED → missing (+ the count guard notices the shrink)
    deleted = dict(pinned)
    del deleted[victim]
    assert ("missing", victim) in kinds(deleted), "a deleted check was not reported"

    # RENAMED → missing AND unregistered (a rename is not laundered into a no-op)
    renamed = dict(pinned)
    renamed["510_renamed_away.py"] = renamed.pop(victim)
    rk = kinds(renamed)
    assert ("missing", victim) in rk and ("unregistered", "510_renamed_away.py") in rk, (
        "a renamed check must report BOTH the lost pin and the unregistered arrival")

    # GUTTED → modified (file present, body replaced — invisible to a set comparison)
    gutted = dict(pinned)
    gutted[victim] = oracle_manifest.file_digest(oracle_manifest.SMOKE_PATH)  # any other bytes
    assert ("modified", victim) in kinds(gutted), (
        "a gutted check (present but rewritten) was not reported — a set-only "
        "comparison would miss exactly this")

    # NEW unregistered check → unregistered
    added = dict(pinned)
    added["999_unregistered_lane.py"] = "cell_" + "0" * 8
    assert ("unregistered", "999_unregistered_lane.py") in kinds(added), (
        "an unregistered new check was not reported")

    # TAMPERED DRIVER → modified (smoke.py holds the inline FOLD §11 sections)
    assert ("modified", "smoke.py") in kinds(drv={"smoke.py": "cell_" + "0" * 8}), (
        "a tampered smoke.py driver was not reported")

    line("  detector proven on synthetic trees: deletion → missing · rename → "
         "missing+unregistered · gutted body → modified · new file → unregistered · "
         "tampered driver → modified ✓")
    line("  the oracle can now only get weaker DELIBERATELY — in a diff, under review ✓")
