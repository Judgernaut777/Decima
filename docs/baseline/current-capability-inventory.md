# Current capability inventory (frozen 2026-07-11)

The out-of-box catalog: the **25 bundled real engines** declared in
`heartbeat/decima/builtin_manifests.py` (`BUILTINS`). Each is a hand-wrapped engine with
its own gated install path (`kernel.integrate_tool`); `register_builtins(k)` folds one
`capability_manifest` per engine onto the Weft so `discovery.discover()` can find a real
engine before forging a new one. **A manifest grants nothing** — registering a
description confers no authority; the engine keeps its own Morta-gated install path.

## The 25 bundled engines

| Engine module | Archetype | Effect class | Gated | 0.3 disposition |
|---|---|---|---|---|
| `stripe_rail` | EFFECT | FINANCIAL | Morta approval | **defer** (no autonomous payments in 0.3) |
| `payouts` | EFFECT | FINANCIAL | Morta approval | **defer** |
| `brokerage_engine` | EFFECT | FINANCIAL | Morta approval | **defer** (no live brokerage in 0.3) |
| `exchange` | EFFECT | FINANCIAL | Morta approval | **defer** |
| `payroll` | EFFECT | FINANCIAL | Morta approval | **defer** |
| `shipping` | EFFECT | FINANCIAL | Morta approval | **defer** |
| `cloud_compute` | EFFECT | FINANCIAL | Morta approval | **defer** |
| `ecommerce` | EFFECT | FINANCIAL | Morta approval | **defer** |
| `ads` | EFFECT | FINANCIAL | Morta approval | **defer** |
| `comms` | EFFECT | COMMUNICATION | Morta approval | defer (email send deferred until containment proven) |
| `paging` | EFFECT | COMMUNICATION | Morta approval | defer |
| `esign` | EFFECT | LEGAL | Morta approval | **defer** |
| `insurance_claim` | EFFECT | LEGAL | Morta approval | **defer** (no insurance automation) |
| `dns` | EFFECT | INFRA | Morta approval | defer |
| `oidc` | EFFECT | IDENTITY | Morta approval | defer |
| `calendar_engine` | EFFECT | SCHEDULING | Morta approval | defer |
| `tax_engine` | COMPUTE | READ | read/record | **defer** (no tax filing) |
| `kyc` | COMPUTE | IDENTITY | read/record | **defer** (no production KYC) |
| `background_check` | COMPUTE | COMPLIANCE | read/record | defer |
| `accounting` | COMPUTE | READ | read/record | defer |
| `maps_engine` | COMPUTE | READ | read/record | defer |
| `weather_engine` | COMPUTE | READ | read/record | defer |
| `cloud_storage` | COMPUTE | STORAGE | read/record | defer |
| `ocr_engine` | COMPUTE | READ | read/record | keep (document ingestion, if OCR needed) |
| `translate_engine` | COMPUTE | READ | read/record | defer |

## What 0.3 actually ships as capabilities

Per handoff §10 the daily-driver capability set is **narrow and deferral-heavy**. Almost
all 25 bundled engines are in explicitly-deferred domains (finance, insurance, tax, KYC).
The 0.3 capabilities are instead:

- **Knowledge** — notes CRUD, document import, source-grounded Q&A (`knowledge.py`,
  `corpus.py`, `doc.py`, `retrieval.py`, `search.py`, plus optional `ocr_engine`).
- **Tasks & projects** — `projects.py`, `planning.py`, task/plan Cells.
- **Development workspace** — isolated repo workspace, edit/test/diff (`workspace.py`,
  `isolation.py`), no push/deploy in 0.3.
- **Restricted filesystem** — granted-directory import/export (`files.py`).
- **Model** — structured/streaming completion, embedding, local-only policy
  (`router.py`, `provider_router.py`, `model.py`, `inference.py`).

The bundled financial/legal engines remain in the tree (handoff: "existing experiments
may remain") but must **not** be wired live or block/expand 0.3 work.
