"""The kernel — wires the primitives into a living system and boots Decima.

Boot sequence:
  - mint the root, executor, reckoner, and human principals
  - ASSERT the bootstrap capabilities (echo, shell, forge) authored by root
  - ASSERT the Decima orchestrator agent, holding those capabilities
The first capability Decima ships with is the capability to author capabilities
(`forge`). That is the bootstrap — Nona's first beat.
"""
import os

from decima.crypto import Keyring
from decima.weft import Weft, ASSERT, RETRACT, INVOKE, ATTEST
from decima.weave import Weave
from decima.capability import capability_content, authorize, attenuate
from decima.hashing import content_id
from decima.agent import Brain, Action
from decima import executor


class Kernel:
    def __init__(self, db_path: str, fresh: bool = False):
        if fresh and os.path.exists(db_path):
            os.remove(db_path)
        self.keyring = Keyring()
        self.weft = Weft(db_path, self.keyring)
        self.brain = Brain()
        self.spent: dict[str, float] = {}     # in-memory budget ledger (seam)
        self.approvals: set[str] = set()       # capabilities user has approved this session

        self.root = self.keyring.mint("decima", "root")
        self.executor = self.keyring.mint("executor", "executor")
        self.reckoner = self.keyring.mint("nona", "reckoner")
        self.human = self.keyring.mint("you", "human")

        if self.weft.count() == 0:
            self._boot()
        # Recover agent id from the weave on warm start.
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
                "objective": "serve the user; allot capability to the work",
                "brain": "rules-stub",
                "envelope": [echo, shell, forge],
                "budget": 100,
                "sandbox": False,
            },
        })

    def _assert_cap(self, name, effect, caveats=None) -> str:
        cap_id = content_id({"cap": name, "effect": effect})
        content = capability_content(name=name, effect=effect, caveats=caveats or {})
        self.weft.append(self.root.id, ASSERT,
                         {"cell": cap_id, "type": "capability", "content": content})
        return cap_id

    def _find_decima(self) -> str:
        w = self.weave()
        for c in w.of_type("agent"):
            if "orchestrator" in c.content.get("objective", "") or c.content.get("brain"):
                return c.id
        raise RuntimeError("no orchestrator agent found")

    # -- projections -------------------------------------------------------
    def weave(self, upto_seq=None) -> Weave:
        return Weave.fold(self.weft, upto_seq)

    def principal_for(self, agent_cell) -> str:
        # Decima the orchestrator signs with the root principal in this heartbeat.
        return self.root.id

    # -- the core action path: authorize -> INVOKE -> execute -> ASSERT ----
    def invoke(self, agent_cell, cap_id, args) -> dict:
        w = self.weave()
        spent = self.spent.get(agent_cell.id, 0.0)
        ok, reason = authorize(w, agent_cell, cap_id, args, spent, self.approvals)
        if not ok:
            return {"denied": reason}

        cap = w.get(cap_id)
        # Record the action request (Law 1: the INVOKE itself is an event).
        inv = self.weft.append(self.principal_for(agent_cell), INVOKE,
                               {"cap": cap_id, "args": args}, authorized=cap_id)
        # Execute the effect.
        result = executor.execute(cap.content["effect"], cap.content.get("impl"), args)
        self.spent[agent_cell.id] = spent + float(args.get("cost", 0))
        # The result returns to the world as an ASSERT carrying its provenance.
        rid = content_id({"result_of": inv.id})
        self.weft.append(self.executor.id, ASSERT, {
            "cell": rid, "type": "result",
            "content": {"of": inv.id, "cap": cap.content["name"], **result},
        })
        return {"ok": result, "result_cell": rid, "invoke_event": inv.id}

    # -- granting = asserting an edge (re-ASSERT the agent with a wider envelope)
    def grant(self, cap_id, agent_id):
        w = self.weave()
        agent = w.get(agent_id)
        env = list(agent.content.get("envelope", []))
        if cap_id not in env:
            env.append(cap_id)
        new_content = {**agent.content, "envelope": env}
        self.weft.append(self.root.id, ASSERT,
                         {"cell": agent_id, "type": "agent", "content": new_content})

    def revoke(self, cap_id):
        """Morta: revocation = RETRACT of the capability cell."""
        self.weft.append(self.root.id, RETRACT, {"cell": cap_id})

    # -- high level: a spoken/typed turn -----------------------------------
    def say(self, text: str) -> list[str]:
        """Assert the utterance, run the orchestrator one step, return a transcript."""
        transcript = []
        uid = content_id({"utterance": text, "lamport": self.weft.lamport})
        self.weft.append(self.human.id, ASSERT,
                         {"cell": uid, "type": "utterance", "content": {"text": text}})
        transcript.append(f"you ▸ {text}")

        agent = self.weave().get(self.decima_agent_id)
        action = self.brain.decide(text, self.weave(), agent)
        if action.kind == "respond":
            rid = content_id({"reply": action.text, "to": uid})
            self.weft.append(self.root.id, ASSERT,
                             {"cell": rid, "type": "speech", "content": {"text": action.text}})
            transcript.append(f"decima ▸ {action.text}")
            return transcript

        # invoke
        res = self.invoke(agent, action.cap, action.args)
        if "denied" in res:
            transcript.append(f"decima ▸ ✋ denied: {res['denied']}")
            return transcript
        out = res["ok"].get("out", res["ok"])
        cap = self.weave().get(action.cap)
        transcript.append(f"decima ▸ [{cap.content['name']}] {out}")
        return transcript

    # -- demos -------------------------------------------------------------
    def demo_attack(self) -> list[str]:
        """Law 2 in the flesh: an under-privileged sandbox agent tries to act."""
        out = []
        intruder_id = content_id({"agent": "intruder"})
        self.weft.append(self.root.id, ASSERT, {
            "cell": intruder_id, "type": "agent",
            "content": {"objective": "exfiltrate", "envelope": [], "sandbox": True},
        })
        intruder = self.weave().get(intruder_id)
        shell_cap = next(c.id for c in self.weave().of_type("capability")
                         if c.content.get("name") == "shell")
        res = self.invoke(intruder, shell_cap, {"cmd": "whoami"})
        out.append("intruder holds envelope: [] (nothing)")
        out.append(f"intruder tries to INVOKE shell → {res.get('denied', res)}")
        out.append("→ no ambient authority. nothing to escalate toward.")
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
