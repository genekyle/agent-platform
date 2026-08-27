"""Which capture-server routes ACT on the world, and which only look at it.

WHY THIS FILE EXISTS. On 2026-08-26 a sweep opened twelve job cards, six of them failed, and there
was no record of it anywhere — not in the sweep's summary, and not in the intent journal, because
`/open_job_card` fires a trusted CDP click and was never decorated with `@journaled`. The swallow in
the caller was not the first record lost; it was the **only** record there would ever have been.

An action that is not journaled is invisible twice over: the operator cannot see it went wrong, and
the corpus never learns it happened. `@journaled` is the enforcement point, and a convention that
"actions should be journaled" is worth exactly as much as the last person who remembered it — so
this is the inventory, and `tests/test_route_classes.py` fails if a route is missing from it or if
an `ACTION` route lost its decorator.

POST DOES NOT MEAN MUTATION HERE. Nearly every route is POST because it takes a request body naming
the tab; most of them only read. So the split is by what the route DOES to the page, not by verb.

THREE CLASSES, and the third one is the finding:

* `ACTION`      — dispatches input, navigates, or otherwise changes the page. MUST be `@journaled`.
* `READ_ONLY`   — evaluates JS, reads the AX tree, screenshots, or inspects tabs. Nothing to journal.
* `NO_VERB`     — really an action, and the closed `Intent` vocabulary has no word for it. Listed
                  with its reason, deliberately NOT quietly filed under READ_ONLY.

ON `NO_VERB`, because it is a decision and not an oversight. `interaction.contract.Intent` is closed
on purpose — "every verb added here is a verb L4 must distinguish" — and its own docstring says a
verb the system really emits and the vocabulary cannot express is "a hole in the corpus, not a purity
win". These four are that hole, and naming them here is what makes it countable instead of forgotten.
Adding verbs is an operator-level decision about the action space a local model must learn, so it is
NOT taken unilaterally by the session that found the hole.
"""

from __future__ import annotations

ACTION = "action"
READ_ONLY = "read_only"
NO_VERB = "no_verb"

#: Every route on the capture server, and what it does to the page. Keyed by path.
ROUTE_CLASSES: dict[str, tuple[str, str]] = {
    # --- ACTIONS: they change the page, and they journal -----------------------------------
    "/execute": (ACTION, "tier-1 driver: type, click, scroll, submit — polymorphic over action_id"),
    "/select_option": (ACTION, "opens a listbox and picks"),
    "/select_prompt": (ACTION, "opens a prompt-driven widget and picks"),
    "/select_prompt_path": (ACTION, "walks a multi-step prompt widget"),
    "/widget_select": (ACTION, "commits a value into a composite widget"),
    "/check_group": (ACTION, "clicks checkboxes/radios in a group"),
    "/set_date": (ACTION, "drives a segmented date widget"),
    "/scroll_job_list": (ACTION, "humanized wheel over the results column"),
    "/scan_required": (ACTION, "presses the form's own Next to make it state what it requires"),
    "/describe_widget": (ACTION, "focuses and interrogates a widget; may open it"),
    "/probe": (ACTION, "raw JS — DISCOVERY ONLY, and journaled precisely because it is the hole"),
    "/open_job_card": (ACTION, "trusted CDP click at a card's centre — THE 2026-08-26 case"),
    "/next_page": (ACTION, "wheels to the end of the list and clicks the pagination control"),
    "/set_distance": (ACTION, "clicks the distance filter pill and picks a radius"),
    "/dismiss_dialog": (ACTION, "clicks a dialog's dismiss control"),
    "/dismiss_native_dialog": (ACTION, "accepts/dismisses a native browser dialog"),

    # --- NO_VERB: actions the closed vocabulary cannot name ---------------------------------
    "/navigate": (NO_VERB, "drives the browser to a URL. There is no NAVIGATE intent, and that is "
                           "deliberate — PRINCIPLES §3 says reach states by CLICKING and treats "
                           "URL-forcing as last-ditch. But the route exists and is used, so the "
                           "action happens and journals nothing. Either §3's exception earns a "
                           "verb, or this route should be harder to call than it is."),
    "/close_tab": (NO_VERB, "closes a tab — a real change to the session's world (BOUNDS calls the "
                            "one-tab apply-epilogue close explicitly legitimate). No verb covers "
                            "tab lifecycle at all; neither does opening one."),
    "/autofill_form": (NO_VERB, "fills a whole form in one call. Every field it writes IS a "
                                "SET_TEXT/SELECT_OPTION, so this is a BATCH of journalable intents "
                                "reported as one opaque call — the journal would need per-field "
                                "rows to be worth anything, which is a real change, not a "
                                "decorator."),
    "/extract_jobs": (NO_VERB, "purpose is to READ the results, but on LinkedIn it wheels the list "
                               "up to 12 times to do it — so it dispatches real input while being "
                               "semantically an observation. SCROLL and OBSERVE both fit, which is "
                               "the tell that one call is doing two things."),

    # --- READ_ONLY: they look, they do not touch --------------------------------------------
    "/auth_state": (READ_ONLY, "reads the nav for a signed-in tell"),
    "/await_results": (READ_ONLY, "polls the result signature until it settles"),
    "/results_signature": (READ_ONLY, "one Runtime.evaluate for the set's fingerprint"),
    "/ax_scan": (READ_ONLY, "reads the accessibility tree"),
    "/capture": (READ_ONLY, "screenshot + artifact for the corpus"),
    "/screenshot": (READ_ONLY, "screenshot"),
    "/page_content": (READ_ONLY, "reads text"),
    "/locate": (READ_ONLY, "resolves a name to a node without pressing it"),
    "/list_tabs": (READ_ONLY, "enumerates the session's tabs"),
    "/challenge_visibility": (READ_ONLY, "is a captcha actually shown and blocking"),
    "/dialog_guard": (READ_ONLY, "is a dialog in the way"),
    "/native_dialog": (READ_ONLY, "reads a pending native dialog"),
    "/fetch_job_description": (READ_ONLY, "reads the open pane's description"),
    "/read_inbox": (READ_ONLY, "reads mail rows"),
    "/observe/start": (READ_ONLY, "starts recording; changes our state, not the page's"),
    "/observe/stop": (READ_ONLY, "stops recording"),
    "/proposer/predict": (READ_ONLY, "model inference over a captured artifact"),
    "/proposer/backfill/{artifact_filename}": (READ_ONLY, "re-runs inference over stored artifacts"),
    "/dialects": (READ_ONLY, "static: which dialects are known"),
    "/health": (READ_ONLY, "liveness"),
}
