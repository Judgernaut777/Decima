"""RECIPES1 — recipes / meal-planning: plan meals, aggregate + DEDUP a grocery list,
and order the groceries on the payments rail.

Proves a household cooking capability COMPOSES the shop rail rather than reinventing
money movement:
  - add `recipe` Cells (ingredient quantities are INTS — a float qty is rejected);
  - plan meals across days → a `meal_plan` with `plans` edges on the Weft;
  - `grocery_list` aggregates + DEDUPS ingredients across the plan, summing INT qtys;
  - ordering the groceries via SHOP1 is Morta-gated: DENIED before the rail is
    approved → APPROVE → PLACED, receipts linked on the Weft.

Runs on its OWN fresh Kernel (it forges a FINANCIAL capability and moves "money").
Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import recipes, shop, payments, executor
from decima.kernel import Kernel


def run(_k, line):
    line("\n== RECIPES / MEAL-PLANNING (grocery order COMPOSES the Morta-gated shop rail) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated
    rail = payments.install_rail(k, cap=100, name="recipes.pay")         # hard cap = 100
    decima = lambda: k.weave().get(k.decima_agent_id)
    spent = lambda: k.spent.get(k.decima_agent_id, 0.0)

    # ---- (1) recipes: ingredient quantities are INTS ------------------------
    rid_soup = recipes.add_recipe(k, "Tomato Soup",
                                  ingredients={"tomato": 6, "onion": 1, "stock": 2},
                                  steps=["chop", "simmer", "blend"])
    rid_salad = recipes.add_recipe(k, "Garden Salad",
                                   ingredients={"tomato": 2, "lettuce": 1, "onion": 1},
                                   steps=["chop", "toss"])
    soup = recipes.recipe(k.weave(), "Tomato Soup")
    assert soup is not None and soup.content["ingredients"]["tomato"] == 6, soup
    assert all(isinstance(q, int) for q in soup.content["ingredients"].values()), soup
    line(f"  recipes: 'Tomato Soup' {rid_soup[:8]}, 'Garden Salad' {rid_salad[:8]} "
         f"(ingredient qtys are INT) ✓")

    # a float qty is rejected — no float reaches signed content
    try:
        recipes.add_recipe(k, "Bad", ingredients={"flour": 1.5}, steps=[])
        assert False, "float qty must be rejected"
    except TypeError as e:
        line(f"  law: float ingredient qty REJECTED — {e}")

    # ---- (2) plan meals across days → meal_plan + plans edges on the Weft ----
    pid = recipes.plan_meals(k, days={"mon": "Tomato Soup", "tue": "Garden Salad"})
    pcell = k.weave().get(pid)
    assert pcell is not None and pcell.type == "meal_plan", pcell
    bound = recipes.plan_recipes(k, pcell)
    assert len(bound) == 2, bound
    edges = k.weave().edges_from(pid, "plans")
    assert len(edges) == 2, edges
    line(f"  plan: {pid[:8]} binds mon→Soup, tue→Salad via {len(edges)} `plans` edges on the Weft ✓")

    # ---- (3) grocery_list aggregates + DEDUPS with correct INT quantities ----
    glist = recipes.grocery_list(k, pcell)
    # tomato: 6 (soup) + 2 (salad) = 8 — DEDUPED to one line, summed; onion 1+1=2.
    assert glist == {"lettuce": 1, "onion": 2, "stock": 2, "tomato": 8}, glist
    assert all(isinstance(q, int) for q in glist.values()), glist
    line(f"  grocery_list: {glist} (tomato 6+2=8 DEDUPED, onion 1+1=2; all INT) ✓")

    # ---- (4) ordering the groceries via SHOP1 is Morta-gated ----------------
    # catalog: each grocery item is a product (int price, minor units)
    for item, price in {"tomato": 3, "onion": 4, "stock": 5, "lettuce": 6}.items():
        shop.add_item(k, item, item.title(), price=price)
    # expected spend: 8*3 + 2*4 + 2*5 + 1*6 = 24+8+10+6 = 48 (<= cap 100)

    # pre-approval: DENIED — no money moves
    pre = recipes.order_groceries(k, decima(), pcell, pay_cap=rail)
    assert not pre["placed"] and pre["denied"], pre
    assert all(o["status"] == "denied" for o in pre["orders"].values()), pre["orders"]
    assert spent() == 0.0, spent()
    line(f"  pre-approval: order_groceries DENIED ({len(pre['orders'])} items, spent=0) — Morta gate ✓")

    k.approve(rail)                                                      # human/Morta approves
    line("  (a human approves the FINANCIAL rail capability — Morta gate)")

    # approved: PLACED, receipts linked on the Weft
    post = recipes.order_groceries(k, decima(), pcell, pay_cap=rail)
    assert post["placed"] and not post["denied"], post
    assert post["total"] == 48 and spent() == 48.0, (post["total"], spent())
    for item, o in post["orders"].items():
        assert o["placed"] and o["status"] == "placed", (item, o)
        oc = k.weave().get(o["order"])
        assert any(e["dst"] == o["receipt"]
                   for e in k.weave().edges_from(oc.id, "paid_by")), (item, oc)
    fin = [c for c in k.weave().of_type("result")
           if c.content.get("effect_class") == payments.FINANCIAL]
    assert len(fin) == 4, len(fin)                                      # one charge per item
    line(f"  approved: {len(post['orders'])} grocery orders PLACED → spent={int(spent())}/100; "
         f"{len(fin)} FINANCIAL receipts, paid_by edges on the Weft ✓")

    # ---- a re-order is idempotent: no double-charge -------------------------
    again = recipes.order_groceries(k, decima(), pcell, pay_cap=rail)
    assert again["placed"] and spent() == 48.0, (again, spent())       # unchanged
    assert all(o["idempotent_replay"] for o in again["orders"].values()), again["orders"]
    line(f"  re-order: idempotent replay, spent still {int(spent())} — no double-order ✓")
