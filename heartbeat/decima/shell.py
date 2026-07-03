"""The Shell — the one program. A projection of the Weave plus a prompt.

Every command here is a *view* over the same Weft. There is no separate admin
surface: `forge` and `revoke` use the same four verbs as `say`.
"""
import cmd

from decima.kernel import Kernel
from decima.hashing import content_id
from decima.inbox import ApprovalInbox, InboxError
from decima.weft import ASSERT

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
        self.inbox = ApprovalInbox(self.k)
        self.intro = BANNER

    # -- a turn ------------------------------------------------------------
    def do_say(self, arg):
        "say <text> — speak to Decima; it decides, allots a capability, acts."
        # APPROVAL INBOX (Phase 2): if this turn would fire a Morta-gated
        # (requires_approval) outward/irreversible effect, it does NOT block inline.
        # We enqueue a durable inbox item — the human reviews it with `inbox` and
        # `approve <id>`/`deny <id>` — and the effect runs only once approved, through
        # the same gate. The brain only PROPOSES here; enqueuing grants nothing.
        agent = self.k.weave().get(self.k.decima_agent_id)
        action = self.k.brain.decide(arg, self.k.weave(), agent)
        if action.kind == "invoke" and self.inbox.is_gated(action.cap):
            # Record the utterance so the queued item has provenance to the request.
            uid = content_id({"utterance": arg, "lamport": self.k.weft.lamport})
            self.k.weft.append(self.k.human.id, ASSERT,
                               {"cell": uid, "type": "utterance", "content": {"text": arg}})
            cap = self.k.weave().get(action.cap)
            item = self.inbox.enqueue(
                agent, action.cap, action.args,
                description=f"{cap.content['name']}: {action.args}", provenance=uid)
            print(f"   you ▸ {arg}")
            print(f"   decima ▸ ⏸ Morta-gated effect [{cap.content['name']}] "
                  f"queued for approval #{item[:8]}")
            print(f"   review with `inbox`, then `approve {item[:8]}` or `deny {item[:8]}`")
            return
        for line in self.k.say(arg):
            print("   " + line)

    # -- the approval inbox (Phase 2 surface) ------------------------------
    def do_inbox(self, arg):
        "inbox — the pending Morta decisions: outward/irreversible effects awaiting a human."
        items = self.inbox.pending()
        if not items:
            print("   (inbox empty — no effects awaiting approval)")
            return
        for c in items:
            print(f"   #{c.id[:8]}  [{c.content.get('capability_name')}]  "
                  f"{c.content.get('description')}")

    def do_approve(self, arg):
        "approve <id> — approve a queued effect; it runs through the SAME Morta/authorize gate."
        ref = arg.strip()
        if not ref:
            print("   usage: approve <id>"); return
        try:
            res = self.inbox.approve(ref)
        except InboxError as e:
            print(f"   ✋ {e}"); return
        if "ok" in res:
            out = res["ok"].get("out", res["ok"])
            print(f"   [Morta] approved → effect ran: {out}")
        else:
            print(f"   ✋ approved, but the gate refused (no authority conferred): "
                  f"{res.get('denied')}")

    def do_deny(self, arg):
        "deny <id> — deny a queued effect; it is recorded as denied and never runs."
        ref = arg.strip()
        if not ref:
            print("   usage: deny <id>"); return
        try:
            self.inbox.deny(ref)
        except InboxError as e:
            print(f"   ✋ {e}"); return
        print(f"   [Morta] denied #{ref} — the effect will not run (recorded on the Weft)")

    # -- the go-live rail (Phase 2 · operator surface) ----------------------
    def do_live(self, arg):
        "live — go-live doctor: wire armed?, brain driver, egress grants, secrets (redacted), engines."
        from decima import golive
        # idempotent: binds an ALREADY-approved grant to the brain; grants nothing.
        print("   " + golive.bind_brain(self.k))
        for line in golive.doctor_lines(self.k):
            print("   " + line)

    def do_grant(self, arg):
        "grant <host> — request live egress to <host> (https only); a human decides via `inbox`/`approve`."
        from decima import golive
        host = arg.strip()
        if not host:
            print("   usage: grant api.anthropic.com"); return
        res = golive.request_grant(self.k, host)
        if res["status"] == "live":
            print(f"   egress to https://{res['host']} is already LIVE "
                  f"(grant {res['capability'][:8]} is human-approved)")
        elif res["status"] == "pending":
            print(f"   ⏸ grant request for https://{res['host']} queued for approval "
                  f"#{res['item'][:8]} — nothing is live yet")
            print(f"   review with `inbox`, then `approve {res['item'][:8]}` or "
                  f"`deny {res['item'][:8]}`")
        else:
            print(f"   ✋ {res.get('reason', res)}")

    def do_secrets(self, arg):
        "secrets — redacted credential list; `secrets intake` pulls DECIMA_SECRET_* / ANTHROPIC_API_KEY from the env."
        from decima import golive
        if arg.strip() == "intake":
            report = golive.intake_env(self.k)
            if not report:
                print("   (no DECIMA_SECRET_<NAME> / ANTHROPIC_API_KEY in the environment)")
            for r in report:
                print(f"   {r['name']}: {r['status']} — value held by the broker, "
                      f"never shown, never on the Weft")
            return
        d = golive.doctor(self.k)
        if not d["secrets"]:
            print("   (no credentials held — export DECIMA_SECRET_<NAME>, then `secrets intake`)")
        for s in d["secrets"]:
            print(f"   {s['name']}: {s['status']}")

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
        for t in ("capability", "agent", "task", "utterance", "speech", "result"):
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
        if report.findings:
            print(f"   scan findings: {report.findings}")
        if report.promoted:
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

    def do_tasks(self, arg):
        "tasks — the delegation tree (who briefed whom, with what capability, and outcome)."
        lines = self.k.task_tree()
        if not lines:
            print("   (no delegations yet)")
        for line in lines:
            print("   " + line)

    def do_score(self, arg):
        "score — organization outcome folded from the task tree (learned-policy signal)."
        s = self.k.org_score()
        print(f"   workers={s['workers']}  steps={s['steps']}  denials={s['denials']}  "
              f"completed={s['completed']}  latency≈{s['latency_ms']}ms  statuses={s['by_status']}")

    def do_ingest(self, arg):
        "ingest <url> — observe a URL (untrusted) and store it in memory as a non-instruction claim."
        decima = self.k.weave().get(self.k.decima_agent_id)
        res = self.k.ingest_observation(decima, arg.strip() or "about:blank")
        if "denied" in res:
            print("   ✋ " + res["denied"]); return
        print(f"   observed → claim {res['claim'][:8]} "
              f"(instruction_eligible={res['instruction_eligible']}) — recalled as data, never obeyed")

    def do_effects(self, arg):
        "effects — the registered effect handlers (the executor registry)."
        from decima import executor
        print("   " + ", ".join(executor.registered()))

    def do_view(self, arg):
        "view <notes|board|graph|timeline> — a projection of the Weave (one graph, many lenses)."
        from decima import workspace
        which = (arg.strip() or "notes").lower()
        if which == "notes":
            lines = workspace.notes(self.k.weave())
        elif which == "board":
            lines = workspace.board(self.k)
        elif which == "graph":
            lines = workspace.graph(self.k.weave())
        elif which == "timeline":
            lines = workspace.timeline(self.k.weft, self.k.keyring, limit=30)
        else:
            print("   usage: view notes|board|graph|timeline"); return
        if not lines:
            print("   (empty)")
        for line in lines:
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
