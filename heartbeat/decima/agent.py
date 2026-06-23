"""Agents — actors that observe the Weave, decide, and INVOKE capabilities.

The "brain" here is a deterministic rule stub so the heartbeat runs offline and
reproducibly. `Brain.decide` is the exact seam where a real model (frontier or a
cheap local reasoner) plugs in — everything around it (envelope, authorize,
the four verbs) is unchanged whether the brain is rules or a 70B.
"""
from dataclasses import dataclass


@dataclass
class Action:
    kind: str            # "invoke" | "respond"
    cap: str | None = None
    args: dict | None = None
    text: str | None = None


class Brain:
    """Rule-based decider. The LLM seam."""

    def decide(self, utterance: str, weave, agent_cell) -> Action:
        text = utterance.strip()

        # "<capname>: payload"  -> invoke the named, promoted capability
        if ":" in text:
            name, payload = text.split(":", 1)
            cap = self._find_cap(weave, agent_cell, name.strip().lower())
            if cap:
                return Action("invoke", cap=cap.id, args={"text": payload.strip()})

        low = text.lower()
        if low.startswith("echo "):
            cap = self._find_cap(weave, agent_cell, "echo")
            if cap:
                return Action("invoke", cap=cap.id, args={"text": text[5:]})
        if low in ("date", "time", "what time is it"):
            cap = self._find_cap(weave, agent_cell, "shell")
            if cap:
                return Action("invoke", cap=cap.id, args={"cmd": "date"})

        return Action("respond", text=f"heard “{text}” — no capability matched")

    @staticmethod
    def _find_cap(weave, agent_cell, name):
        env = set(agent_cell.content.get("envelope", []))
        for c in weave.of_type("capability"):
            if c.id in env and c.content.get("name") == name \
                    and not c.content.get("quarantined"):
                return c
        return None
