## decima/capabilities/ — daily-driver workflows (Phase 10)

A NEW package composing the existing seams into three complete, narrow user workflows. Mints no authority, adds no second store: every durable write is a Cell asserted through `decima.kernel.model` onto the sole canonical Weft.

### documents.py — source-linked ingestion
`import_document(weft, author, *, source, data: bytes, title=None, project="default")` → `ImportedDocument`.
- Content-addressed identity (`document_id = hash(source, digest)`, `segment_id = hash(doc, offset, text)`) ⇒ re-importing the same bytes is idempotent.
- `classify()` → plain_text / markdown / source_code / pdf (name + `%PDF-` sniff). `extract_text()` decodes UTF-8 (binary/NUL ⇒ empty, never garbage) and runs a **bounded, pure** PDF extractor (`_extract_pdf_text`, pulls literal `(...)` strings, best-effort FlateDecode — never executes an action/script).
- `segment_text()` yields `(offset, chunk)` bounded pieces; each segment lands as a `claim` Cell carrying `source_document` + `offset` + a typed `from_source` edge (double witness), all `instruction_eligible=False` (invariant 5).
- `knowledge_projection()` / `build_index()` are disposable read-models rebuilt from the Weft (invariant 2).

### qa.py — source-grounded, horizon-scoped Q&A
`answer_question(weft, question, *, provider, horizon=None, limit=5)` → `Answer(text, model, citations, grounded)`.
- Ranks with `projections.search`, resolves provenance from the fold, then **horizon-scopes**: only segments whose `project` ∈ `horizon` survive (None = all; empty = nothing). A private project is invisible to an agent whose horizon excludes it.
- The model (a `models` provider — DeterministicProvider in tests) PROPOSES; retrieved text is passed as `instruction_eligible=False` context (invariants 4/5). No source in horizon ⇒ ungrounded answer, no fabricated citation. Each `Citation` resolves to an imported segment Cell.

### workspace.py — isolated repo workspace
`create_workspace()` → `Workspace`; `.mount_repo(files)` (path-traversal-refused), `.read_file/.edit_file/.list_files`, `.diff()` (reviewable unified diff BEFORE `.apply()`), `.produce_diff_artifact()` / `.produce_test_artifact()` (durable Weft Cells + a receipt).
- `.run_in_worker(...)` builds a real lease (`runtime.cells.create_lease`) + capability proof and dispatches a digest-bound runner into a `WORKERS` `WORKSPACE`-profile child (chroot jail, no network, no creds). The worker only sees the file bytes we pass and **cannot read any host path** — `probe_paths` come back empty. Declared checks (`check_source`) run confined, never in the API process (invariant 7).
- Durable artifacts live on the append-only log, so a restart (reopen `Weft(db, kr)`) re-folds the produced diff — it is never lost.

Line-length-100, ruff-clean, fully type-annotated, stdlib + PyNaCl only.