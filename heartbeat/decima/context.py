"""CTX1 — a code-aware context engine: map by STRUCTURE, pull only the SLICE.

The Augment Code learning, applied on the Weave: keyword search over a whole
codebase is the wrong primitive for handing context to a model. Code has a
*structure* — files define symbols, import modules, reference names — and the
context a task actually needs is the small CONNECTED region of that structure the
task touches, not every line that happens to share a word. So this module:

  - `index(k, files)`      — builds a STRUCTURAL map. Each file and each symbol it
                             defines becomes a `code_unit` Cell; typed edges record
                             `defines` (file → symbol), `imports` (file → module),
                             and `references` (unit → symbol) between units. The
                             parse is purely STRUCTURAL — regex over the text reading
                             `def`/`class`/`import`/name-use — and is performed on the
                             code as DATA. The code is NEVER executed, imported, or
                             eval'd; it is stored `instruction_eligible=False` (the
                             REVIEW1/FILES1 trust law) and only ever READ.
  - `relevant_slice(...)`  — given a task (a target symbol / file / query), returns the
                             MINIMAL connected slice of code units the task touches by
                             walking the structure graph (composing KNOW1's
                             `neighbors`/`subgraph`), bounded by an INT token/size
                             budget. This is "pull only the slice", not keyword over the
                             whole codebase: an unrelated, disconnected unit is excluded.
  - `context_for(...)`     — the slice rendered as the precise context to hand a model,
                             each unit carrying its provenance (cell id, path, kind, the
                             structural reason it was pulled in).

DETERMINISM: every step is a pure fold + sorted traversal, so re-running on the same
Weft yields a byte-identical slice and context. Budget is an INTEGER size budget (a
token/char estimate), never a float; the slice grows by structural distance until the
next unit would exceed it, then stops — a token-bounded selection is an int budget.

Public-API-only: composes `knowledge` (neighbors/subgraph), `review`/`files` (code as
untrusted DATA), `model` (typed edges), and the Weave. No kernel or other-module edits.
"""
from __future__ import annotations

import re

from decima import knowledge, review
from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc

# --- the Weave vocabulary this engine folds (all DATA, Law 3) -----------------------
CODE_UNIT = "code_unit"          # a file or a symbol, as a structural node
DEFINES = "defines"              # file_unit  → symbol_unit
IMPORTS = "imports"              # file_unit  → module_unit (the imported name)
REFERENCES = "references"        # unit       → symbol_unit it mentions by name

# Structural parse heuristics — pure text reads, never executed.
_DEF = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)")
_IMPORT = re.compile(r"^\s*import\s+([A-Za-z_][\w.]*)")
_FROM_IMPORT = re.compile(r"^\s*from\s+([A-Za-z_][\w.]*)\s+import\s+(.+)")
_NAME = re.compile(r"[A-Za-z_]\w*")
# rough token/size estimate: ~4 chars per token (an INT budget unit)
_CHARS_PER_TOKEN = 4


def _unit_id(kind: str, name: str) -> str:
    """Content-address a code unit by (kind, name) so the same file/symbol keeps one
    structural identity across re-indexing (idempotent map)."""
    return content_id({"code_unit": kind, "name": nfc(name)})


def file_unit_id(path: str) -> str:
    """The structural node id for a file."""
    return _unit_id("file", path)


def symbol_unit_id(name: str) -> str:
    """The structural node id for a defined symbol."""
    return _unit_id("symbol", name)


def _size_tokens(text: str) -> int:
    """An INTEGER token/size estimate for a blob of code (never a float)."""
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def _parse(code: str) -> dict:
    """STRUCTURAL parse of source text — reads names/imports/refs, NEVER executes.

    Returns {defines: [symbol], imports: [module], uses: [name]} where `uses` is the
    set of bare names mentioned anywhere (the candidate `references`). All ordering is
    deterministic (sorted) so the same source always yields the same map."""
    lines = code.split("\n")
    defines: set[str] = set()
    imports: set[str] = set()
    uses: set[str] = set()
    for ln in lines:
        m = _DEF.match(ln)
        if m:
            defines.add(m.group(1))
        mi = _IMPORT.match(ln)
        if mi:
            imports.add(mi.group(1).split(".")[0])
        mf = _FROM_IMPORT.match(ln)
        if mf:
            imports.add(mf.group(1).split(".")[0])
            for piece in mf.group(2).split(","):
                nm = piece.strip().split(" as ")[0].strip()
                if nm and nm != "*":
                    uses.add(nm)
        # bare-name uses: every identifier on the line is a candidate reference.
        for nm in _NAME.findall(ln):
            uses.add(nm)
    return {
        "defines": sorted(defines),
        "imports": sorted(imports),
        "uses": sorted(uses),
    }


def index(k, files: dict, *, author: str | None = None) -> dict:
    """Build the STRUCTURAL map for `files` (a {path: source} mapping).

    For each file: store the source as an untrusted `code` Cell (REVIEW1's `store_code`
    — code is DATA, `instruction_eligible=False`, never executed), mint a `code_unit`
    Cell for the file, a `code_unit` for every symbol it `defines`, and fold the typed
    edges `defines` / `imports` / `references` between units. `references` edges link a
    file unit to a symbol unit DEFINED in this index whose name the file mentions — so
    the structure graph captures who-touches-whom across files.

    Idempotent by content (units are content-addressed). Returns
    {"files": {path: unit_id}, "symbols": {name: unit_id}, "units": [all unit ids]}.
    Pure data: nothing here imports or runs the indexed code."""
    author = author or k.reckoner.id

    # First pass: parse every file (structurally) and register its file/symbol units.
    parsed: dict[str, dict] = {}
    file_units: dict[str, str] = {}
    symbol_units: dict[str, str] = {}
    for path in sorted(files):
        code = files[path]
        # store the source verbatim as UNTRUSTED DATA on the Weft (never executed).
        code_cell = review.store_code(k, path, code, author=author)
        info = _parse(code)
        parsed[path] = info
        fuid = file_unit_id(path)
        assert_content(k.weft, author, fuid, CODE_UNIT, {
            "kind": "file",
            "name": nfc(path),
            "path": nfc(path),
            "code_cell": code_cell,          # provenance → the DATA Cell holding bytes
            "size": _size_tokens(code),      # INT token/size estimate
            "defines": info["defines"],
            "imports": info["imports"],
        })
        file_units[path] = fuid
        for sym in info["defines"]:
            suid = symbol_unit_id(sym)
            assert_content(k.weft, author, suid, CODE_UNIT, {
                "kind": "symbol",
                "name": sym,
                "path": nfc(path),
                "code_cell": code_cell,
                "size": _size_tokens(sym),
            })
            symbol_units[sym] = suid

    # Second pass: fold the structural edges now that all units exist.
    for path in sorted(parsed):
        info = parsed[path]
        fuid = file_units[path]
        # defines: file → each symbol it defines.
        for sym in info["defines"]:
            assert_edge(k.weft, author, fuid, DEFINES, symbol_units[sym])
        # imports: file → an imported MODULE unit (mint a lightweight module unit).
        for mod in info["imports"]:
            muid = _unit_id("module", mod)
            assert_content(k.weft, author, muid, CODE_UNIT, {
                "kind": "module", "name": mod, "path": None,
                "code_cell": None, "size": _size_tokens(mod),
            })
            assert_edge(k.weft, author, fuid, IMPORTS, muid)
        # references: file → a symbol DEFINED in this index whose name it mentions
        # (excluding the file's own definitions — that is the `defines` edge).
        own = set(info["defines"])
        for sym in sorted(set(info["uses"]) & set(symbol_units)):
            if sym in own:
                continue
            assert_edge(k.weft, author, fuid, REFERENCES, symbol_units[sym])

    return {
        "files": file_units,
        "symbols": symbol_units,
        "units": sorted(set(file_units.values()) | set(symbol_units.values())),
    }


def _resolve_seed(k, task) -> str | None:
    """Resolve a task (a path, a symbol name, a unit id, or a Cell) to a seed
    `code_unit` id that exists in the fold — deterministically. Tries, in order:
    an exact file unit, an exact symbol unit, then KNOW1's id-prefix resolution.
    Returns None if nothing structural matches (a query that touches no code)."""
    w = k.weave()
    target = getattr(task, "id", task)
    for cand in (file_unit_id(target), symbol_unit_id(target), _unit_id("module", target)):
        c = w.cells.get(cand)
        if c is not None and c.type == CODE_UNIT and not c.retracted:
            return cand
    c = w.get(target)
    if c is not None and c.type == CODE_UNIT and not c.retracted:
        return c.id
    return None


def relevant_slice(k, task, *, budget: int, max_depth: int = 4) -> dict:
    """The MINIMAL connected slice of code units the `task` touches, bounded by `budget`.

    `budget` is an INTEGER token/size budget. Starting from the seed unit the task
    names, we grow outward along the STRUCTURE graph (KNOW1's `subgraph` reachability,
    ordered by hop-distance then id) and admit each unit while its cumulative size stays
    within `budget`. The result is therefore the connected region nearest the task —
    "pull only the slice" — and a unit unreachable through structure (a disconnected,
    unrelated file) is NEVER included, no matter how small the budget.

    Returns {"seed": id|None, "units": [ids in admission order], "edges": [hop dicts
    within the slice], "tokens": int, "budget": int, "truncated": bool, "reason":
    {id: why-pulled}}. Pure read-only traversal — asserts nothing."""
    w = k.weave()
    seed = _resolve_seed(k, task)
    empty = {"seed": None, "units": [], "edges": [], "tokens": 0,
             "budget": int(budget), "truncated": False, "reason": {}}
    if seed is None or budget <= 0:
        return empty

    # Reachability + hop-distance over the structure graph (deterministic).
    sg = knowledge.subgraph(k, seed, depth=max_depth)
    depths = sg["depths"]
    # admission order: nearest first, then by id — a stable, minimal-first ordering.
    order = sorted(sg["cells"], key=lambda cid: (depths.get(cid, 1 << 30), cid))

    admitted: list[str] = []
    reason: dict[str, str] = {}
    tokens = 0
    truncated = False
    for cid in order:
        cell = w.cells.get(cid)
        if cell is None or cell.type != CODE_UNIT or cell.retracted:
            continue
        size = int(cell.content.get("size", 0))
        if cid != seed and tokens + size > budget:
            # admitting this unit would breach the int budget → stop growing the slice.
            truncated = True
            break
        tokens += size
        admitted.append(cid)
        d = depths.get(cid, 0)
        reason[cid] = ("seed (the task target)" if cid == seed
                       else f"reachable via structure at depth {d}")

    admitted_set = set(admitted)
    # keep only the edges whose BOTH endpoints survived the budget — the slice is
    # itself a connected structural subgraph.
    edges = [e for e in sg["edges"]
             if e["src"] in admitted_set and e["dst"] in admitted_set]
    return {
        "seed": seed,
        "units": admitted,
        "edges": edges,
        "tokens": int(tokens),
        "budget": int(budget),
        "truncated": truncated,
        "reason": reason,
    }


def context_for(k, task, *, budget: int = 4000, max_depth: int = 4) -> dict:
    """The relevant slice rendered as the precise context to hand a model.

    Walks `relevant_slice`, then materializes each admitted unit into a record carrying
    its source body (read from the DATA `code` Cell — never executed) and full
    PROVENANCE: the unit cell id, the code Cell it came from, its kind/path, and the
    structural reason it was pulled into the slice. Returns
    {"task", "seed", "units": [record...], "edges", "tokens", "budget", "truncated"}.

    Each record: {"unit", "kind", "name", "path", "code_cell", "reason", "body"} where
    `body` is the verbatim source slice for that unit (the file's source for a file unit,
    or the file's source for a symbol unit, drawn from the untrusted DATA Cell). Read-only
    and deterministic."""
    w = k.weave()
    sl = relevant_slice(k, task, budget=budget, max_depth=max_depth)
    records = []
    for cid in sl["units"]:
        cell = w.cells.get(cid)
        if cell is None:
            continue
        c = cell.content
        body = None
        code_cell = c.get("code_cell")
        if code_cell:
            cc = w.cells.get(code_cell)
            if cc is not None and cc.content.get("body") is not None:
                # the source is DATA — handed to the model as context, NOT executed.
                body = cc.content["body"]
        records.append({
            "unit": cid,
            "kind": c.get("kind"),
            "name": c.get("name"),
            "path": c.get("path"),
            "code_cell": code_cell,
            "reason": sl["reason"].get(cid),
            "body": body,
        })
    return {
        "task": getattr(task, "id", task),
        "seed": sl["seed"],
        "units": records,
        "edges": sl["edges"],
        "tokens": sl["tokens"],
        "budget": sl["budget"],
        "truncated": sl["truncated"],
    }
