#!/usr/bin/env python3
"""Smoke test — drive the kernel directly and watch the Five Laws hold.

Run: python3 smoke.py
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import reckoner


def line(s=""):
    print(s)


def main():
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    k = Kernel(db, fresh=True)

    line("== BOOT ==")
    w = k.weave()
    line(f"booted: {k.weft.count()} events, "
         f"{len(w.of_type('capability'))} bootstrap caps, "
         f"{len(w.of_type('agent'))} agent")
    line("caps: " + ", ".join(c.content["name"] for c in w.of_type("capability")))

    line("\n== LAW 1 (everything is an event) + the agent loop ==")
    for ln in k.say("echo hello, fates"):
        line("  " + ln)
    for ln in k.say("date"):
        line("  " + ln)

    line("\n== NONA: forge a capability, test-gate it, promote it ==")
    rep = reckoner.forge(k, "shout", "transform", "upper", "hello", "HELLO")
    line("  " + str(rep))
    line("  the system just grew an organ. now use it:")
    for ln in k.say("shout: the loom is weaving"):
        line("  " + ln)

    line("\n== NONA rejects a capability that fails its verifier ==")
    bad = reckoner.forge(k, "broken", "transform", "reverse", "abc", "ZZZ")
    line("  " + str(bad))
    line("  try to use the rejected (still-quarantined) cap:")
    for ln in k.say("broken: abc"):
        line("  " + ln)

    line("\n== LAW 2 (no ambient authority) ==")
    for ln in k.demo_attack():
        line("  " + ln)

    line("\n== LAW 5 (state is a fold — time travel) ==")
    head = k.weave().last_seq
    before_shout = 6  # right after boot + first two turns, before forge
    past = k.weave(upto_seq=before_shout)
    now = k.weave()
    line(f"  @e{before_shout}: caps={[c.content['name'] for c in past.of_type('capability')]}")
    line(f"  @head(e{head}): caps={[c.content['name'] for c in now.of_type('capability')]}")
    line("  'shout' did not exist in the past. the fold proves it.")

    line("\n== MORTA (revocation = RETRACT) ==")
    shout = next(c for c in now.of_type("capability") if c.content["name"] == "shout")
    k.revoke(shout.id)
    for ln in k.say("shout: are you still there"):
        line("  " + ln)
    line("  revoked. the next INVOKE failed closed.")

    line("\n== LAW 4 (provenance) — why does the Decima agent look the way it does? ==")
    agent = k.weave().get(k.decima_agent_id)
    line(f"  agent {agent.id[:8]} (envelope grew as caps were granted):")
    for pl in k.provenance(agent):
        line(pl)

    line("\n== DELEGATION + capability possession (signed, attenuated grants) ==")
    for ln in k.demo_delegation():
        line("  " + ln)

    line("\n== BRAIN-DRIVEN DELEGATION (Decima spawns + briefs a worker) ==")
    for ln in k.say("delegate shell as Clock: date"):
        line("  " + ln)

    line("\n== AUTHORIZATION PROOF (invocation binding — anti-replay) ==")
    for ln in k.demo_replay():
        line("  " + ln)

    line("\n== TAMPER-EVIDENCE (Law 1/4) ==")
    # Corrupt a payload byte directly in the DB and prove the fold rejects it.
    # (seq 5 is the "echo hello, fates" utterance — a real payload to tamper with.)
    k.weft.db.execute("UPDATE events SET payload = REPLACE(payload, 'fates', 'XXXXX') WHERE seq=5")
    k.weft.db.commit()
    try:
        k.weave()
        line("  !! tamper NOT detected (bug)")
    except Exception as e:  # noqa: BLE001
        line(f"  tamper detected on fold: {type(e).__name__}: {e}")

    line("\nheartbeat: alive. ✓")


if __name__ == "__main__":
    main()
