"""Grounded Q&A service — OWNED BY THE QA LANE (Path A).

This module is the ONLY backend file the qa lane edits (besides its own screen
``js/screens/qa.js``, its tests, and qa capability glue). The shared contracts it
implements live in ``contracts.py``; the routes/commands/events are already wired:

  commands  AskGroundedQuestion                → :func:`ask_grounded_question`
  readers   GET /api/v1/questions              → :func:`list_question_runs`
            GET /api/v1/questions/detail?id=…  → :func:`get_question_run`
  events    ``question.*`` via ``svc.bus.emit`` (see ``events.QUESTION_EVENTS``)

Implementation rules (the lane's obligations):
  * Retrieval + answering compose ``decima.capabilities.qa`` (horizon-scoped, cited);
    the model only ever PROPOSES (invariant 4) and retrieved text stays
    ``instruction_eligible=False`` (invariant 5).
  * Any durable record (a question run cell) is asserted through the established
    kernel paths from inside the command handler (invariant 1); readers stay pure
    reads over the fold/projections (invariant 2).
  * Return :class:`~decima.services.api.commands.CommandResult` from the command,
    plain JSON-safe dicts (``{"items": [...]}`` / an ``as_dict``) from readers.
"""

from __future__ import annotations

from decima.services.api.contracts import NOT_IMPLEMENTED, CommandError


def ask_grounded_question(svc: object, args: dict) -> object:
    """Answer a question from imported sources with resolving citations.

    OWNER: qa lane. Parse ``args`` with ``contracts.QuestionRequest.from_args``,
    retrieve + answer via ``decima.capabilities.qa``, record the run durably, emit
    ``question.asked`` / ``question.answered``, and return a ``CommandResult`` whose
    data is a ``contracts.QuestionRun.as_dict()``."""
    raise CommandError(
        NOT_IMPLEMENTED, "AskGroundedQuestion is not implemented yet (qa lane)",
        http_status=501,
    )


def list_question_runs(app: object, query: dict) -> dict:
    """Reader: every recorded question run, newest first — ``{"items": [...]}``.

    OWNER: qa lane."""
    raise CommandError(
        NOT_IMPLEMENTED, "question runs reader is not implemented yet (qa lane)",
        http_status=501,
    )


def get_question_run(app: object, query: dict) -> dict:
    """Reader: one question run by ``?id=…`` with its full citation list.

    OWNER: qa lane. Unknown id ⇒ ``CommandError(NOT_FOUND, http_status=404)``."""
    raise CommandError(
        NOT_IMPLEMENTED, "question run detail is not implemented yet (qa lane)",
        http_status=501,
    )


# Reader dispatch (target name in routes.py → callable). The app consults this table;
# the qa lane replaces stub bodies above, never the table keys.
READERS = {
    "question_runs": list_question_runs,
    "question_run": get_question_run,
}
