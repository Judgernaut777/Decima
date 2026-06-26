"""RECIPES1 — recipes / meal-planning: plan meals, aggregate a grocery list, and
order the groceries on the payments rail.

`CAPABILITY_MAP` Part B (household/cooking). A recipe is just data on the Weft; a
meal plan is edges (a slot → recipe); a grocery list is a pure projection that
DEDUPS ingredients across the plan; ordering the groceries MOVES MONEY, so it does
NOT mint a new authority — it composes SHOP1 (`shop.order`), which is itself a
Morta-gated, spend-capped, idempotent FINANCIAL payment (PAY1). Denied until
approved.

  - **a `recipe` Cell** holds {name, ingredients, steps}. Ingredient quantities are
    INTS (no floats — WEFT §4/§7); a float qty is rejected so none reaches signed
    content.
  - **a `meal_plan` Cell** + `plans` EDGES bind day slots to recipes on the Weft —
    provenance on the Weft, not in Python.
  - **`grocery_list`** folds the plan: walk its `plans` edges to the recipes and SUM
    the int quantities per item, DEDUPED across the whole plan.
  - **`order_groceries`** places ONE order per distinct grocery item via `shop.order`
    — Morta-gated (denied until the rail is approved), spend-capped, idempotent.

Pure composition: it calls `shop` / `scheduling` / `model` PUBLIC APIs and edits
none of them, no core file. A real recipe DB / grocery fulfilment slots in behind
the shop rail the same way a real payment provider does.
"""
from __future__ import annotations

from decima import shop, scheduling, model
from decima.hashing import content_id, nfc

RECIPE = "recipe"
MEAL_PLAN = "meal_plan"


def _recipe_id(name: str) -> str:
    return content_id({"recipe": nfc(name)})


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


# ── a recipe is a Weft Cell keyed by name ────────────────────────────────────
def add_recipe(k, name: str, *, ingredients: dict, steps: list,
               author: str | None = None) -> str:
    """Assert a `recipe` Cell for `name` and return its id (LWW by name).

    `ingredients` is an {item: int qty} map — every quantity MUST be a positive
    int (no floats reach signed content); `steps` is a list of instruction lines.
    Re-asserting the same name lands on the same Cell id (idempotent)."""
    name = nfc(name)
    clean: dict[str, int] = {}
    for item, qty in dict(ingredients).items():
        if not _is_int(qty):
            raise TypeError(f"ingredient {item!r} qty must be an int, got "
                            f"{type(qty).__name__}")
        if qty <= 0:
            raise ValueError(f"ingredient {item!r} qty must be positive, got {qty}")
        clean[nfc(str(item))] = int(qty)
    steps = [nfc(str(s)) for s in list(steps)]
    rid = _recipe_id(name)
    author = author or k.decima_agent_id
    model.assert_content(k.weft, author, rid, RECIPE, {
        "name": name, "ingredients": clean, "steps": steps,
    })
    return rid


def recipe(weave, name: str):
    """The `recipe` Cell for `name`, or None."""
    return weave.get(_recipe_id(name))


def recipes(k) -> list:
    """Every `recipe` Cell on the Weft."""
    return list(k.weave().of_type(RECIPE))


# ── a meal plan binds day slots to recipes via edges ─────────────────────────
def plan_meals(k, *, days: dict, name: str = "plan", author: str | None = None) -> str:
    """Assert a `meal_plan` Cell and bind each day slot to its recipe with a `plans`
    EDGE on the Weft. Returns the plan id.

    `days` maps a day-slot label → recipe name (e.g. {"mon": "Soup"}). The slot list
    is recorded on the Cell; the slot→recipe binding lives as `plans` edges so the
    plan's provenance is on the Weft. Each referenced recipe must already exist."""
    author = author or k.decima_agent_id
    slots = {nfc(str(slot)): nfc(str(rname)) for slot, rname in dict(days).items()}
    pid = content_id({"meal_plan": nfc(name), "slots": sorted(slots.items())})
    model.assert_content(k.weft, author, pid, MEAL_PLAN, {
        "name": nfc(name), "slots": slots,
    })
    for slot, rname in slots.items():
        rid = _recipe_id(rname)
        if k.weave().get(rid) is None:
            raise ValueError(f"meal plan slot {slot!r} references unknown recipe {rname!r}")
        # provenance on the Weft: the plan → recipe binding is an EDGE, tagged by slot.
        model.assert_edge(k.weft, author, pid, "plans", rid)
    return pid


def plan(weave, plan_id: str):
    """The `meal_plan` Cell for `plan_id`, or None."""
    return weave.get(plan_id)


def plan_recipes(k, plan) -> list:
    """The recipe Cells a meal plan binds, walked from its `plans` edges on the Weft."""
    pid = plan if isinstance(plan, str) else plan.id
    out = []
    for e in k.weave().edges_from(pid, "plans"):
        c = k.weave().get(e["dst"])
        if c is not None and c.type == RECIPE:
            out.append(c)
    return out


# ── the grocery list: aggregate + DEDUP ingredients across the plan ──────────
def grocery_list(k, plan) -> dict:
    """Aggregate the plan's ingredients into a shopping list: walk the plan's recipes
    and SUM the int quantities per item, DEDUPED across the whole plan.

    Returns {item: int total_qty}. The same item used by several recipes appears once,
    its quantity the sum; all quantities stay ints (recipes only hold int qtys)."""
    totals: dict[str, int] = {}
    for rcell in plan_recipes(k, plan):
        for item, qty in rcell.content.get("ingredients", {}).items():
            totals[item] = totals.get(item, 0) + int(qty)
    return dict(sorted(totals.items()))


# ── order the groceries on the SHOP1 rail (Morta-gated) ──────────────────────
def order_groceries(k, agent, plan, *, pay_cap, account: str = "default") -> dict:
    """Order the plan's groceries via SHOP1 — one `shop.order` per distinct grocery
    item, each a Morta-gated, spend-capped, idempotent FINANCIAL payment.

    Each item's catalog product must already exist (`shop.add_item`); the order qty is
    the deduped grocery total. Before the rail is approved every order is DENIED (no
    money moves); after `k.approve(pay_cap)` they are PLACED, receipts linked on the
    Weft. Returns {list, orders, placed, denied, total} — `orders` maps item → the
    `shop.order` result, `placed`/`denied` flags whether any/all were gated."""
    items = grocery_list(k, plan)
    pid = plan if isinstance(plan, str) else plan.id
    orders: dict[str, dict] = {}
    for item, qty in items.items():
        # one order per item; idempotency keyed by (plan, item) so a replay never
        # double-charges, and a placed item is not re-ordered on a second pass.
        res = shop.order(k, agent, item, int(qty),
                         idempotency_key=f"groceries:{pid}:{item}",
                         pay_cap=pay_cap, account=account)
        orders[item] = res
    placed = bool(orders) and all(o["placed"] for o in orders.values())
    denied = any(not o["placed"] for o in orders.values())
    total = sum(o.get("amount", 0) for o in orders.values())
    return {"list": items, "orders": orders, "placed": placed,
            "denied": denied, "total": total}


def schedule_plan(k, plan, *, at: int, name: str | None = None) -> str:
    """Optional: drop a `scheduled_event` reminder for a meal plan via SCHED1, so a
    due plan can fire a disposition. Returns the event id. (Composes scheduling's
    public API; `at` is an int logical tick.)"""
    pid = plan if isinstance(plan, str) else plan.id
    title = name or f"meal plan {pid[:8]}"
    return scheduling.schedule(k, title, at)
