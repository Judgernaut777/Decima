"""The kernel — wires the primitives into a living system and boots Decima.

Boot:
  - mint root (authority source), executor, reckoner (Nona), human, and the
    Decima orchestrator's own principal
  - ASSERT bootstrap capabilities (echo, shell, forge) GRANTED to Decima
  - ASSERT the Decima orchestrator agent, bound to its principal, holding them
Every agent — Decima and every subagent — has its OWN key and signs its own
INVOKEs. Authority is a signed grant to a principal, never id-possession. The
first capability Decima ships with is the capability to author capabilities
(`forge`) — the bootstrap, Nona's first beat.
"""
import os

from decima.crypto import Keyring
from decima.weft import Weft, ASSERT, RETRACT, INVOKE, ATTEST
from decima.weave import Weave
from decima.capability import (capability_content, authorize, attenuate,
                               build_proof, verify_proof)
from decima.hashing import content_id, nfc
from decima.agent import make_brain, Action, _find_named
from decima import executor


class Kernel:
    def __init__(self, db_path: str, fresh: bool = False):
        seed_path = db_path + ".keys"
        if fresh:
            for p in (db_path, seed_path):
                if os.path.exists(p):
                    os.remove(p)

        seed = bytes.fromhex(open(seed_path).read().strip()) if os.path.exists(seed_path) else None
        self.keyring = Keyring(seed)
        if seed is None:
            with open(seed_path, "w") as f:
                f.write(self.keyring.master.hex())

        self.weft = Weft(db_path, self.keyring)
        self.brain = make_brain()
        self.spent: dict[str, float] = {}     # in-memory budget ledger (seam)
        self.approvals: set[str] = set()       # capabilities approved this session

        self.root = self.keyring.mint("root", "root")
        self.executor = self.keyring.mint("executor", "executor")
        self.reckoner = self.keyring.mint("nona", "reckoner")
        self.human = self.keyring.mint("you", "human")
        self.decima = self.keyring.mint("decima", "agent")

        if self.weft.count() == 0:
            self._boot()
        self.decima_agent_id = self._find_decima()

    # -- boot --------------------------------------------------------------
    def _boot(self):
        echo = self._assert_cap("echo", "echo")
        shell = self._assert_cap("shell", "shell", caveats={"budget": 100})
        forge = self._assert_cap("forge", "forge")
        agent_id = content_id({"agent": "decima-orchestrator"})
        self.weft.append(self.root.id, ASSERT, {
            "cell": agent_id, "type": "agent",
            "content": {
                "principal": self.decima.id,
                "objective": "serve the user; allot capability to the work",
                "brain": "rules-stub",
                "envelope": [echo, shell, forge],
                "budget": 100,
                "sandbox": False,
            },
        })

    def _assert_cap(self, name, effect, caveats=None) -> str:
        cap_id = content_id({"cap": name, "effect": effect})
        content = capability_content(name=name, effect=effect, caveats=caveats or {},
                                     grantee=self.decima.id, granter=self.root.id)
        self.weft.append(self.root.id, ASSERT,
                         {"cell": cap_id, "type": "capability", "content": content})
        return cap_id

    def _find_decima(self) -> str:
        for c in self.weave().of_type("agent"):
            if c.content.get("brain"):
                return c.id
        raise RuntimeError("no orchestrator agent found")

    # -- projections -------------------------------------------------------
    def weave(self, upto_seq=None) -> Weave:
        return Weave.fold(self.weft, upto_seq)

    def principal_for(self, agent_cell) -> str:
        # Each agent signs with its OWN bound principal — possession of its key.
        return agent_cell.content["principal"]

    # -- the core action path: authorize -> INVOKE -> execute -> ASSERT ----
    def invoke(self, agent_cell, cap_id, args) -> dict:
        w = self.weave()
        holder = self.principal_for(agent_cell)
        spent = self.spent.get(agent_cell.id, 0.0)
        # Bind the proof to THIS exact request: verb + body + nonce + frontier.
        nonce = os.urandom(16).hex()
        parents = [self.weft.head] if self.weft.head else []
        body = {"cap": cap_id, "args": args}
        proof = build_proof(w, self.keyring, holder, cap_id, INVOKE, body, nonce, parents)
        ok, reason = verify_proof(w, self.keyring, agent_cell, proof, INVOKE, body,
                                  nonce, parents, spent, self.approvals)
        if not ok:
            return {"denied": reason}

        cap = w.get(cap_id)
        # The INVOKE carries its AuthorizationProof and is signed by the holder's key.
        inv = self.weft.append(holder, INVOKE,
                               {**body, "nonce": nonce, "proof": proof}, authorized=cap_id)
        result = executor.execute(cap.content["effect"], cap.content.get("impl"), args)
        self.spent[agent_cell.id] = spent + float(args.get("cost", 0))
        rid = content_id({"result_of": inv.id})
        self.weft.append(self.executor.id, ASSERT, {
            "cell": rid, "type": "result",
            "content": {"of": inv.id, "cap": cap.content["name"], **result},
        })
        return {"ok": result, "result_cell": rid, "invoke_event": inv.id, "signer": holder}

    # -- granting = asserting a signed edge to a named grantee -------------
    def grant(self, cap_id, agent_id):
        w = self.weave()
        agent = w.get(agent_id)
        principal = agent.content["principal"]
        env = list(agent.content.get("envelope", []))
        if cap_id not in env:
            env.append(cap_id)
        self.weft.append(self.root.id, ASSERT,
                         {"cell": agent_id, "type": "agent",
                          "content": {**agent.content, "envelope": env}})
        cap = w.get(cap_id)
        if cap.content.get("grantee") != principal:
            self.weft.append(self.root.id, ASSERT,
                             {"cell": cap_id, "type": "capability",
                              "content": {**cap.content, "grantee": principal,
                                          "granter": self.root.id}})

    def spawn(self, parent_agent_cell, name, base_cap_id, stricter, objective,
              sandbox=False):
        """Decima allots: mint a subagent with its OWN key and a downhill, signed,
        attenuated grant. The GRANTER (parent) signs the grant event it issues."""
        w = self.weave()
        sub = self.keyring.mint(name, "agent")
        granter = parent_agent_cell.content["principal"]
        base = w.get(base_cap_id)
        att = attenuate(base.content, stricter, base_cap_id,
                        grantee=sub.id, granter=granter)
        att_id = content_id({"grant": name, "of": base_cap_id, "to": sub.id})
        self.weft.append(granter, ASSERT,
                         {"cell": att_id, "type": "capability", "content": att})
        sub_id = content_id({"agent": name, "by": granter})
        self.weft.append(granter, ASSERT, {
            "cell": sub_id, "type": "agent",
            "content": {
                "principal": sub.id, "objective": objective,
                "envelope": [att_id], "budget": att["caveats"].get("budget", 0),
                "sandbox": sandbox, "lineage": parent_agent_cell.id,
            },
        })
        return sub_id, att_id, sub

    def approve(self, cap_id):
        """A human (or a Morta policy) approves a requires_approval capability."""
        self.approvals.add(cap_id)

    def revoke(self, cap_id):
        """Morta: revocation = RETRACT of the capability cell."""
        self.weft.append(self.root.id, RETRACT, {"cell": cap_id})

    # -- high level: a spoken/typed turn -----------------------------------
    def say(self, text: str) -> list[str]:
        text = nfc(text)                       # normalize human text at the boundary (§1)
        transcript = []
        uid = content_id({"utterance": text, "lamport": self.weft.lamport})
        self.weft.append(self.human.id, ASSERT,
                         {"cell": uid, "type": "utterance", "content": {"text": text}})
        transcript.append(f"you ▸ {text}")

        agent = self.weave().get(self.decima_agent_id)
        action = self.brain.decide(text, self.weave(), agent)
        if action.reasoning:                       # the model brain's stated why
            transcript.append(f"decima ⟂ {action.reasoning}")
        if action.kind == "delegate":
            transcript.extend(self._delegate(agent, action, text))
            return transcript
        if action.kind == "respond":
            rid = content_id({"reply": action.text, "to": uid})
            self.weft.append(self.decima.id, ASSERT,
                             {"cell": rid, "type": "speech", "content": {"text": action.text}})
            transcript.append(f"decima ▸ {action.text}")
            return transcript

        res = self.invoke(agent, action.cap, action.args)
        if "denied" in res:
            transcript.append(f"decima ▸ ✋ denied: {res['denied']}")
            return transcript
        out = res["ok"].get("out", res["ok"])
        cap = self.weave().get(action.cap)
        transcript.append(f"decima ▸ [{cap.content['name']}] {out}")
        return transcript

    def _delegate(self, decima_cell, action, fallback_text) -> list[str]:
        """Decima reasons → spawns a worker with ONE attenuated grant + a brief,
        then the worker reasons over its narrow envelope and acts."""
        lines = []
        cap = _find_named(self.weave(), decima_cell, action.capability)
        if cap is None:
            lines.append(f"decima ▸ ✋ I don't hold a “{action.capability}” capability to delegate")
            return lines
        name = action.subagent or "Worker"
        objective = action.objective or fallback_text
        budget = int(action.budget) if action.budget else 10
        sub_id, grant_id, sub = self.spawn(decima_cell, name, cap.id,
                                           {"budget": budget}, objective)
        lines.append(f"decima ▸ spawns {name} ({sub.id[:8]}, own key), grants "
                     f"{cap.content['name']} (budget→{budget})  —  brief: “{objective}”")
        lines.extend(self._act_once(self.weave().get(sub_id), objective, name))
        return lines

    def _act_once(self, agent_cell, prompt, speaker) -> list[str]:
        """One decide→act cycle for any agent — used to run a briefed worker.
        The worker's brain sees only its own (narrow) envelope; authorize() gates it."""
        lines = []
        action = self.brain.decide(prompt, self.weave(), agent_cell)
        if action.reasoning:
            lines.append(f"{speaker} ⟂ {action.reasoning}")
        if action.kind == "delegate":
            lines.append(f"{speaker} ▸ (no authority to sub-delegate)")
            return lines
        if action.kind == "respond":
            lines.append(f"{speaker} ▸ {action.text}")
            return lines
        res = self.invoke(agent_cell, action.cap, action.args)
        if "denied" in res:
            lines.append(f"{speaker} ▸ ✋ denied: {res['denied']}")
            return lines
        cap = self.weave().get(action.cap)
        out = res["ok"].get("out", res["ok"])
        lines.append(f"{speaker} ▸ [{cap.content['name']}] {out}")
        return lines

    # -- demos -------------------------------------------------------------
    def demo_attack(self) -> list[str]:
        """Law 2: an under-privileged sandbox agent with no grants is denied."""
        out = []
        imp = self.keyring.mint("intruder", "agent")
        intruder_id = content_id({"agent": "intruder"})
        self.weft.append(self.root.id, ASSERT, {
            "cell": intruder_id, "type": "agent",
            "content": {"principal": imp.id, "objective": "exfiltrate",
                        "envelope": [], "sandbox": True},
        })
        intruder = self.weave().get(intruder_id)
        shell_cap = next(c.id for c in self.weave().of_type("capability")
                         if c.content.get("name") == "shell")
        res = self.invoke(intruder, shell_cap, {"cmd": "whoami"})
        out.append("intruder holds envelope: [] (nothing)")
        out.append(f"intruder INVOKE shell → {res.get('denied', res)}")
        out.append("→ no ambient authority. nothing to escalate toward.")
        return out

    def demo_delegation(self) -> list[str]:
        """Decima allots a downhill, signed, possession-proven grant to a
        subagent — and every way of abusing it fails at the right gate."""
        out = []
        decima = self.weave().get(self.decima_agent_id)
        shell = next(c for c in self.weave().of_type("capability")
                     if c.content.get("name") == "shell")

        # 1. Allot: a subagent with its OWN key and an attenuated grant.
        sub_id, grant_id, sub = self.spawn(
            decima, "Researcher", shell.id,
            stricter={"budget": 5, "requires_approval": True},
            objective="look something up",
        )
        out.append(f"Decima spawns Researcher (principal {sub.id[:8]}, own key)")
        out.append(f"  granted shell: budget 100→5, +requires_approval  [grant {grant_id[:8]}]")
        researcher = self.weave().get(sub_id)

        # 2. Morta approval gate.
        r = self.invoke(researcher, grant_id, {"cmd": "date", "cost": 3})
        out.append(f"Researcher INVOKE shell:date → ✋ {r['denied']}")

        # 3. Approved → runs, signed by Researcher's OWN key.
        self.approve(grant_id)
        r = self.invoke(researcher, grant_id, {"cmd": "date", "cost": 3})
        out.append(f"approved → Researcher INVOKE → {r['ok']['out']}  "
                   f"(signed by {r['signer'][:8]} = Researcher, not Decima)")

        # 4. Budget caveat bites on the second call (3 spent, +3 > 5).
        r = self.invoke(researcher, grant_id, {"cmd": "date", "cost": 3})
        out.append(f"Researcher 2nd call → ✋ {r['denied']}")

        # 5. The id is public. An impostor copies it into its envelope — refused.
        bad = self.keyring.mint("Impostor", "agent")
        bad_id = content_id({"agent": "impostor-deleg"})
        self.weft.append(self.root.id, ASSERT, {
            "cell": bad_id, "type": "agent",
            "content": {"principal": bad.id, "objective": "steal",
                        "envelope": [grant_id], "sandbox": False},
        })
        impostor = self.weave().get(bad_id)
        r = self.invoke(impostor, grant_id, {"cmd": "date", "cost": 3})
        out.append(f"Impostor copies the public grant id → ✋ {r['denied']}")

        # 6. Researcher reaches past its grant — no edge, no authority.
        forge = next(c for c in self.weave().of_type("capability")
                     if c.content.get("name") == "forge")
        r = self.invoke(researcher, forge.id, {})
        out.append(f"Researcher reaches for forge (never granted) → ✋ {r['denied']}")

        # 7. Downhill: Researcher cannot re-widen what it sub-delegates.
        _, intern_grant, _ = self.spawn(
            researcher, "Intern", grant_id,
            stricter={"budget": 50}, objective="help",   # asks to WIDEN 5→50
        )
        eff = self.weave().get(intern_grant).content["caveats"].get("budget")
        out.append(f"Researcher sub-delegates to Intern asking budget 50 → clamped to {eff} (downhill)")
        return out

    def demo_replay(self) -> list[str]:
        """A captured proof can't be reused against a different request or frontier."""
        out = []
        decima = self.weave().get(self.decima_agent_id)
        holder = self.principal_for(decima)
        echo = next(c for c in self.weave().of_type("capability")
                    if c.content.get("name") == "echo")
        nonce = os.urandom(16).hex()
        parents = [self.weft.head] if self.weft.head else []
        body = {"cap": echo.id, "args": {"text": "transfer $10"}}
        proof = build_proof(self.weave(), self.keyring, holder, echo.id,
                            INVOKE, body, nonce, parents)
        ok, _ = verify_proof(self.weave(), self.keyring, decima, proof, INVOKE,
                             body, nonce, parents, 0, self.approvals)
        out.append(f"Decima signs a proof for echo “transfer $10” → verifies: {ok}")

        evil = {"cap": echo.id, "args": {"text": "transfer $1000000"}}
        _, why2 = verify_proof(self.weave(), self.keyring, decima, proof, INVOKE,
                               evil, nonce, parents, 0, self.approvals)
        out.append(f"same proof, args tampered to $1000000 → ✋ {why2}")

        self.say("echo advance the frontier")                     # move the causal frontier
        moved = [self.weft.head]
        _, why3 = verify_proof(self.weave(), self.keyring, decima, proof, INVOKE,
                               body, nonce, moved, 0, self.approvals)
        out.append(f"same proof, replayed at a new frontier → ✋ {why3}")
        out.append("→ the proof is bound to verb+body+nonce+parents; it cannot be moved.")
        return out

    def provenance(self, cell) -> list[str]:
        """Law 4: walk the events that built a cell, each naming its cap + author."""
        lines = []
        index = {ev.id: ev for ev in self.weft.events()}
        for eid in cell.provenance:
            ev = index.get(eid)
            if not ev:
                continue
            who = self.keyring.name_of(ev.author)
            cap = ev.authorized[:8] if ev.authorized else "—"
            lines.append(f"  e{ev.seq:<3} {ev.verb:<7} by {who:<10} via cap {cap}")
        return lines
