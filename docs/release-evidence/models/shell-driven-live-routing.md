# Shell-driven live routing — P5 High fix verification

With the operator live config set (`DECIMA_LIVE_PROVIDER=local`,
`DECIMA_LIVE_MODEL=qwen3-30b-a3b`, `DECIMA_LIVE_BASE_URL=http://127.0.0.1:8080`), a real
product command `RequestPlanProposal` driven through `build_application` →
`CommandService.execute` (the exact path a Shell request takes) records
`selected_model = qwen3-30b-a3b` — **with no registry surgery** (no `set_enabled`
disabling of the deterministic entry). Before the fix, product routing recorded
`deterministic-offline` because the placeholder tied on cost and won the model-id
alphabetical tie-break, making the operator's configured provider unreachable.

Fix: the deterministic-offline placeholder carries a nominal rank cost
(`models_setup._PLACEHOLDER_RANK_COST`) so any configured real provider (local, honest
cost 0) outranks it, while it remains the sole entry when nothing is configured and the
fallback otherwise. Regression: `tests/end_to_end/test_audit_live_provider_selectable.py`
(3 tests) + `tests/api/test_models_setup.py::test_configured_live_provider_is_selected_over_the_placeholder`.
