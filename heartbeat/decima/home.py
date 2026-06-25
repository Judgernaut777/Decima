"""Home / devices (IoT) rail — a device action as the canonical OUTWARD effect (HOME1).

Turning a lock, opening a garage, switching a heater: a command leaving the box to
touch the physical world is outward and possibly irreversible (you cannot un-unlock a
door), so this rail invents NO new authority — it composes the primitives the kernel
already has, exactly like the payments rail (PAY1):

  • a `device` Cell carries the device's STATE on the Weft — the durable, folded,
    time-travelable record of "what is the lock right now"; an action UPDATES that
    cell (a new CONTENT version), so device state is graph state, audited like any cell;
  • a "home.act" executor effect — a DETERMINISTIC stub (no real hardware): it computes
    the next state from (kind, current, action) and never touches a device. A nonsense
    action for the device kind raises ExecError → a FAILED receipt (definite no-effect);
  • **Morta** (`requires_approval`) — a SENSITIVE action (e.g. `unlock`) is denied until
    a human/policy approves the capability; an un-approved sensitive command never runs;
  • a **sandbox profile** — only the `home.act` effect may run under this capability, and
    network is DENIED (a stub device needs none; the durable form would pin egress to the
    home hub). An over-reaching handler is refused before dispatch (SB1);
  • a full **EffectReceipt** on the Weft (status/effect_class) plus the updated `device`
    cell — so every command and every state change is auditable via `audit.audit_trail`.

Pure composition: registers its effect through the public `executor.register` (via
`kernel.integrate_tool`), forges/approves through Morta, and updates the device cell with
the public `model.assert_content` — it edits no kernel or other-module file.
"""
from decima import executor, model
from decima.hashing import content_id, nfc

DEVICE_EFFECT = "home.act"          # the registered effect name
DEVICE = "device"                   # the Cell type holding device state
EffectClass = "DEVICE"              # outward physical effect_class (audit signal)

# Per-device-kind action table: action -> resulting state. A `sensitive` action is one
# whose name is in SENSITIVE; everything reaching the handler is deterministic.
_KINDS = {
    "lock":   {"lock": "locked", "unlock": "unlocked"},
    "light":  {"on": "on", "off": "off"},
    "thermostat": {"heat": "heating", "cool": "cooling", "off": "idle"},
    "garage": {"open": "open", "close": "closed"},
}
# Sensitive actions are Morta-gated: an outward effect with real-world risk if mistaken.
SENSITIVE = {"unlock", "open"}


def is_sensitive(action: str) -> bool:
    """A sensitive action (e.g. unlock a door, open a garage) MUST be Morta-gated."""
    return nfc(str(action)) in SENSITIVE


def _device_id(name: str) -> str:
    return content_id({"device": nfc(str(name))})


def _act_handler(impl, args: dict) -> dict:
    """The device rail itself — a deterministic stub standing in for a smart-home hub.
    A real handler would speak to the hub over the network-denied-to-everything-but-hub
    sandbox; here it computes the next state from the action table and returns it.

    A bad request (unknown device kind, or an action the kind does not support) raises
    ExecError → a FAILED receipt: a definite no-effect, the device never moved."""
    kind = nfc(str(args.get("kind", "")))
    action = nfc(str(args.get("action", "")))
    table = _KINDS.get(kind)
    if table is None:
        raise executor.ExecError(f"unknown device kind {kind!r}")
    if action not in table:
        raise executor.ExecError(f"action {action!r} not valid for a {kind!r} device")
    return {"out": f"{kind} → {action}", "kind": kind, "action": action,
            "new_state": table[action]}


def register_device(k, name: str, kind: str, *, state: str = "unknown") -> str:
    """Assert a `device` Cell with its initial STATE on the Weft and return its id.
    Idempotent by name (same name → same cell id). State lives on the Weft so it is
    folded, audited, and time-travelable like every other cell."""
    kind = nfc(str(kind))
    if kind not in _KINDS:
        raise executor.ExecError(f"unknown device kind {kind!r}")
    did = _device_id(name)
    model.assert_content(k.weft, k.root.id, did, DEVICE, {
        "name": nfc(str(name)), "kind": kind, "state": state,
        "actions": sorted(_KINDS[kind]),
    })
    return did


def install_rail(k, *, name: str = DEVICE_EFFECT) -> str:
    """Register the `home.act` effect and forge a capability granted to Decima:
    Morta `requires_approval` (a sensitive command is denied until approved) and a
    sandbox profile that allows ONLY this effect with network DENIED. Returns the
    capability id. Mirrors payments.install_rail — same primitives, outward effect."""
    caveats = {
        "effect_class": EffectClass,
        "requires_approval": True,          # Morta gate (sensitive actions)
        # SB1 sandbox: only home.act may run under the cap; no network (a stub device
        # hub needs none — the durable form pins egress to the home hub host).
        "sandbox": {"effects": [name], "network": False},
    }
    return k.integrate_tool(name, _act_handler, caveats=caveats)


def device(k, name_or_id: str):
    """The current `device` cell (folded state), by name or id/prefix, or None."""
    w = k.weave()
    c = w.get(name_or_id) or w.get(_device_id(name_or_id))
    return c if (c is not None and c.type == DEVICE) else None


def act(k, agent_cell, cap_id, device_cell, action: str) -> dict:
    """Run a Morta-gated, sandboxed device action and, on success, UPDATE the device
    state cell (audited). Returns {status, result_cell, denied?, device, state, action}.

    Flow: (invoke) Morta-gated + sandboxed via the cap; the kernel emits the
    EffectReceipt. A sensitive action (e.g. unlock) is denied until `k.approve(cap_id)`.
    On SUCCEEDED, re-assert the device cell with the handler's computed `new_state` — a
    new CONTENT version on the Weft — so device state tracks the world and is auditable.
    A denial (Morta or sandbox) leaves the state cell untouched: a definite no-effect."""
    action = nfc(str(action))
    res = k.invoke(agent_cell, cap_id, {
        "kind": device_cell.content["kind"], "action": action,
    })
    out = {"action": action, "status": res.get("status"),
           "result_cell": res.get("result_cell"),
           "device": device_cell.id, "state": device_cell.content.get("state")}
    if "denied" in res:                                   # Morta or sandbox refusal
        out["denied"] = res["denied"]
        return out                                        # state cell untouched

    new_state = res["ok"]["new_state"]
    model.assert_content(k.weft, k.root.id, device_cell.id, DEVICE, {
        **device_cell.content, "state": new_state,
    })
    out["state"] = new_state
    return out
