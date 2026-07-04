"""The Shell — the one program. A projection of the Weave plus a prompt.

Every command here is a *view* over the same Weft. There is no separate admin
surface: `forge` and `revoke` use the same four verbs as `say`.

BATCH R (the P4/P5 operator surface): every landed P4/P5 module is now REACHABLE
from this prompt — `flip` (go-live engine flip), `mcp` (mount/list a foreign MCP
server), `mail` (gated inbound mail + digest), `corpus` (personal-corpus walk +
recall), `browse` (mediated browser), `citizen` (terminals-as-citizens), `human`
(multi-human principals), `update` (attested self-update), `voice` (the voice
session), `migrate` (schema reprojection), and `beat` now drives due jobs across
REAL parallel workers (`concurrency.run_concurrent`) while keeping the daemon's
durable checkpoint semantics. A verb confers NO authority: each one only COMPOSES
its module's public API, so every outward/gated act still routes through
kernel.invoke (authorize + Morta) and every foreign result lands as UNTRUSTED
DATA (`instruction_eligible=False`) — shown, recorded, never obeyed.
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


def _shell_codegen(intent):
    """The shell's deterministic default codegen for `update propose` — the injected
    MODEL seam `candidate.author_candidate` expects. Returns the shared normalizer
    source as TEXT: generated source is DATA on the Weft (untrusted-is-data), tested
    and scanned before it could ever run. A live model brain slots in by assigning
    `shell.codegen`; the promotion/Morta gates are identical either way."""
    from decima import candidate
    return candidate.NORMALIZER_SOURCE


def _identity_transform(content):
    """The shell's default migration transform: a pure copy of the old content —
    `migrate.migrate` itself stamps the new `schema_v` marker. A custom transform
    registers via `shell.transforms[name]`; either way the transform is data/config
    (its content-address is what the declaration records), never authority."""
    return dict(content)


class Shell(cmd.Cmd):
    prompt = "decima› "

    def __init__(self, db_path="weft.db", fresh=False):
        super().__init__()
        self.k = Kernel(db_path, fresh=fresh)
        self.inbox = ApprovalInbox(self.k)
        self.intro = BANNER
        # -- injectable OFFLINE seams for the P4/P5 surface (Batch R). These are
        # transport/codegen STUBS only — the wrapped-engine idiom: a stub replaces
        # the SOCKET (or the model), never a gate. Empty by default, so every verb
        # that needs the wire FAILS CLOSED until a human approves an egress grant
        # (the live path) or an oracle injects a stub (the offline path).
        self.mcp_transports = {}        # server name → transport(request)->response
        self.mail_transport = None      # gated-GET transport stub for `mail recv`
        self.browse_transport = None    # raw-GET transport stub for `browse`
        self.codegen = _shell_codegen   # deterministic codegen for `update propose`
        self.updates = {}               # (name, version) → propose_update handle
        self.transforms = {"identity": _identity_transform}
        self.migrations = {}            # migration cell id → define_migration handle

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

    # -- the P4/P5 operator surface (Batch R · every module gets its verb) ---
    def do_flip(self, arg):
        "flip <engine> <host> [shape] — flip a named engine LIVE behind a human-approved egress grant (fail closed without one)."
        from decima import golive
        parts = arg.split()
        if len(parts) < 2:
            print("   usage: flip shipping api.shipping.example "
                  "[post|get|get2|method|put|get_raw]"); return
        shape = parts[2] if len(parts) > 2 else "post"
        # THE flip: golive.activate_engine runs the same approved-grant test
        # bind_brain rides — no human-approved grant for this host ⇒ NO flip,
        # the engine stays offline (fail closed). The verb mints nothing.
        res = golive.activate_engine(self.k, parts[0], parts[1], shape=shape)
        if res["status"] != "live":
            print(f"   ✋ engine stays OFFLINE: {res.get('reason', res)}"); return
        print(f"   engine {res['engine']!r} is LIVE on https://{res['host']} — "
              f"grant {res['capability'][:8]}, wire-gated per call "
              f"(engine_live {res['cell'][:8]} on the Weft)")

    def do_mcp(self, arg):
        "mcp mount <name> [stdio <cmd...>|<url>] | list | tools <name> | resources <name> | read <name> <uri> — foreign MCP servers; everything foreign is DATA."
        from decima import executor, live_wire, mcp, wire
        parts = arg.split()
        if not parts:
            print("   usage: mcp mount <name> [stdio <cmd...> | <https-url>] · "
                  "mcp list · mcp tools <name> · mcp resources <name> · "
                  "mcp read <name> <uri>"); return
        op = parts[0]
        if op == "list":
            cells = mcp.mounts(self.k)
            if not cells:
                print("   (no MCP servers mounted)")
            for c in cells:
                print(f"   {c.content['server']}: {c.content['tool_count']} gated "
                      f"tool(s) — {', '.join(c.content.get('tools', []))}")
            return
        if len(parts) < 2:
            print("   usage: mcp <mount|tools|resources|read> <server> ..."); return
        server = parts[1]
        transport = self.mcp_transports.get(server)
        try:
            if op == "mount":
                if transport is None:
                    if len(parts) > 3 and parts[2] == "stdio":
                        transport = mcp.stdio_transport(parts[3:])
                    elif len(parts) > 2:
                        transport = mcp.http_transport(parts[2])  # gated: fails closed sans grant
                    else:
                        print("   ✋ no transport for that server — give `stdio "
                              "<cmd...>` / an https url, or inject one at "
                              "shell.mcp_transports (the offline seam)"); return
                    self.mcp_transports[server] = transport
                # mcp.mount: each foreign tool becomes a GATED realm capability
                # (foreign default: Morta approval required) — importing grants nothing.
                caps = mcp.mount(self.k, server, transport)
                self.mcp_transports[server] = transport
                print(f"   mounted {server!r}: {len(caps)} tool(s), each a GATED "
                      f"capability (foreign default Morta-gated) — every result "
                      f"will land as DATA, never an instruction")
            elif op == "tools":
                if transport is None:
                    print(f"   ✋ no live transport for {server!r} — mount it first "
                          f"(fail closed)"); return
                for t in mcp.list_tools(transport):
                    print(f"   [DATA] tool {str(t.get('name'))!r}: "
                          f"{str(t.get('description', ''))[:56]}")
            elif op == "resources":
                if transport is None:
                    print(f"   ✋ no live transport for {server!r} — mount it first "
                          f"(fail closed)"); return
                for r in mcp.resources_list(transport):
                    print(f"   [DATA] resource {r.get('uri')} "
                          f"({r.get('mimeType', '?')})")
            elif op == "read":
                if transport is None or len(parts) < 3:
                    print("   usage: mcp read <mounted-server> <uri>"); return
                res = mcp.resources_read(self.k, server, transport, parts[2])
                print(f"   [DATA] quarantined {res['chars']} chars of {res['uri']} → "
                      f"claim {res['claim'][:8]} (instruction_eligible="
                      f"{res['instruction_eligible']}) — cited, never obeyed")
            else:
                print("   usage: mcp mount|list|tools|resources|read ...")
        except (executor.ExecError, executor.Ambiguous,
                live_wire.NoGatedTransport, wire.EgressDenied) as e:
            print(f"   ✋ mcp refused: {e}")

    def do_mail(self, arg):
        "mail recv <https-endpoint> | digest — fetch inbound mail through the gated wire (untrusted DATA) and read the folded digest."
        from decima import golive, live_wire, mail_engine, maildigest, wire
        parts = arg.split()
        op = parts[0] if parts else "digest"
        if op == "digest":
            d = maildigest.digest(self.k)
            if not d["items"]:
                print("   (mail digest empty — nothing ingested)")
            for it in d["items"]:
                print(f"   [DATA] from {it['from']!r} — {it['subject']!r}: "
                      f"{str(it['summary'])[:44]!r}"
                      + (f" · ask: {it['ask']!r} (at most a Morta-gated proposal, "
                         f"never a command)" if it.get("ask") else ""))
            return
        if op != "recv" or len(parts) < 2:
            print("   usage: mail recv <https-endpoint> · mail digest"); return
        from urllib.parse import urlparse
        endpoint = parts[1]
        agent = self.k.weave().get(self.k.decima_agent_id)
        cap = golive.approved_egress_cap(self.k, urlparse(endpoint).hostname or "")
        try:
            # mail_engine.receive: the gated transport is the ONLY live path —
            # unwired (no stub, no approved grant) refuses before any socket.
            res = mail_engine.receive(self.k, agent, cap, endpoint=endpoint,
                                      transport=self.mail_transport)
        except (mail_engine.MailEngineError, live_wire.NoGatedTransport,
                wire.EgressDenied) as e:
            print(f"   ✋ mail refused: {e}"); return
        print(f"   received {res['received']} message(s) — each an UNTRUSTED "
              f"observation (instruction_eligible=False, however imperative its "
              f"text); read them with `mail digest`")

    def do_corpus(self, arg):
        "corpus ingest <path> | recall <query...> — walk files into citable UNTRUSTED claims; recall returns DATA with provenance."
        from decima import corpus
        parts = arg.split(None, 1)
        if len(parts) < 2 or parts[0] not in ("ingest", "recall"):
            print("   usage: corpus ingest <file-or-dir> · corpus recall <query>")
            return
        if parts[0] == "ingest":
            rep = corpus.ingest_path(self.k, parts[1].strip())
            print(f"   ingested {rep['files']} file(s) → {rep['chunks']} chunk(s): "
                  f"+{rep['ingested']} new claim(s), {rep['deduped']} deduped — all "
                  f"instruction_eligible=False (a file is DATA, never a command)")
            return
        hits = corpus.recall_corpus(self.k, parts[1].strip())
        if not hits:
            print("   (no corpus hits)")
        for h in hits[:8]:
            print(f"   [DATA] {str(h['text'])[:48]!r} ← {h['source']} "
                  f"(instruction_eligible={h['instruction_eligible']})")

    def do_browse(self, arg):
        "browse <url> | browse read <url> — fetch a page through the egress gate; the page is DATA, never an instruction."
        from decima import golive, live_wire, mediated_browser, wire
        parts = arg.split()
        if not parts:
            print("   usage: browse <url> · browse read <url>"); return
        if parts[0] == "read":
            if len(parts) < 2:
                print("   usage: browse read <url>"); return
            page = mediated_browser.read(self.k, parts[1])
            if not page["found"]:
                print(f"   (nothing fetched yet for {page['url']})"); return
            print(f"   [DATA] {page['url']} → claim {page['page'][:8]} "
                  f"(instruction_eligible={page['instruction_eligible']}): "
                  f"{str(page['text'])[:48]!r} — recalled, never obeyed")
            return
        from urllib.parse import urlparse
        url = parts[0]
        agent = self.k.weave().get(self.k.decima_agent_id)
        cap = golive.approved_egress_cap(self.k, urlparse(url).hostname or "")
        transport = self.browse_transport
        if transport is None and cap is not None:
            # the ONE sanctioned live path — the gate re-runs the full rule of
            # egress per call; with neither a grant nor a stub, fetch fails closed.
            transport = live_wire.gated_get_raw_transport(self.k, agent, cap)
        try:
            res = mediated_browser.fetch(self.k, agent, cap, url, transport=transport)
        except (mediated_browser.MediatedBrowserError, live_wire.NoGatedTransport,
                wire.EgressDenied) as e:
            print(f"   ✋ browse refused: {e}"); return
        print(f"   fetched {url} (status {res['status']}) → page claim "
              f"{res['page'][:8]} — stored instruction_eligible=False "
              f"(the page is DATA, never obeyed)")

    def do_citizen(self, arg):
        "citizen admit <name> [cap-prefix] | list — admit a terminal/tool as an ATTENUATED citizen; list the realm's citizens."
        from decima import citizens
        parts = arg.split()
        if not parts or parts[0] not in ("admit", "list"):
            print("   usage: citizen admit <name> [from-cap-prefix] · citizen list")
            return
        if parts[0] == "list":
            got = citizens.citizens(self.k)
            if not got:
                print("   (no citizens admitted)")
            for c in got:
                env = ", ".join(f"{g['effect']}@{g['target']}"
                                for g in c["envelope"]) or "(empty — default-deny)"
                print(f"   {c['citizen'][:8]}  {c['name']:<12} envelope: {env}")
            return
        if len(parts) < 2:
            print("   usage: citizen admit <name> [from-cap-prefix]"); return
        from_cap, narrow = None, None
        if len(parts) > 2:
            cell = self.k.weave().get(parts[2])
            if cell is None or cell.type != "capability":
                print("   ✋ no such capability to attenuate from (fail closed)")
                return
            from_cap = cell.id
            narrow = {"effects": [cell.content["effect"]]}
        try:
            adm = citizens.admit_citizen(self.k, parts[1], from_cap=from_cap,
                                         narrow=narrow)
        except citizens.CitizenError as e:
            print(f"   ✋ {e}"); return
        print(f"   admitted citizen {parts[1]!r} ({adm['citizen'][:8]}) — envelope: "
              + (f"one grant {adm['grant'][:8]}, attenuated DOWNHILL"
                 if adm["grant"]
                 else "EMPTY (default-deny: it can invoke nothing)"))

    def do_human(self, arg):
        "human register <subject> [grant,names] | whoami <subject> | view <subject> — per-human principals, each with their OWN scoped authority."
        from decima import multihuman
        parts = arg.split()
        if len(parts) < 2 or parts[0] not in ("register", "whoami", "view"):
            print("   usage: human register <subject> [realm-cap,names] · "
                  "human whoami <subject> · human view <subject>"); return
        subject = parts[1]
        try:
            if parts[0] == "register":
                grants = [g for g in (parts[2].split(",") if len(parts) > 2 else [])
                          if g]
                reg = multihuman.register_human(self.k, subject, grants=grants)
                print(f"   enrolled {subject!r} as principal {reg['principal'][:8]} "
                      f"— scope {reg['scope']!r}, holding {len(reg['caps'])} "
                      f"attenuated grant(s), nothing ambient")
            elif parts[0] == "whoami":
                act = multihuman.acting_as(self.k, subject)
                print(f"   {act['subject']} = principal {act['principal'][:8]} · "
                      f"scope {act['scope']!r} · holds: "
                      f"{', '.join(sorted(act['caps'])) or '(nothing)'}")
            else:
                v = multihuman.view_of(self.k, subject)
                print(f"   view of {v['subject']!r}: scope {v['scope']!r} · caps "
                      f"{sorted(v['caps']) or '(none)'} · pending {len(v['pending'])} "
                      f"· claims {len(v['claims'])} — a projection, it grants nothing")
        except multihuman.MultiHumanError as e:
            print(f"   ✋ {e}")

    def do_update(self, arg):
        "update propose <name> <ver> <goal...> | promote <name> <ver> [tier] | activate <name> <ver> | rollback <name> | status <name> — attested, Morta-gated self-update."
        from decima import reckoner, selfupdate
        from decima.promotion import PromotionBlocked
        parts = arg.split()
        usage = ("   usage: update propose <name> <ver> <goal> · promote <name> "
                 "<ver> [tier] · activate <name> <ver> · rollback <name> · "
                 "status <name>")
        if not parts:
            print(usage); return
        op = parts[0]
        try:
            if op == "propose" and len(parts) >= 4:
                name, version = parts[1], int(parts[2])
                upd = selfupdate.propose_update(self.k, name, " ".join(parts[3:]),
                                                self.codegen, version=version)
                self.updates[(name, version)] = upd
                print(f"   proposed {name} v{version} — candidate "
                      f"{upd['candidate']['cell'][:8]} BORN QUARANTINED "
                      f"(default-deny: nothing is active yet)")
            elif op == "promote" and len(parts) >= 3:
                name, version = parts[1], int(parts[2])
                upd = self.updates.get((name, version))
                if upd is None:
                    print("   ✋ no proposed update by that name/version in this "
                          "session — `update propose` first (fail closed)"); return
                ev = reckoner.evaluate(self.k, upd["candidate"])
                tier = parts[3] if len(parts) > 3 else "workspace_write"
                rep = selfupdate.promote_update(self.k, upd, ev, tier=tier)
                print(f"   promoted {name} v{version} (tier {rep['tier']}) — "
                      f"attested; activation still needs the Morta gate")
            elif op == "activate" and len(parts) >= 3:
                rep = selfupdate.activate(self.k, parts[1], int(parts[2]))
                print(f"   [Morta] activated {rep['name']} v{rep['version']} — the "
                      f"pointer moved (the old version stays on the Log)")
            elif op == "rollback" and len(parts) >= 2:
                rep = selfupdate.rollback(self.k, parts[1])
                print(f"   [Morta] rolled {rep['name']} back to v{rep['version']}")
            elif op == "status" and len(parts) >= 2:
                v = selfupdate.active(self.k, parts[1])
                hist = selfupdate.history(self.k, parts[1])
                print(f"   {parts[1]}: active v{v} · pointer history "
                      f"{[h.get('version') for h in hist]}")
            else:
                print(usage)
        except ValueError:
            print("   usage: update ... (<ver> is an int)")
        except (selfupdate.SelfUpdateError, selfupdate.ActivationDenied,
                PromotionBlocked) as e:
            print(f"   ✋ {e} — fail closed, the active version is unchanged")

    def do_voice(self, arg):
        "voice owner <audio-ref> | ambient <audio-ref> | say <text...> | log — owner turn = proposal, ambient = DATA, speech Morta-gated."
        from decima import voice, voice_shell
        parts = arg.split(None, 1)
        if not parts:
            print("   usage: voice owner <audio-ref> · voice ambient <audio-ref> · "
                  "voice say <text> · voice log"); return
        op, rest = parts[0], (parts[1].strip() if len(parts) > 1 else "")
        w = self.k.weave()
        if not any(c.content.get("name") == voice.LISTEN and not c.retracted
                   for c in w.of_type("capability")):
            voice.install(self.k)       # the stub engine, behind the same contract
        sid = voice_shell.session(self.k, "shell")
        if op in ("owner", "ambient"):
            if not rest:
                print(f"   usage: voice {op} <audio-ref>"); return
            t = voice_shell.turn(self.k, sid, rest, owner=(op == "owner"))
            if "denied" in t:
                print(f"   ✋ {t['denied']}"); return
            print(f"   turn #{t['seq']} ({t['role']}): {t['text'][:48]!r} — "
                  + ("proposed through the ORDINARY gate (dispatched="
                     f"{t['dispatched']})" if t["role"] == "owner"
                     else "recorded as DATA (instruction_eligible=False), "
                          "NEVER dispatched"))
        elif op == "say":
            res = voice_shell.say(self.k, rest)
            if "denied" in res:
                print(f"   ✋ speech refused (Morta): {res['denied']}"); return
            print(f"   {res['ok']['out']}")
        elif op == "log":
            for t in voice_shell.transcript(self.k, sid):
                print(f"   #{t['seq']} {t['role']:<7} {t['text'][:44]!r} "
                      f"dispatched={t['dispatched']}")
        else:
            print("   usage: voice owner|ambient|say|log ...")

    def do_migrate(self, arg):
        "migrate define <type> <from_v> <to_v> [transform] | run <cell-prefix> | list — declared, append-only schema reprojection."
        from decima import migrate as mig
        parts = arg.split()
        if not parts:
            print("   usage: migrate define <type> <from_v> <to_v> [transform] · "
                  "migrate run <migration-cell-prefix> · migrate list"); return
        try:
            if parts[0] == "define" and len(parts) >= 4:
                fn = self.transforms.get(parts[4] if len(parts) > 4 else "identity")
                if fn is None:
                    print(f"   ✋ unknown transform (registered: "
                          f"{sorted(self.transforms)})"); return
                handle = mig.define_migration(self.k, parts[1], int(parts[2]),
                                              int(parts[3]), fn)
                self.migrations[handle["cell"]] = handle
                print(f"   declared migration {handle['cell'][:8]}: {parts[1]} "
                      f"v{handle['from_v']} → v{handle['to_v']} (the transform is "
                      f"DATA, never authority)")
            elif parts[0] == "run" and len(parts) >= 2:
                match = [h for cid, h in self.migrations.items()
                         if cid.startswith(parts[1])]
                if not match:
                    print("   ✋ no declared migration by that id in this session — "
                          "`migrate define` first (the transform callable does not "
                          "fold)"); return
                rep = mig.migrate(self.k, match[0])
                print(f"   migrated {rep['migrated']} cell(s), skipped "
                      f"{rep['skipped']} — append-only (every old shape stays on "
                      f"the Log; run {rep['run'][:8]})")
            elif parts[0] == "list":
                decls = self.k.weave().of_type(mig.MIGRATION)
                if not decls:
                    print("   (no migrations declared)")
                for c in decls:
                    print(f"   {c.id[:8]}  {c.content['type']} "
                          f"v{c.content['from_v']} → v{c.content['to_v']}")
            else:
                print("   usage: migrate define|run|list ...")
        except ValueError:
            print("   usage: migrate define <type> <int from_v> <int to_v>")
        except mig.MigrateError as e:
            print(f"   ✋ {e} — fail closed, nothing migrated")

    # -- the always-on substrate (Batch A · production beat driver) ---------
    def do_beat(self, arg):
        "beat [upto] [workers] — advance the durable run-loop to the logical frontier: due jobs fire across REAL parallel workers, then the checkpoint lands."
        # THE production caller of the always-on substrate: sweep the durable
        # run-loop from its Weft-folded checkpoint THROUGH the current logical
        # frontier (default: this Weft's lamport — the operator owns the clock,
        # never a wall-clock). BATCH R: the DUE JOBS now fire across real
        # parallel worker threads first (`concurrency.run_concurrent` — the
        # effects overlap, ONLY the Weft commit serializes, and each job still
        # fires through ONLY its pre-fixed single-use lease), then daemon.resume
        # drives one reactor.tick per frontier — watchers, due events, crash
        # recovery — and records ONE new loop_checkpoint Cell, so the NEXT beat
        # (or the next process) continues instead of re-scanning. The beat
        # itself confers NO authority: every fired lane passes its own gates
        # (dispositions, pre-fixed job leases, Morta on anything irreversible).
        from decima import concurrency, daemon, observ, resume as _resume
        parts = arg.split()
        try:
            upto = int(parts[0]) if parts else int(self.k.weft.lamport)
            workers = int(parts[1]) if len(parts) > 1 else 2
        except ValueError:
            print("   usage: beat [<int logical frontier>] [<int workers>]"); return
        cp = daemon.checkpoint(self.k)
        if upto <= cp:
            # fail closed + friendly: the cursor never moves backward, and an
            # already-checkpointed frontier is a genuine no-op (nothing ticked).
            print(f"   beat: quiet — the loop is already checkpointed at e{cp} "
                  f"(asked e{upto}); nothing ticked, nothing fired")
            return
        jobs_before = observ.metrics(self.k)["jobs"]
        # PARALLEL DUE WORK (Batch R): reconcile crash-fired jobs FIRST (their
        # spent leases would deny the workers — recovery, not re-run), then run
        # every due job across up to `workers` real threads. Exactly-once stays
        # the LEASE's law: a racing commit is denied by the exhausted lease, so
        # the fired set equals a serial pass's.
        _resume.recover(self.k, upto)
        par = concurrency.run_concurrent(self.k, upto, workers=workers)
        out = daemon.resume(self.k, upto)                        # ← the beat
        jobs_after = observ.metrics(self.k)["jobs"]
        print(f"   beat: checkpoint e{out['resumed_from']} → e{out['to']} · "
              f"ticked {len(out['ticked'])} frontier(s) · fired {out['fired']}"
              + (" · quiet" if out["quiet"] else ""))
        print(f"   workers: {par['fired']} due job(s) fired across {workers} "
              f"parallel worker(s) · {par['denied']} lease-denied")
        print(f"   jobs: +{jobs_after['done'] - jobs_before['done']} done · "
              f"+{jobs_after['recovered'] - jobs_before['recovered']} recovered · "
              f"{jobs_after['enqueued']} still enqueued")

    def do_metrics(self, arg):
        "metrics — the folded operational report (a pure lens: ints only, adds nothing)."
        from decima import observ
        for line in observ.dashboard_lines(self.k):
            print("   " + line)

    def do_backup(self, arg):
        "backup <path> — export the whole signed event log as a verifiable, tamper-evident manifest."
        import json
        from decima import backup as bk
        from decima.weft import WeftError
        path = arg.strip()
        if not path:
            print("   usage: backup <path>"); return
        try:
            blob = bk.backup(self.k)     # refuses to certify an unsound source log
        except (bk.BackupError, WeftError) as e:
            print(f"   ✋ backup refused: {e}"); return
        with open(path, "w") as f:
            json.dump(blob, f)
        print(f"   backed up {blob['count']} events → {path} (root {blob['root'][:8]})")

    def do_restore(self, arg):
        "restore <manifest> [db] — replay a verified backup into a fresh db; a tampered blob is refused."
        import json
        from decima import backup as bk
        parts = arg.split()
        if not parts:
            print("   usage: restore <manifest.json> [dest.db]"); return
        src = parts[0]
        dest = parts[1] if len(parts) > 1 else src + ".restored.db"
        try:
            with open(src) as f:
                blob = json.load(f)
        except (OSError, ValueError) as e:
            print(f"   ✋ unreadable manifest: {e}"); return
        # cheap distrust FIRST: verify the manifest's own bytes before a single
        # row touches a database — a tampered blob is refused here, fail closed.
        ok, reason = bk.verify(blob)
        if not ok:
            print(f"   ✋ restore refused: {reason} — fail closed, nothing restored")
            return
        try:
            weft = bk.restore(blob, dest, keyring=self.k.keyring)
        except bk.BackupError as e:
            print(f"   ✋ restore refused: {e}"); return
        print(f"   restored {weft.count()} events → {dest} "
              f"(every row re-earned its place through Weft.ingest)")

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
