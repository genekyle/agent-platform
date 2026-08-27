"""Every capture-server route is classified, and every ACTION journals.

THE FAILURE THIS EXISTS TO STOP (2026-08-26). A sweep opened twelve job cards, six failed, and the
event exists nowhere: not in the sweep's summary, which counted only successes, and not in the
intent journal, because `/open_job_card` fires a trusted CDP click and had never been decorated.
An unjournaled action is invisible twice — the operator cannot see it went wrong, and the corpus
never learns it happened.

The convention already existed. Eleven routes carried `@journaled` and twenty-seven did not, and
nothing anywhere could tell you which of the twenty-seven mattered. This turns that into a fact a
test can check: `app/route_classes.py` is the inventory, and a new route fails here until somebody
decides what it does.

Run from apps/mcp:  ../../.venv/bin/python -m pytest tests/test_route_classes.py -q
"""

import ast

from app.route_classes import ACTION, NO_VERB, READ_ONLY, ROUTE_CLASSES

_SOURCE = "app/main_server.py"


def _routes() -> dict[str, bool]:
    """{path: is_journaled} read off the source, so the test cannot be fooled by an import."""
    out: dict[str, bool] = {}
    for node in ast.walk(ast.parse(open(_SOURCE).read())):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        path = None
        journaled = False
        for dec in node.decorator_list:
            text = ast.unparse(dec)
            if text.startswith(("app.post(", "app.get(")) and getattr(dec, "args", None):
                path = ast.literal_eval(dec.args[0])
            if text.startswith("journaled"):
                journaled = True
        if path:
            out[path] = journaled
    return out


def test_every_route_is_classified():
    """A new route is a decision — does it touch the page? — and this fails until someone makes it.
    That is the whole mechanism: not a rule people remember, a build that stops."""
    live = set(_routes())
    declared = set(ROUTE_CLASSES)
    assert not (live - declared), (
        f"routes with no class in app/route_classes.py: {sorted(live - declared)}. "
        f"Decide whether each ACTS on the page (must be @journaled) or only READS it.")
    assert not (declared - live), (
        f"classified routes that no longer exist: {sorted(declared - live)}")


def test_every_action_route_is_journaled():
    """The enforcement point for the convention. `/open_job_card` sat outside it for a month."""
    live = _routes()
    missing = [p for p, (cls, _why) in ROUTE_CLASSES.items()
               if cls == ACTION and not live.get(p, False)]
    assert not missing, (
        f"ACTION routes without @journaled: {missing}. An action that is not journaled is "
        f"invisible to the operator AND to the corpus.")


def test_read_only_routes_do_not_pretend_to_act():
    """The reverse mistake: a journaled read fills the corpus with rows for actions that never
    happened, which is worse than a gap because it is indistinguishable from real ones."""
    live = _routes()
    liars = [p for p, (cls, _why) in ROUTE_CLASSES.items()
             if cls == READ_ONLY and live.get(p, False)]
    assert not liars, f"READ_ONLY routes carrying @journaled: {liars}"


def test_the_no_verb_hole_is_named_and_does_not_grow_silently():
    """These four ARE actions that the closed Intent vocabulary has no word for. They are pinned by
    name so the hole is countable — `interaction.contract` says a verb the system emits and the
    vocabulary cannot express is "a hole in the corpus, not a purity win", and this is that hole,
    measured. Adding verbs changes the action space a local model must learn, so it is the
    operator's call; changing this list should require saying so out loud."""
    holes = {p for p, (cls, _why) in ROUTE_CLASSES.items() if cls == NO_VERB}
    assert holes == {"/navigate", "/close_tab", "/autofill_form", "/extract_jobs"}
    for path in holes:
        assert len(ROUTE_CLASSES[path][1]) > 60, f"{path} is exempt without a real reason"
