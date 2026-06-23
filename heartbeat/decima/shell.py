"""The Shell — the one program. A projection of the Weave plus a prompt.

Every command here is a *view* over the same Weft. There is no separate admin
surface: `forge` and `revoke` use the same four verbs as `say`.
"""
import cmd

from decima.kernel import Kernel

BANNER = r"""
   ╔══════════════════════════════════════════════════════════╗
   ║   D E C I M A  ·  heartbeat                               ║
   ║   four verbs · one log · authority is a held object       ║
   ║   woven on the Loom — spun by Nona, cut by Morta          ║
   ╚══════════════════════════════════════════════════════════╝
   type `help` for commands.  `say echo hello`  ·  `forge`  ·  `attack`
"""


class Shell(cmd.Cmd):
    prompt = "decima› "

    def __init__(self, db_path="weft.db", fresh=False):
        super().__init__()
        self.k = Kernel(db_path, fresh=fresh)
        self.intro = BANNER

    # -- a turn ------------------------------------------------------------
    def do_say(self, arg):
        "say <text> — speak to Decima; it decides, allots a capability, acts."
        for line in self.k.say(arg):
            print("   " + line)

    # -- projections of the Weave -----------------------------------------
    def do_log(self, arg):
        "log — the Weft: every event, in order, with its authorizing capability."
        for ev in self.k.weft.events():
            who = self.k.keyring.name_of(ev.author)
            cap = ev.authorized[:8] if ev.authorized else "—"
            print(f"   e{ev.seq:<3} {ev.verb:<7} {who:<9} cap:{cap:<8} {ev.id[:8]}")

    def do_cells(self, arg):
        "cells — the materialized Weave (folded state)."
        w = self.k.weave()
        for t in ("capability", "agent", "utterance", "speech", "result"):
            cs = w.of_type(t)
            if cs:
                print(f"   {t}:")
                for c in cs:
                    extra = c.content.get("name") or c.content.get("text") \
                        or c.content.get("out") or c.content.get("objective", "")
                    q = " [quarantined]" if c.content.get("quarantined") else ""
                    print(f"     {c.id[:8]} v{c.version}{q}  {str(extra)[:46]}")

    def do_caps(self, arg):
        "caps — capabilities and their caveats (the authority surface)."
        for c in self.k.weave().of_type("capability"):
            cv = c.content.get("caveats", {})
            q = "quarantined" if c.content.get("quarantined") else "active"
            print(f"   {c.id[:8]}  {c.content['name']:<10} {c.content['effect']:<10} {q:<11} {cv}")

    def do_why(self, arg):
        "why <cell-prefix> — Law 4: provenance walk of how a cell came to be."
        c = self.k.weave().get(arg.strip())
        if not c:
            print("   no such cell"); return
        print(f"   {c.id[:8]} ({c.type}) built by:")
        for line in self.k.provenance(c):
            print(line)

    def do_fold(self, arg):
        "fold <seq> — Law 5: rebuild the world as of event <seq>. time-travel."
        try:
            seq = int(arg)
        except ValueError:
            print("   usage: fold <seq>"); return
        past = self.k.weave(upto_seq=seq)
        now = self.k.weave()
        print(f"   @e{seq}: {len(past.of_type('capability'))} caps, "
              f"{len(past.of_type('result'))} results  |  "
              f"now: {len(now.of_type('capability'))} caps, "
              f"{len(now.of_type('result'))} results")

    # -- Nona / Morta ------------------------------------------------------
    def do_forge(self, arg):
        "forge <name> <upper|lower|reverse|wc> <test_in> <expect> — Nona authors a capability."
        from decima import reckoner
        parts = arg.split()
        if len(parts) < 4:
            print("   usage: forge shout upper hello HELLO"); return
        name, fn, test_in, expect = parts[0], parts[1], parts[2], parts[3]
        report = reckoner.forge(self.k, name, "transform", fn, test_in, expect)
        print("   " + str(report))
        if report.passed:
            print(f"   → Decima now holds {name!r}. try:  say {name}: anything")

    def do_revoke(self, arg):
        "revoke <cap-prefix> — Morta: RETRACT a capability. authority withdrawn."
        c = self.k.weave().get(arg.strip())
        if not c:
            print("   no such cell"); return
        self.k.revoke(c.id)
        print(f"   [Morta] retracted {c.content.get('name', c.id[:8])} — next INVOKE fails closed")

    def do_attack(self, arg):
        "attack — demonstrate Law 2 with a zero-authority sandbox agent."
        for line in self.k.demo_attack():
            print("   " + line)

    def do_delegate(self, arg):
        "delegate — Decima spawns a subagent with its own key and a downhill, signed grant."
        for line in self.k.demo_delegation():
            print("   " + line)

    def do_replay(self, arg):
        "replay — an AuthorizationProof is bound to its exact request; it can't be reused."
        for line in self.k.demo_replay():
            print("   " + line)

    def do_whoami(self, arg):
        "whoami — the principals in this kernel."
        for p in self.k.keyring.principals.values():
            print(f"   {p.id[:8]}  {p.name:<10} ({p.kind})")

    def do_quit(self, arg):
        "quit — leave the shell (the Weft persists)."
        print("   the thread is measured. (weft.db persists)")
        return True

    do_EOF = do_quit


def main():
    import sys
    fresh = "--fresh" in sys.argv
    Shell(fresh=fresh).cmdloop()


if __name__ == "__main__":
    main()
