"""TIER 2 — the widget protocols. Site-agnostic, dispatching on widget_type.

Each function here owns ONE proven multi-step widget contract: precondition → open → stage →
confirm staged → commit → confirm from outside. The caller says what it wants; the protocol
knows how the widget works. That division is the whole plan:

    The model says WHAT. The recipe says WHERE. The API says HOW.

Every protocol returns `(Outcome, steps, detail)`. `steps` is the per-step trace — it tells
you WHICH step broke, and it is exactly the intermediate-state vocabulary L3 lacks
(`popup_open`, `option_staged`), which is why the loop cannot verify its own progress
through a multi-step widget today.

Nothing in this module is new knowledge. Every rule below was paid for live on 2026-07-15
and written into `apply_recipe.GREENHOUSE_LESSONS` / `WORKDAY_LESSONS` as prose. This is that
prose becoming the single place the fix lands.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from interaction.contract import Outcome

from app.js_common import WIDGET_TELLS_JS

# --- shared page-side helpers -------------------------------------------------------

# Focus a node the way a real mousedown would. `.click()` DOES NOT FOCUS — a synthetic click
# skips it, and without focus the widget's own keyboard protocol (aria-activedescendant) is
# dead. focus() THEN click().
_FOCUS_AND_OPEN_JS = r"""
(sel) => {
  __WIDGET_TELLS__
  // FRAME-AWARE TARGET LOOKUP. A page is not its top document: iCIMS renders its whole apply flow
  // inside `icims_content_iframe`, and every layer here had to learn this separately — the act-time
  // resolver, the census, the captcha rail, the native-select protocol, and now the classifier and
  // the popup protocols. `__findAll` is the one definition (app/js_common.py).
  const el = __findAll(sel)[0] || null;
  if (!el) return {ok: false, detail: 'no node matching ' + sel};
  el.scrollIntoView({block: 'center'});
  el.focus();
  const r = el.getBoundingClientRect();
  return {ok: true, x: r.x + r.width / 2, y: r.y + r.height / 2,
          expanded: el.getAttribute('aria-expanded')};
}
"""

# Clear whatever is in the focused control, react-safely (the native setter + an input event
# — assigning .value directly leaves React's internal state stale).
_CLEAR_ACTIVE_JS = r"""
(() => {
  const el = document.activeElement;
  if (!el || !('value' in el)) return false;
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
  setter.call(el, '');
  el.dispatchEvent(new Event('input', {bubbles: true}));
  return true;
})()
"""


def _read_single_value_js(selector: str) -> str:
    """Read a react-select's TRUTH.

    After a pick the input's own `.value` goes EMPTY — the choice renders in a sibling
    [class*=singleValue]. Verifying at `.value` "confirmed" an empty field twice on
    2026-07-15. This is /describe_widget's `value_read_at` applied.
    """
    s = json.dumps(selector)
    return (
        "(() => {"
        "  " + WIDGET_TELLS_JS +
        f"  const el = __findAll({s})[0] || null;"
        "   if (!el) return null;"
        "   const wrap = el.closest('[class*=select__control], .select, [class*=field], div');"
        "   const sv = wrap && wrap.querySelector('[class*=singleValue]');"
        "   return sv ? (sv.innerText || sv.textContent || '').trim() : null;"
        "})()"
    )


def _find_option_js(value: str, scope_ref: Optional[str]) -> str:
    """The visible option matching `value` — EXACT first, then prefix. Never substring.

    "State" matched "United **State**s"; "Concord" matched "**Concord**ia, Entre Rios,
    Argentina" over "Concord, New Hampshire"; "No" would match "Yes, **no**n-compete".
    Exact-by-default with prefix as the explicit fallback is the rule those three bought.

    `scope_ref` is the popup the opener declared via aria-controls. Without it we search
    document-wide and can click ANOTHER widget's identically-named option (a Workday page had
    63 stray [role=option]s; scoping cut it to 5).
    """
    v = json.dumps((value or "").strip())
    scope = f"document.getElementById({json.dumps(scope_ref)}) || document" if scope_ref else "document"
    return (
        "(() => {"
        f"  const want = {v};"
        f"  const scope = {scope};"
        "   const vis = e => { const r = e.getBoundingClientRect();"
        "                      return e.offsetParent !== null && r.width > 0 && r.height > 0; };"
        "   const opts = [...scope.querySelectorAll('[role=option], li[role=option], "
        "                  [data-automation-id=promptOption], [class*=select__option]')].filter(vis);"
        "   const txt = o => (o.innerText || o.textContent || '').trim();"
        "   let el = opts.find(o => txt(o) === want) || opts.find(o => txt(o).startsWith(want));"
        "   if (!el) return {found: false, count: opts.length, sample: opts.slice(0, 12).map(txt)};"
        "   el.scrollIntoView({block: 'center'});"
        "   el.click();"
        "   return {found: true, text: txt(el), count: opts.length};"
        "})()"
    )


async def _eval(cdp, expression: str) -> Any:
    r = await cdp.send("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                            "awaitPromise": True})
    return (r.get("result") or {}).get("value")


async def _type_trusted(cdp, text: str, *, per_char_delay: float = 0.05) -> None:
    """TRUSTED per-char key events into the focused element.

    react-select and Workday's prompt searchBox both FETCH their options server-side on real
    keystrokes. A programmatic value-set or `Input.insertText` does NOT trigger the fetch, so
    aria-expanded stays false and no listbox ever appears. This is the same lesson twice, on
    two unrelated sites, which is why it lives in the protocol and not in a caller.
    """
    for ch in text:
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "text": ch, "key": ch,
                                                  "unmodifiedText": ch})
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch})
        await asyncio.sleep(per_char_delay)


# --- the react-select protocol ------------------------------------------------------
async def react_select_pick(cdp, *, selector: str, value: str,
                            settle_seconds: float = 1.2) -> tuple[Outcome, list[dict], str]:
    """Greenhouse's country / location / school / degree / every Yes-No custom question.

    open (per-char keystrokes) → stage (exact-match click) → confirm at singleValue.
    """
    steps: list[dict] = []

    opened = await _eval(cdp, f"({_FOCUS_AND_OPEN_JS})({json.dumps(selector)})") or {}
    steps.append({"step": "precheck", "found": bool(opened.get("ok"))})
    if not opened.get("ok"):
        return Outcome.NOT_FOUND, steps, opened.get("detail") or f"no node matching {selector!r}"

    # A real mousedown focuses; the synthetic one doesn't. Click at the measured centre to
    # focus, then type — the typing is what opens it, not the click.
    for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
        ev = {"type": typ, "x": opened["x"], "y": opened["y"]}
        if typ != "mouseMoved":
            ev.update({"button": "left", "clickCount": 1})
        await cdp.send("Input.dispatchMouseEvent", ev)
    await asyncio.sleep(0.15)
    await _eval(cdp, _CLEAR_ACTIVE_JS)
    await _type_trusted(cdp, value)
    await asyncio.sleep(settle_seconds)   # the debounced fetch

    # aria-controls is ABSENT until it expands, so resolve the popup AFTER typing — never
    # before. Reading it early is how "already open" got decided off another widget's strays.
    ref = await _eval(cdp, f"(() => {{ const e = document.querySelector({json.dumps(selector)});"
                           " return e && e.getAttribute('aria-controls'); }})()")
    steps.append({"step": "open", "popup_ref": ref, "scoped": bool(ref)})

    hit = await _eval(cdp, _find_option_js(value, ref)) or {}
    steps.append({"step": "select", "found": bool(hit.get("found")),
                  "n_options": hit.get("count"), "picked": hit.get("text")})
    if not hit.get("found"):
        if not hit.get("count"):
            # Nothing rendered at all: the widget never opened. Distinct from "opened, but
            # your word isn't in the list" — different caller move, so different outcome.
            return (Outcome.NOT_OPENED, steps,
                    f"no options appeared after typing {value!r} — react-select opens on real "
                    f"keystrokes; if this recurs the widget_type is wrong")
        return (Outcome.NO_OPTION, steps,
                f"no option matching {value!r} among {hit.get('count')} — "
                f"sample: {hit.get('sample')}. Vocabulary miss -> /resolve_answer.")

    # CONFIRM AT THE LAYER THAT COMMITS. .value is empty by now and would report failure;
    # singleValue is where the choice actually lives.
    await asyncio.sleep(0.35)
    got = await _eval(cdp, _read_single_value_js(selector))
    steps.append({"step": "commit", "kind": "on_select", "value_read_at": "[class*=singleValue]",
                  "observed": got})
    if not got:
        return (Outcome.NOT_STAGED, steps,
                f"clicked {value!r} but singleValue is empty — the pick did not take")
    if value.strip().lower() not in str(got).strip().lower():
        return (Outcome.NOT_STAGED, steps,
                f"clicked {value!r} but singleValue reads {got!r} — wrong option took")
    return Outcome.OK, steps, f"selected {got!r} (verified at singleValue)"


# --- the text-menu protocol ---------------------------------------------------------
#
# A MENU WITH NO ROLES AT ALL. Measured live on WAHVE (`insurance.brainwahve.com`, 2026-08-17),
# which is React 15 + Material-UI v0: the opener is `div.dropdown[data-id=…][tabindex=0]`, the
# items are bare `<div>`s inside a `<span tabindex="0">`, and there is no `<select>`, no
# `role=listbox|option|menu`, no `aria-*` and no shadow root anywhere in the document.
#
# WHY THE OTHER THREE CANNOT REACH IT, each for its own reason — this is the interaction-layer
# question (§6) answered by measurement rather than by preference:
#   native_select  structurally impossible; the tag is a div.
#   aria_listbox   opens it correctly and then cannot SEE the options — it looks for
#                  `[role=option]` / `li`, and these carry neither. It reported `not_opened`
#                  about a menu that was plainly open on the screenshot, which is the
#                  interesting half: absence of a selector match was being reported as absence
#                  of a popup. Two different claims.
#   react_select   types to open; this widget opens on tap and ignores keystrokes.
#
# AND THE COMMIT IS A TAP, NOT A CLICK. Material-UI v0 rides react-tap-event-plugin, which
# synthesises its tap from real mousedown+mouseup. A JS `.click()` (and anything that resolves a
# node and calls click on it) fires nothing — measured: the option highlighted, the menu stayed
# open, and the opener's label never changed. So both the open and the pick are TRUSTED mouse
# events at measured coordinates, which is also why this protocol addresses by bbox: the node id
# is useless when the handler is listening for a physical gesture.
_TEXT_MENU_OPEN_JS = r"""
(selector) => {
  const el = document.querySelector(selector);
  if (!el) return {ok: false, detail: `no node matching ${selector}`};
  el.scrollIntoView({block: "center", inline: "nearest"});
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height) return {ok: false, detail: "opener has no box (hidden?)"};
  // The opener's own label is the value_read_at for this family — read it BEFORE opening so a
  // commit can be judged as a CHANGE rather than as a string that happened to be there.
  const lbl = el.querySelector(".dropdown-label, [class*=label]");
  return {ok: true, x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
          before: ((lbl || el).textContent || "").trim()};
}
"""

#: Find the option by its exact visible TEXT, then hand back the tap target's centre.
#:
#: Scoped by EXCLUSION rather than by a popup ref: these menus have no `aria-controls` and no
#: stable container class, so the honest rule is "a visible leaf carrying this text that is not
#: inside the opener". The opener has to be excluded explicitly — it renders the chosen value in
#: exactly the same words, so a committed menu would otherwise match itself and report success
#: without ever opening.
_TEXT_MENU_PICK_JS = r"""
(cfg) => {
  const want = String(cfg.value).trim().toLowerCase();
  const opener = document.querySelector(cfg.selector);
  const vis = (e) => e.offsetParent !== null && e.getBoundingClientRect().width > 0;
  const leaves = [...document.querySelectorAll("body *")].filter(
    (e) => e.children.length === 0 && vis(e) && (e.textContent || "").trim());
  // AN OPTION IS A TAP TARGET, NOT MERELY VISIBLE TEXT. The page behind an open menu is still
  // visible, so filtering on visibility alone counted every heading and paragraph on the form as
  // a candidate — 92 "options" on a menu of eight, and a `no_option` refusal whose sample was the
  // page's prose instead of the choices. Since the commit is a tap, the honest definition of an
  // option is "a visible leaf inside something focusable", which is what a menu item is and what
  // body copy never is.
  const focusableAncestor = (e) => {
    let a = e;
    for (let i = 0; i < 4 && a && a.parentElement; i++) {
      a = a.parentElement;
      if (a.hasAttribute && a.hasAttribute("tabindex")) return a;
    }
    return null;
  };
  const outside = leaves.filter(
    (e) => !(opener && opener.contains(e)) && focusableAncestor(e));
  const texts = [...new Set(outside.map((e) => (e.textContent || "").trim()))];
  const hits = outside.filter((e) => (e.textContent || "").trim().toLowerCase() === want);
  if (!hits.length) return {found: false, count: outside.length, sample: texts.slice(0, 40)};
  // THE TAP TARGET IS NOT THE TEXT NODE'S ELEMENT. MUI v0 wraps each item in an EnhancedButton
  // (`span[tabindex]`) which is what carries the handler; clicking the inner div works only
  // because the event bubbles, and bubbling is not something to rely on when a library may stop
  // propagation. Walk up to the nearest focusable ancestor, bounded, and fall back to the leaf.
  const tapTarget = (e) => {
    let a = e;
    for (let i = 0; i < 4 && a && a.parentElement; i++) {
      a = a.parentElement;
      if (a.hasAttribute && a.hasAttribute("tabindex")) return a;
    }
    return e;
  };
  // SCROLL IT INTO THE MENU'S VIEW BEFORE MEASURING. A tap is delivered at viewport coordinates,
  // so an option below the fold of a scrollable menu measures to a box that is not where it will
  // be tapped — the click lands on whatever row happens to sit there. Measured on WAHVE's State
  // list (50 options): asking for "New Hampshire" tapped the row showing "Alaska". `nearest`
  // rather than `center` so a list that already has the option on screen does not jump under the
  // cursor for no reason.
  //
  // SCROLLING AND MEASURING ARE TWO PASSES, and the gap between them is the whole point. Reading
  // the rect in the same turn as the scroll returns the position the row is LEAVING, so every tap
  // landed one row off — on the checkbox menu that presented as "the value I asked to remove went
  // away and its neighbour came on", which on a real employer's form is worse than doing nothing.
  // `cfg.measureOnly` is the second pass, after the caller has let the scroll settle.
  const boxes = hits.map((h) => { const t = tapTarget(h);
    if (!cfg.measureOnly) t.scrollIntoView({block: "nearest", inline: "nearest"});
    const r = t.getBoundingClientRect();
    return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
            w: Math.round(r.width), h: Math.round(r.height)}; });
  // Distinct tap targets with the same words: refuse rather than pick. Same rule as every other
  // ambiguity in this codebase — a loose match is a guess wearing a structural costume.
  const distinct = [...new Set(boxes.map((b) => `${b.x},${b.y}`))];
  if (distinct.length > 1) return {found: false, ambiguous: distinct.length, sample: texts.slice(0, 12)};
  return {found: true, count: outside.length, ...boxes[0]};
}
"""


def _text_menu_read_js(selector: str) -> str:
    return (f"(() => {{ const e = document.querySelector({json.dumps(selector)});"
            " if (!e) return null;"
            " const l = e.querySelector('.dropdown-label, [class*=label]');"
            " return ((l || e).textContent || '').trim(); })()")


async def text_menu_pick(cdp, *, selector: str, value: str,
                         settle_seconds: float = 0.5) -> tuple[Outcome, list[dict], str]:
    """A role-less, tap-driven menu: open by trusted click → pick by TEXT → confirm at the opener.

    tap open → tap the option → confirm the opener's own label changed to the value.
    """
    steps: list[dict] = []

    async def _tap(x: int, y: int) -> None:
        for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
            ev = {"type": typ, "x": x, "y": y}
            if typ != "mouseMoved":
                ev.update({"button": "left", "clickCount": 1})
            await cdp.send("Input.dispatchMouseEvent", ev)

    opened = await _eval(cdp, f"({_TEXT_MENU_OPEN_JS})({json.dumps(selector)})") or {}
    steps.append({"step": "precheck", "found": bool(opened.get("ok")),
                  "before": opened.get("before")})
    if not opened.get("ok"):
        return Outcome.NOT_FOUND, steps, opened.get("detail") or f"no node matching {selector!r}"

    await _tap(opened["x"], opened["y"])
    await asyncio.sleep(settle_seconds)

    cfg = json.dumps({"selector": selector, "value": value})
    hit = await _eval(cdp, f"({_TEXT_MENU_PICK_JS})({cfg})") or {}
    if hit.get("found"):
        # Let the scroll the first pass requested actually happen, then re-measure where the row
        # has come to rest. Same session, so the popover is never blurred (see the module note).
        await asyncio.sleep(0.25)
        cfg2 = json.dumps({"selector": selector, "value": value, "measureOnly": True})
        settled = await _eval(cdp, f"({_TEXT_MENU_PICK_JS})({cfg2})") or {}
        if settled.get("found"):
            hit = settled
    steps.append({"step": "open", "n_visible": hit.get("count"), "found": bool(hit.get("found"))})
    if hit.get("ambiguous"):
        return (Outcome.AMBIGUOUS, steps,
                f"{value!r} matches {hit['ambiguous']} separate tap targets — refusing to guess")
    if not hit.get("found"):
        # NOTHING RENDERED vs YOUR WORD ISN'T THERE — different caller moves, so different
        # outcomes, and the distinction the aria_listbox attempt got wrong on this very widget.
        if not hit.get("count"):
            return Outcome.NOT_OPENED, steps, "tapping the opener rendered nothing"
        return (Outcome.NO_OPTION, steps,
                f"no option matching {value!r} among {hit.get('count')} visible — "
                f"sample: {hit.get('sample')}")

    await _tap(hit["x"], hit["y"])
    await asyncio.sleep(0.35)

    got = await _eval(cdp, _text_menu_read_js(selector))
    steps.append({"step": "commit", "kind": "on_select", "value_read_at": ".dropdown-label",
                  "observed": got})
    # MEMBERSHIP, NOT EQUALITY — because "select the type(s)" is a real question and this family
    # answers it. WAHVE's insurance-firm question is a checkbox menu whose opener reads
    # "Credit Union, Other, Direct Writer / Captive Insurance Carrier"; comparing that whole label
    # to "Other" called a correct selection NOT_STAGED. A false negative here is not harmless: the
    # caller's reasonable next move is to try again, and on a multi-select trying again TOGGLES
    # THE VALUE BACK OFF, so the retry undoes the success it was sent to confirm.
    #
    # Still compares against the VALUE and not merely against `before` — a menu that closed on the
    # wrong item also changes the label, and "it moved" is not "it is right" (2026-08-15).
    chosen = [p.strip().lower() for p in str(got or "").split(",") if p.strip()]
    if value.strip().lower() not in chosen:
        return (Outcome.NOT_STAGED, steps,
                f"tapped {value!r} but the opener reads {got!r}"
                + (" (unchanged)" if str(got or "") == str(opened.get("before") or "") else ""))
    return (Outcome.OK, steps,
            f"selected {got!r} (verified at the opener's own label)"
            if len(chosen) == 1
            else f"selected {value!r}; the opener now reads {got!r} (multi-select)")


# --- the checkbox-group protocol ----------------------------------------------------
# Greenhouse renders required checkbox groups as question_<id>[]_<optid>. Group by the id
# prefix BEFORE '[]'. A scan that only looks at inputs/selects misses them entirely — we
# missed `restrictions` and `languages` on the first pass, both required. 0 checked =
# unanswered. Match labels EXACTLY: "No" must not match "Yes, non-compete".
CHECK_GROUP_JS = r"""
(cfg) => {
  __WIDGET_TELLS__
  const log = [];
  const vis = e => { try { const r = e.getBoundingClientRect();
                           return e.offsetParent !== null && r.width > 0 && r.height > 0; }
                     catch (x) { return false; } };
  const txt = e => ((e && (e.innerText || e.textContent)) || '').replace(/\s+/g, ' ').trim();

  // A CHECKBOX'S NAME IS OFTEN NOT INSIDE IT. This read the ANCESTOR <label> and then fell
  // straight through to `.value` — and Workday associates its labels the standard HTML way,
  // `<label for=id>` as a SIBLING. So every certification on Eversource's Application Questions
  // 2 of 2 came back named "on", which is the HTML default value for a checkbox that has none:
  // twelve distinct options, one meaningless name, and `values:["None"]` answered
  // `no option(s) ["None"] — options: ["on"]` (live 2026-08-17). Resolved the way a screen reader
  // would, most-specific first, and scoped to the box's OWN document so this keeps working inside
  // the iframes `__findAll` exists for.
  const labelFor = (c) => {
    if (!c.id) return '';
    const d = c.ownerDocument || document;
    try {
      const esc = (window.CSS && CSS.escape) ? CSS.escape(c.id) : c.id;
      return txt(d.querySelector('label[for="' + esc + '"]'));
    } catch (x) { return ''; }
  };
  const labelledBy = (c) => {
    const d = c.ownerDocument || document;
    return (c.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
             .map(i => txt(d.getElementById(i))).join(' ').trim();
  };
  //: `value="on"` is what the browser supplies when the author gave none — it identifies nothing
  //: and collides with every sibling that shares it, so it is not a name (same rule as the
  //: census's `__isBoilerplate`). Refusing it here is what makes the "no option" report honest.
  const ownValue = (c) => (String(c.value || '').toLowerCase() === 'on' ? '' : c.value || '');
  const label = c => txt(c.closest('label')) || labelFor(c) || labelledBy(c)
                     || c.getAttribute('aria-label') || ownValue(c) || '';

  // FRAME-AWARE TARGET LOOKUP. A page is not its top document: iCIMS renders its whole apply flow
  // inside `icims_content_iframe`, and every layer here had to learn this separately — the act-time
  // resolver, the census, the captcha rail, the native-select protocol, and now the classifier and
  // the popup protocols. `__findAll` is the one definition (app/js_common.py).
  const anchor = __findAll(cfg.selector)[0] || null;
  if (!anchor) return {ok: false, code: 'not_found', detail: 'no node matching ' + cfg.selector};
  const wrap = anchor.closest('fieldset, [role=group], [class*=field], li, div') || anchor.parentElement;
  const boxes = [...wrap.querySelectorAll('input[type=checkbox]')].filter(vis);
  if (!boxes.length) return {ok: false, code: 'not_found', detail: 'no visible checkboxes in the group'};

  const key = c => (c.id || c.name || '').split('[')[0];
  const groups = {};
  for (const b of boxes) (groups[key(b)] = groups[key(b)] || []).push(b);
  const keys = Object.keys(groups);
  if (keys.length > 1)
    return {ok: false, code: 'ambiguous', detail: 'selector spans ' + keys.length +
            ' checkbox groups (' + keys.join(', ') + ') — address one group',
            groups: keys};
  const group = groups[keys[0]];
  log.push({step: 'precheck', group: keys[0], n_boxes: group.length,
            already_checked: group.filter(b => b.checked).map(label)});

  // values ["*"] = EVERY box in this group ends CHECKED — the label-independent contract a
  // required consent needs: the box's text is TENANT PROSE ("I consent" on SolutionHealth,
  // "I confirm that I have read and acknowledge" on US Bank), and a caller that must quote it
  // exactly breaks per tenant. Converges like everything else here: an already-checked box is
  // left alone, so re-entering the leg never toggles a recorded consent off.
  const wantAll = cfg.values.length === 1 && cfg.values[0] === '*';
  const available = group.map(label);
  if (!wantAll) {
    const missing = cfg.values.filter(v => !available.some(a => a === v));
    if (missing.length)
      return {ok: false, code: 'no_option', detail: 'no option(s) ' + JSON.stringify(missing),
              options: available, log: log};
  }

  // Exact match only. Toggle by CLICK (not .checked = true) so React sees the event.
  const changed = [];
  for (const b of group) {
    const want = wantAll || cfg.values.includes(label(b));
    if (b.checked !== want) { b.scrollIntoView({block: 'center'}); b.click(); changed.push(label(b)); }
  }
  log.push({step: 'select', changed: changed});

  // CONFIRM by re-reading the DOM, not by trusting the clicks.
  const now = group.filter(b => b.checked).map(label);
  const okSet = wantAll ? now.length === group.length
                        : cfg.values.length === now.length && cfg.values.every(v => now.includes(v));
  log.push({step: 'commit', kind: 'on_select', value_read_at: 'checked', observed: now});
  if (!okSet)
    return {ok: false, code: 'not_staged', detail: 'wanted ' + JSON.stringify(cfg.values) +
            ' but the group now reads ' + JSON.stringify(now), log: log};
  return {ok: true, code: 'ok', detail: 'checked ' + JSON.stringify(now) + ' (verified)',
          log: log, checked: now};
}
"""


# --- the required-field scan --------------------------------------------------------
# Replaced /scan_form (deleted 2026-07-16), which labelled every control with its CONTAINER's
# text — on Workday First/Middle/Last were indistinguishable; on Greenhouse each of 14 language
# checkboxes became its own required field. Measured against KKR's live form: /scan_form said 21
# fields / 18 "required and unfilled" (which would have made form_complete_gate permanently
# un-passable) while never finding ~16 real required fields; this said 1, which was the truth.
#
# The two rules that make this one honest where that one wasn't:
#   1. `disabled` BEATS the label asterisk and a stale aria-required. KKR's End date keeps
#      both after 'Current role' is ticked; the input is disabled => not required. Conversely
#      "If yes, provide details" keeps aria-required='true' when the parent is No and DOES
#      still need filling ('N/A' is the form's own stated convention).
#   2. CHECKBOX GROUPS COUNT. The scan that missed `restrictions` and `languages` only looked
#      at inputs/selects. Group by id prefix before '[]'; 0-checked = unanswered.
SCAN_REQUIRED_JS = r"""
(doc) => {
  __WIDGET_TELLS__
  const vis = __vis, txt = __txt, attr = __attr;

  // An id -> a USABLE '#id' selector. React 18's useId emits ids like ':r16:', and a raw
  // '#rich-text-question-input-:r16:' makes querySelector THROW SyntaxError — so the field is
  // unaddressable even though getElementById finds it fine. Caught live 2026-07-19 on Indeed's
  // free-text question, which stalled the drive at NOT_FOUND. labelFor below already escaped
  // for exactly this reason; the selector we hand OUT has to as well.
  const __idSel = (el) => {
    if (!el || !el.id) return null;
    try { return '#' + CSS.escape(el.id); } catch (e) { return null; }
  };

  // Per-control label, NOT the container's whole text — that is exactly what makes
  // /scan_form useless on Workday (First/Middle/Last all report the fieldset's text).
  const labelFor = (el) => {
    if (el.id) { const l = doc.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                 if (l) return txt(l); }
    const al = attr(el, 'aria-label'); if (al) return al;
    const ref = attr(el, 'aria-labelledby');
    if (ref) { const n = doc.getElementById(ref); if (n) return txt(n); }
    const own = el.closest('label'); if (own) return txt(own);
    const aid = attr(el, 'data-automation-id'); if (aid) return aid;
    const ph = attr(el, 'placeholder'); if (ph) return ph;
    // PROXIMITY BEFORE THE WRAP, because nearest wins. Cornerstone's contact inputs carry NO
    // id, name, association, aria or placeholder — the visible "First Name*" is an
    // unassociated <label> a couple of hops up-and-before the input (live 2026-08-11). The
    // wrap branch below reaches FARTHER (closest container, then its first label anywhere),
    // so with it first these three inputs all read as the section heading — one name for
    // three fields, which the dedup then collapsed into a single unaddressable aggregate.
    // The nearest PRECEDING short text (a label reads short; a paragraph does not) names a
    // box the way a sighted user does — by looking just above it.
    let hop = el;
    for (let i = 0; i < 4 && hop; i++) {
      let sib = hop.previousElementSibling;
      while (sib) {
        const t = txt(sib);
        if (t && t.length >= 2 && t.length <= 60) return t;
        if (t && t.length > 60) break;   // ran into prose — a paragraph is not a label
        sib = sib.previousElementSibling;
      }
      hop = hop.parentElement;
    }
    const wrap = el.closest('[class*=field], fieldset, [role=group], li');
    const lbl = wrap && wrap.querySelector('label, legend');
    return lbl ? txt(lbl) : '(unlabeled)';
  };

  // A structural CSS path for a node with no usable id — :nth-child steps up to the nearest
  // id-anchored ancestor (or body). Anonymous inputs are otherwise visible to the census but
  // unaddressable by the teach seam, which is how "the census catches it" turns into "and
  // nobody can act on it".
  const __cssPath = (el) => {
    try {
      const steps = [];
      let n = el;
      while (n && n !== doc.body) {
        const anchor = __idSel(n);
        if (anchor) { steps.unshift(anchor); break; }
        const p = n.parentElement;
        if (!p) break;
        const idx = [...p.children].indexOf(n) + 1;
        steps.unshift(n.tagName.toLowerCase() + ':nth-child(' + idx + ')');
        n = p;
      }
      if (!steps.length) return null;
      if (!steps[0].startsWith('#')) steps.unshift('body');
      const sel = steps.join(' > ');
      return doc.querySelector(sel) === el ? sel : null;   // hand out only what resolves
    } catch (e) { return null; }
  };

  // __isUserField, not __vis: a required control the user cannot TAB to is a validation
  // proxy, not a question. react-select mounts exactly such a proxy (opacity:0, tabIndex=-1)
  // which a rect-based visibility check happily reports as a phantom required field.
  // `[aria-haspopup=listbox]` catches the OPTION WIDGET THAT IS A BARE BUTTON. Workday renders
  // its State and Degree pickers as <button> with no role, no automation-id and no
  // aria-required — invisible to every clause above, so the census read a form COMPLETE while
  // the page refused Continue over it (live 2026-08-11). aria-haspopup is the ARIA contract for
  // "this control opens a popup", which is precisely what an option widget is; it is the
  // vendor-neutral tell, not a Workday special case. Requiredness is then read the usual way —
  // and Workday states it in the accessible name ("Degree Select One Required"), which the
  // label-asterisk rule below is widened to accept.
  const singles = [...doc.querySelectorAll(
    'input:not([type=checkbox]):not([type=radio]):not([type=hidden]), select, textarea, ' +
    '[role=combobox], [aria-haspopup=listbox]'
  )].filter(__isUserField);

  const out = [];
  // The SATISFIED required fields, kept rather than dropped. The gate's list (`unanswered`)
  // only ever showed what still blocks — so the cockpit could not render the form as it stands,
  // and an ANSWERED-but-wrong field was invisible by construction. The live case that demands
  // this: smartapply's "Are you an Active Employee?" standing on a blind-answered Yes, one
  // Continue away from a self-withdrawal (2026-08-10). Same walk, same truth reads — the only
  // change is that satisfaction files a row instead of vanishing.
  const done = [];
  // A native <select>'s choices are ON the page — hand them over so a surface offering "pick
  // one" does not have to probe for what the DOM already says. (React listboxes render options
  // only while open; they stay un-enumerated here and honest about it.)
  // A CAPPED LIST THAT DOES NOT SAY IT IS CAPPED IS READ AS THE WHOLE LIST. Measured live
  // 2026-08-14 on Boston Children's: a ~250-entry Country select reported 24 options whose only
  // "United" entry was United Arab Emirates, so the planner's stored answer ("United States")
  // matched nothing and Country silently stayed unanswered on a form one screen from Submit.
  // The cap itself is right — nobody needs 250 strings in a census payload — but a caller has to
  // be able to tell "not an option" from "not shown", which is the same distinction as a probe
  // that found nothing versus one that found "no". `option_count` is the page's own total.
  const OPTION_CAP = 24;
  const selectOptions = (el) => {
    if ((el.tagName || '').toLowerCase() !== 'select') return undefined;
    return [...el.options].map(o => txt(o).slice(0, 60)).filter(Boolean).slice(0, OPTION_CAP);
  };
  const optionMeta = (el) => {
    if ((el.tagName || '').toLowerCase() !== 'select') return {};
    const total = [...el.options].map(o => txt(o)).filter(Boolean).length;
    return {option_count: total, options_truncated: total > OPTION_CAP};
  };
  const seen = new Set();
  // Optional-but-visible controls, filed for ADDRESSING, never for the gate. The address book
  // used to be the required census alone, so an optional field simply had no address — the
  // teach seam answered "cannot address field 'Country'" over a form whose Country dropdown
  // stood on "Please Select" with the value known (live 2026-08-11, Cornerstone). A field the
  // operator can see is a field the teacher must be able to act on; requiredness gates the
  // SUBMIT, not the reach.
  const optional = [];
  for (const el of singles) {
    const disabled = !!(el.disabled || attr(el, 'aria-disabled') === 'true' || el.readOnly);
    const label = labelFor(el);
    let required = false, via = 'none';
    if (disabled) { required = false; via = 'disabled'; }          // rule 1 — short-circuits
    else if (el.required) { required = true; via = 'required-attr'; }
    else if (attr(el, 'aria-required') === 'true') { required = true; via = 'aria-required'; }
    else if (/\*/.test(label)) { required = true; via = 'label-asterisk'; }
    // Some widgets state requiredness in WORDS rather than a star, in the accessible name the
    // screen reader would announce: Workday's "Degree Select One Required". Anchored to the end
    // so a field merely ABOUT requirements ("Required certifications") is not swept in.
    else if (/\brequired\s*$/i.test(label)) { required = true; via = 'label-required'; }
    if (!required) {
      if (via !== 'disabled' && label && label !== '(unlabeled)' && optional.length < 40) {
        const t0 = __valueTruth(el);
        if ((el.type || '').toLowerCase() === 'password') t0.preview = t0.answered ? '••••' : '';
        const q0 = __questionOf(el);
        // The SAME naming rules as the required rows below — "regardless of whether it's required or
        // not" (operator, 2026-08-12). Applying them only to required rows is how a conditional
        // follow-up to the work-authorization question filed as " Select One" and then, once
        // answered, as its own answer. An optional field nobody can name is an optional field
        // nobody can answer, and this one was a work-eligibility question.
        const __n0 = (s) => (s || '').toLowerCase().replace(/\s*required\s*$/i, '')
                                     .replace(/[^a-z0-9]+/g, ' ').trim();
        const label0 = ((__isBoilerplate(label) ||
                         (!!t0.preview && __n0(label) === __n0(t0.preview))) && q0.question)
                       ? q0.question : label;
        optional.push({field: label0.slice(0, 90), selector: __idSel(el) || __cssPath(el),
                       within: (q0.section || '').slice(0, 90), question_source: q0.source,
                       kind: __isReactSelect(el) ? 'react_select' : el.tagName.toLowerCase(),
                       required_via: 'none', value_read_at: t0.read_at,
                       answered: t0.answered, valid: true, value_preview: t0.preview,
                       options: selectOptions(el), ...optionMeta(el)});
      }
      continue;
    }

    // Answered? Ask the SHARED tell where this widget's truth lives — never guess .value.
    // This used to detect a react-select by the presence of [class*=singleValue], which only
    // MOUNTS once the widget is answered: so every react-select this scan returned (i.e. the
    // unanswered ones, i.e. all of them) fell through to `.value`. Latent danger, not just a
    // wrong label — react-select's .value holds transient search text, so a half-typed field
    // would read ANSWERED, drop out of this list, and let form_complete_gate pass an
    // incomplete form. Verified live on KKR 2026-07-16. See app/js_common.py.
    const truth = __valueTruth(el);
    // A password's value never leaves the page, answered or not (§4). The old shape leaked
    // nothing only by accident — satisfied fields were dropped entirely; now that they file
    // a row, the mask has to be explicit.
    if ((el.type || '').toLowerCase() === 'password') truth.preview = truth.answered ? '••••' : '';
    const invalid = __invalid(el);
    const id = el.id || label;
    if (seen.has(id)) continue;          // a hidden required TWIN is not a second question
    seen.add(id);
    // The correlation, on every row: which question this control answers as the PAGE names it,
    // and how strong that identity is. `field` above is our best name for the question; `within`
    // and `question_source` are what let a caller (or a human) tell two same-named controls apart
    // and judge how much to trust the match. Required or not is a separate axis entirely — an
    // optional control filled with the wrong answer is the same error as a required one.
    const q = __questionOf(el);
    // A NAME THAT IS THE WIDGET'S PLACEHOLDER IDENTIFIES NOTHING. Workday names every option
    // dropdown "Select One Required", so three distinct required questions filed as three rows
    // called " Select One Required" — indistinguishable, and therefore unaddressable and
    // unanswerable (live 2026-08-12). Same rule as the uploader's "Drop files here": when our label
    // chain lands on boilerplate, the question is what the FIELD asks, which __questionOf reads.
    //
    // AND A NAME THAT IS THE CONTROL'S OWN ANSWER IS NOT A QUESTION EITHER. An option opener's
    // accessible name becomes the chosen value once it is answered, so the three questions above
    // filed as "No Required", "Yes Required", "No Required" the moment they were answered — leaving
    // the ANSWERED census unable to say which question got which answer, which is exactly what the
    // operator reads at the Submit gate. Name == value is the tell, and it is self-evidently not an
    // identity: no page labels a field with its own contents.
    const __norm = (s) => (s || '').toLowerCase().replace(/\s*required\s*$/i, '')
                                   .replace(/[^a-z0-9]+/g, ' ').trim();
    const nameIsValue = !!truth.preview && __norm(label) === __norm(truth.preview);
    const named = ((__isBoilerplate(label) || nameIsValue) && q.question) ? q.question : label;
    const row = {field: named.slice(0, 90), selector: __idSel(el) || __cssPath(el),
                 within: (q.section || '').slice(0, 90), question_source: q.source,
                 kind: __isReactSelect(el) ? 'react_select' : el.tagName.toLowerCase(),
                 required_via: via, value_read_at: truth.read_at,
                 // `answered` is NOT always false on the unanswered list: a FILLED-but-INVALID
                 // field is reported there too, and the gate needs the distinction
                 // (reason "invalid" vs "empty").
                 answered: truth.answered, valid: !invalid, value_preview: truth.preview,
                 options: selectOptions(el), ...optionMeta(el)};
    (truth.answered && !invalid ? done : out).push(row);   // satisfied files, never vanishes
  }

  // A GROUP's question container is, BY CONSTRUCTION, the lowest common ancestor of its
  // members — not `closest('…, div')`, which lands on ONE option's own row div and reads
  // "True" where the question (and its required asterisk) lives two levels up. Third
  // encounter with the closest-div trap (react-select truth, ground-truth probe, now this):
  // smartapply's radios carry NO required attribute at all — the asterisk in the question
  // text is the only signal, so reading the wrong container silently drops the whole group.
  // Found live: scan said 0 while three required radio questions sat unanswered.
  const lca = (group) => {
    let node = group[0];
    while (node && node !== doc.body && !group.every(m => node.contains(m)))
      node = node.parentElement;
    return node || group[0].parentElement;
  };
  const groupLabel = (group, fallback) => {
    // The LCA alone is NOT the question container: measured live on smartapply, the radio
    // pair's LCA text is exactly "True False" — the question ("Are you able to commute…*")
    // is a SIBLING of the options' wrapper, a level or two up. So climb from the LCA to the
    // LOWEST ancestor whose text reads like a question (carries '?' or '*', or is an
    // explicit question container). Stopping at the first hit keeps us from swallowing the
    // whole form (every higher ancestor also contains '?').
    // Text only — no class shortcut. The first attempt also accepted
    // `[class*=question]` containers, and smartapply's options wrapper is itself
    // class=single-select-question-*, so the shortcut matched the very node whose text is
    // "True False" and ended the climb one level short. A structural tell that can name the
    // options box is no tell; the question text ('?' or the required '*') is the signal.
    let node = lca(group);
    for (let i = 0; i < 5 && node && node !== doc.body; i++) {
      // Detect on the FULL text, cap only what we return. The cap used to run first, so a long
      // question's trailing required-'*' (CRCH's family-members question: ~280 chars, the star
      // at the very end) was amputated before the test — the group read as optional, vanished
      // from the census, and the page's own "Choose an option to continue." was the only thing
      // left telling the truth (live, 2026-08-10).
      const full = txt(node);
      if (/[?*]/.test(full))
        return full.slice(0, 160) || fallback;
      node = node.parentElement;
    }
    return txt(lca(group)).slice(0, 160) || fallback;
  };

  // Whether the group's QUESTION (the same climbed container) marks itself required — tested on
  // the uncapped text, because the star on a long question lives past any display cap.
  const groupStarred = (group) => {
    let node = lca(group);
    for (let i = 0; i < 5 && node && node !== doc.body; i++) {
      const full = txt(node);
      if (/[?*]/.test(full))
        return /\*/.test(full);
      node = node.parentElement;
    }
    return /\*/.test(txt(lca(group)));
  };

  // A group MEMBER's own label — the option's word ("True", "Job Board"), not the question's.
  // The question container's text is the field; each input's label is the choice. These are what
  // a surface renders as the pressable answers, and what check_group's `values` expects.
  const optLabel = (el) => {
    if (el.id) { const l = doc.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                 if (l) return txt(l).slice(0, 60); }
    const own = el.closest('label'); if (own) return txt(own).slice(0, 60);
    return (attr(el, 'value') || '').slice(0, 60);
  };
  const optLabels = (group) => group.map(optLabel).filter(Boolean).slice(0, 16);

  // rule 2 — checkbox groups, which the old scan missed entirely.
  const boxes = [...doc.querySelectorAll('input[type=checkbox]')].filter(vis);
  const groups = {};
  let synth = 0;
  for (const b of boxes) {
    let k = (b.id || b.name || '').split('[')[0];
    // A LONE checkbox with no id/name used to be skipped here — which silently dropped the
    // smartapply certification ("I have read and accept the above acknowledgement *"): scan said
    // 0-unanswered while Continue was blocked with "Choose an option to continue" (live 2026-07-18).
    // A checkbox with no group identity is its own required question, a group of one.
    if (!k) k = '__lone_checkbox_' + (synth++);
    (groups[k] = groups[k] || []).push(b);
  }
  for (const [k, group] of Object.entries(groups)) {
    const label = groupLabel(group, k);
    const anyDisabled = group.every(b => b.disabled);
    const req = !anyDisabled &&
                (group.some(b => b.required || attr(b, 'aria-required') === 'true')
                 || groupStarred(group));
    if (!req) continue;
    const checked = group.filter(b => b.checked);
    const row = {field: label.slice(0, 90), selector: __idSel(group[0]),
                 kind: 'checkbox_group', required_via: 'group', value_read_at: 'checked',
                 answered: checked.length > 0, valid: true,
                 value_preview: checked.map(optLabel).filter(Boolean).join(', ').slice(0, 90),
                 options: optLabels(group)};
    (checked.length ? done : out).push(row);       // 0 checked = unanswered
  }

  // Radio groups, same reasoning. NB group by NAME before id: smartapply gives every radio
  // on the page the SAME id (single-select-question) while the name (q_<hash>) is the real
  // group identity — id-first grouping would fuse three questions into one.
  const radios = [...doc.querySelectorAll('input[type=radio]')].filter(vis);
  const rgroups = {};
  let rsynth = 0;
  // Same lone-control fix as checkboxes: a single required radio with no name/id (rare but seen on
  // one-option acknowledgment radios) was skipped by the old `if (k)` — give it a group of one.
  for (const r of radios) { let k = r.name || r.id; if (!k) k = '__lone_radio_' + (rsynth++); (rgroups[k] = rgroups[k] || []).push(r); }
  for (const [k, group] of Object.entries(rgroups)) {
    const label = groupLabel(group, k);
    const req = group.some(r => r.required || attr(r, 'aria-required') === 'true')
                || groupStarred(group);
    const picked = group.find(r => r.checked);
    // A VOLUNTARY group with nothing picked is reported too, marked as such — the EEO self-ID
    // radios carry no star ("voluntary"), yet smartapply's own Continue refuses until each has
    // an explicit choice (including "I don't wish to answer"), and a group the census cannot
    // see is a group the teach seam cannot address (live, 2026-08-10). The required GATE
    // filters on `required_via` and stays exactly as strict as before.
    if (!req && picked) continue;
    const row = {field: label.slice(0, 90), selector: __idSel(group[0]),
                 kind: 'radio_group', required_via: req ? 'group' : 'none',
                 value_read_at: 'aria-checked',
                 answered: !!picked, valid: true,
                 value_preview: picked ? optLabel(picked) : '',
                 options: optLabels(group)};
    (picked ? done : out).push(row);
  }

  // THE PAGE'S OWN VERDICT OUTRANKS OUR ELEMENT LIST. `aria-invalid=true` is the site saying,
  // in its own words, that this control is not satisfied — and it is the ONE signal that does not
  // depend on us having guessed the right tags. Workday renders its State picker as a bare
  // <button> with no role, no automation-id and no aria-required: invisible to `singles` above,
  // so the census read the form COMPLETE while the page printed "Error: The field State is
  // required and must have a value" and refused every Continue (live 2026-08-11).
  //
  // Filed only when the walk above did not already report the control, and always as
  // `required_via: aria-invalid` so the row's provenance is legible. This cannot mint a false
  // blocker: a page that marks a control invalid is a page that will not advance past it.
  const seenSel = new Set(out.map(r => r.selector).filter(Boolean));
  for (const el of doc.querySelectorAll('[aria-invalid=true]')) {
    if (!__isUserField(el)) continue;
    const sel = __idSel(el) || __cssPath(el);
    if (sel && seenSel.has(sel)) continue;
    const label = labelFor(el);
    const t = __valueTruth(el);
    out.push({field: label.slice(0, 90), selector: sel,
              kind: (el.tagName || '').toLowerCase(),
              required_via: 'aria-invalid', value_read_at: t.read_at,
              answered: t.answered, valid: false, value_preview: t.preview,
              options: selectOptions(el), ...optionMeta(el)});
    if (sel) seenSel.add(sel);
  }

  // FILE UPLOADERS, which no clause above can see. `singles` filters on __isUserField, and a
  // file input is NEVER keyboard-reachable by design — the widget draws a button and a drop zone
  // over a display:none input, so the one required control on the screen was invisible to the
  // whole census (live 2026-08-12: "all required fields answered" over a page printing "The field
  // Upload a file (5MB max) is required"). Requiredness comes from the label, exactly as
  // elsewhere; the ANSWER comes from the widget, because `files.length` is not the truth for an
  // uploader that POSTs on change and resets the input (see _UPLOAD_WITNESS_JS).
  //
  // `within` is the row's own addressing hint: two identical uploaders are told apart by the
  // section they sit in, and nothing else. It travels on the row so the teach seam and the fill
  // can name the one they mean.
  const uploaders = [];
  for (const el of doc.querySelectorAll('input[type=file]')) {
    const sel = __idSel(el) || __cssPath(el);
    if (sel && seenSel.has(sel)) continue;
    // The section that NAMES this uploader: the nearest heading above it. On Workday that is
    // "Resume/CV" vs the "Attachments" block — the only signal that distinguishes the two, and the
    // one the act-time resolver scopes on. Same shared definition, so the address book the census
    // publishes and the target the resolver picks are the same correlation.
    const qUp = __questionOf(el);
    const within = qUp.section || '';
    // labelFor's data-automation-id fallback is WRONG for an uploader: Workday gives every file
    // input the same `file-upload-input-ref`, so both uploaders would answer to one name and the
    // asterisk that makes one of them required ("Upload a file (5MB max)*", a sibling node) would
    // never be read. `__questionOf` knows how to read a composite widget's label — climb to the
    // widget, skip its furniture, refuse a neighbour's label — so ASK IT rather than repeating the
    // walk here. A second copy of this reasoning is what let the census and the act-time check
    // disagree about the same uploader (live 2026-08-12).
    let label = labelFor(el);
    if (label === attr(el, 'data-automation-id') || label === '(unlabeled)' ||
        /drop files|select files|drag|browse/i.test(label)) {
      // …and an uploader the page never labelled at all is named by its SECTION, which is what a
      // human would call it ("the Attachments box"), never by a neighbour's label.
      label = (qUp.source === 'proximity' ? qUp.question : '') || within || label;
    }
    // The uploader's own container — the walk that stops before a neighbouring file input, so
    // one uploader's rendered row never answers for another.
    let box = null;
    for (let n = el.parentElement, d = 0; n && n !== doc.body && d < 8; d++, n = n.parentElement) {
      if (n.querySelectorAll('input[type=file]').length > 1) break;
      box = n;
    }
    const scope = box ? txt(box) : '';
    // A rendered filename is the widget saying it holds a file. Bare "Drop files here / Select
    // files" is the empty state and says nothing. Take the FILENAME, not the row that contains it:
    // the row's full text is the widget's furniture plus the name ("Drop files here or Select files
    // GM_Resume.pdf 111.32 KB Successfully Uploaded!"), and a preview is supposed to answer "which
    // file" in a glance.
    // NO SPACES in the name part, deliberately. With spaces allowed the class is greedy across the
    // widget's whole furniture and "GM_Resume.pdf" came back as "YYYY Attachments Drop files here
    // or Sele…" — a preview that names a sentence instead of a file. A file whose real name has
    // spaces previews as its last word plus extension, which is still recognisably the file.
    const FILENAME = /[\w.()\-]{1,60}\.(pdf|docx?|rtf|txt|odt|png|jpe?g)\b/i;
    const named = ((box ? FILENAME.exec(txt(box)) : null) || FILENAME.exec(scope) || [''])[0].trim();
    const answered = !!named || !!(el.files && el.files.length);
    let required = false, via = 'none';
    if (el.required) { required = true; via = 'required-attr'; }
    else if (attr(el, 'aria-required') === 'true') { required = true; via = 'aria-required'; }
    else if (/\*/.test(label) || /\*/.test(within)) { required = true; via = 'label-asterisk'; }
    else if (/\b(required)\s*$/i.test(label)) { required = true; via = 'label-required'; }
    const row = {field: (label && label !== '(unlabeled)' ? label : (within || 'file upload')).slice(0, 90),
                 selector: sel, within: within.slice(0, 90), question_source: qUp.source,
                 kind: 'file', required_via: required ? via : 'none',
                 value_read_at: named ? 'rendered_file_row' : 'files.length',
                 answered, valid: true, value_preview: (named || '').slice(0, 40)};
    if (!required) { if (optional.length < 40) optional.push(row); }
    else (answered ? done : out).push(row);
    // Remembered for THE JOIN below, and deliberately not published on the row: the container's
    // text is how we recognise which uploader the page is complaining about, but it is page
    // content, and a census row carries names and addresses — not transcripts.
    uploaders.push({row, scope});
    if (sel) seenSel.add(sel);
  }

  // THE PAGE'S OWN ERROR SUMMARY — the last word, and the cheapest one. Workday prints
  // "Errors Found · Error-Upload a file (5MB max) · The field Upload a file (5MB max) is required
  // and must have a value" in a plain <div data-automation-id=errorHeading> with no role=alert, no
  // aria-live and no link to the control. So every ARIA-shaped rule above misses it, and the
  // census reported a COMPLETE form over a page that had just refused to advance (live 2026-08-12).
  //
  // This clause reads the sentence, not the markup: whatever the page NAMES as unsatisfied is
  // unsatisfied, whether or not we found a control for it. A row with no selector is deliberate —
  // it says "the page rejects this field and we cannot address it", which is the honest state and
  // the one that stops a submit. Matching an already-reported field by name keeps it from
  // double-filing.
  // A COLLAPSED banner is still a verdict. Workday's error summary starts expanded and collapses
  // itself; once collapsed the item list is display:none, so a visibility-gated read saw an empty
  // "Errors Found" and the census went back to reporting a COMPLETE form over a page that was
  // refusing to advance (live 2026-08-12). What matters is whether the page is ASSERTING errors
  // right now — the banner's own header is on screen — not whether the operator has the details
  // unfolded. So: the header gates, the items are read either way.
  const asserting = [...doc.querySelectorAll('*')].some(
    el => __vis(el) && /^errors? found\b/i.test(txt(el)) && txt(el).length < 400);
  const complained = new Set();
  const errText = [];
  for (const el of doc.querySelectorAll(
        '[data-automation-id*=rror], [role=alert], [aria-live=assertive], [class*=rror]')) {
    if (!__vis(el) && !asserting) continue;
    const t = txt(el);
    if (t && t.length <= 400) errText.push(t);
  }
  const namedField = /(?:the field|field)\s+(.+?)\s+is required(?: and must have a value)?/gi;
  for (const t of errText) {
    let m;
    while ((m = namedField.exec(t)) !== null) {
      const name = (m[1] || '').trim().slice(0, 90);
      if (!name || complained.has(name.toLowerCase())) continue;
      complained.add(name.toLowerCase());
    }
  }
  // THE JOIN. A complaint with no control is unactionable; a control with a bad name is
  // unaddressable. The census produced exactly those two half-rows for the same field — "upload a
  // file (5mb max)" with `selector: null` beside an uploader named "Drop files here or Select
  // files" — and neither half could have driven the fix (live 2026-08-12).
  //
  // They are joined the way a person joins them: the page prints its complaint NEXT TO the control
  // it is complaining about, so the field the page names is the one whose own container repeats
  // that name. The merged row carries the PAGE's name (authoritative — it is the string the page
  // will keep refusing on) and OUR selector (actionable). Provenance says it was joined, because a
  // join is an inference and inferences travel labelled.
  const norm = (s) => (s || '').toLowerCase().replace(/[*:]+/g, ' ').replace(/[^a-z0-9]+/g, ' ').trim();
  const sameName = (a, b) => {
    const x = norm(a), y = norm(b);
    if (!x || !y) return false;
    if (x === y) return true;
    const short = x.length <= y.length ? x : y, long = x.length <= y.length ? y : x;
    return short.length >= 4 && long.includes(short);
  };
  const contains = (hay, needle) => {
    const h = norm(hay), n2 = norm(needle);
    return !!h && !!n2 && n2.length >= 4 && h.includes(n2);
  };
  for (const name of complained) {
    // Already on the unanswered list under this name? Then the walk found it and nothing to add.
    if (out.some(r => sameName(r.field, name))) continue;
    // Reported as ANSWERED (or merely OPTIONAL) while the page says required-and-empty? The page
    // wins in both cases — it is the one refusing to advance.
    let joined = null;
    // The uploader whose OWN CONTAINER prints the complained name is the control the page means.
    const up = uploaders.find(u => contains(u.scope, name) || sameName(u.row.field, name));
    for (const list of [done, optional, out]) {
      const i2 = up ? list.indexOf(up.row) : -1;
      if (i2 >= 0) { joined = list.splice(i2, 1)[0]; break; }
    }
    if (!joined) {
      for (const list of [done, optional]) {
        const idx = list.findIndex(r => sameName(r.field, name));
        if (idx >= 0) { joined = list.splice(idx, 1)[0]; break; }
      }
    }
    if (joined) {
      out.push({...joined, field: name, required_via: 'page-error',
                question_source: 'page-error+' + (joined.question_source || 'scan'),
                answered: false, valid: false});
      continue;
    }
    out.push({field: name, selector: null, kind: 'unknown', required_via: 'page-error',
              question_source: 'page-error', value_read_at: 'page_error_summary',
              answered: false, valid: false, value_preview: ''});
  }

  // A REFUSAL THAT NAMES NO FIELD. Workday answered two Save-and-Continue presses with
  //     "Errors Found · Error-Page Error · Error Code: VPS|7909b5a0-…"
  // and nothing else: no field, no control, fresh codes on every attempt. The census had all
  // required fields answered and said so, which is TRUE and completely misleading — the page was
  // refusing for a reason it declined to attribute (live 2026-08-12, SolutionHealth JR11587).
  //
  // Reported as its own kind of fact, never as a field row: there is no control to fill, so a
  // field-shaped row would send the crank hunting for one. A page-level refusal is a state for a
  // HUMAN to judge — the same class as a captcha, and the same response: name it, don't guess at
  // it. `page_errors` is empty on every healthy page, so nothing downstream changes shape.
  const pageErrors = [];
  if (asserting) {
    for (const t of errText) {
      // Strip the parts already attributed to a field; what remains is the unattributed refusal.
      let rest = t.replace(/(?:the field|field)\s+.+?\s+is required(?: and must have a value)?\.?/gi, '').trim();
      if (!rest || rest.length < 6) continue;
      if (/^errors? found$/i.test(rest)) continue;               // the header itself
      if (!/error|unable|failed|problem|try again/i.test(rest)) continue;
      if (!pageErrors.includes(rest)) pageErrors.push(rest.slice(0, 200));
    }
  }

  // A COMPLAINT ABOUT A FIELD THAT IS NOT "YOU LEFT IT EMPTY".
  //
  // The whole gate above asks one question — which REQUIRED fields are UNANSWERED — and a page can
  // refuse for a reason that answers neither half. Live 2026-08-14, Boston Children's: the resume
  // parser filled the OPTIONAL "Job Description" past the form's limit and the page printed, in
  // red, under the control: "Job Description (current or recent job responsibilities) is too long,
  // maximum {500 chars}." The census reported `unanswered: 0`, `page_errors: []` and that very
  // field as `valid: true`, so every rung above it believed the form complete while Save &
  // Continue did nothing at all. Twice.
  //
  // `page_errors` cannot carry this and should not: that list is deliberately for refusals the
  // page attributes to NO field ("Error Code: VPS|…"), which are a human's to judge because there
  // is nothing to fill. This one names its control, which makes it actionable — and it is the
  // operator's 2026-08-12 rule arriving one axis over: *"regardless of whether it's required or
  // not"*. An optional field filled with the wrong answer is the same error.
  const fieldErrors = [];
  const known = [...out, ...done, ...optional];
  const seenErr = new Set();
  const pushErr = (f, message) => {
    const key = f.field + '|' + message;
    if (seenErr.has(key)) return;
    seenErr.add(key);
    fieldErrors.push({field: f.field, selector: f.selector || null,
                      required: !!f.required, message: message.slice(0, 200)});
  };
  // Pass 1 — the error-styled nodes we already collect, joined to the field they name.
  for (const t of errText) {
    if (/is required(?: and must have a value)?/i.test(t)) continue;   // the empty-field case
    if (/^errors? found$/i.test(t.trim())) continue;
    const hit = known.find((f) => contains(t, f.field));
    if (hit) pushErr(hit, t);
  }
  // Pass 2 — FROM THE FIELD SIDE, because a page's complaint is not obliged to wear our markup.
  //
  // Pass 1 hunts nodes matching `[role=alert] / [class*=rror] / [data-automation-id*=rror]` and
  // then asks which field they name. On Boston Children's the complaint is rendered in none of
  // those, so a form the page was actively refusing censused with `field_errors: []` — the exact
  // shape of the 2026-08-12 lesson ("no role=alert, no aria-live, no link to a control — read the
  // SENTENCE"), one layer over. Markup is the site's choice; the FIELD LIST is ours and it is
  // enumerable, so we walk it instead: for each control, read its own wrapper for a sentence that
  // names it and complains. Bounded by the census we already have.
  const CUES = /(too long|too short|must be|must have|cannot exceed|exceeds|maximum|minimum|not valid|invalid|is not a valid|please enter)/i;
  for (const f of known) {
    if (!f.selector) continue;
    let el = null;
    try { el = __findAll(f.selector)[0] || null; } catch (e) { el = null; }
    if (!el) continue;
    // The largest box around this control that holds no OTHER user field — the same wrapper the
    // question walk uses, so a neighbour's complaint cannot be attributed here.
    let box = el.parentElement, guard = 0;
    while (box && guard++ < 6) {
      const mine = box.querySelectorAll('input, select, textarea');
      if (mine.length > 1) { box = box.parentElement === null ? box : box; break; }
      if (!box.parentElement) break;
      const up = box.parentElement;
      if (up.querySelectorAll('input, select, textarea').length > 1) break;
      box = up;
    }
    if (!box) continue;
    const text = txt(box);
    if (!text || text.length > 400) continue;
    if (/is required(?: and must have a value)?/i.test(text)) continue;
    if (!CUES.test(text)) continue;
    // The sentence, not the whole wrapper: take the line that carries the cue.
    const line = (text.split(/\n+/).find((l) => CUES.test(l)) || text).trim();
    if (line) pushErr(f, line);
  }

  return {unanswered: out, answered: done, optional,
          page_errors: pageErrors.slice(0, 6),
          field_errors: fieldErrors.slice(0, 8),
          url: (location.href || '').slice(0, 140)};
}
"""

# Inject the shared tells. A placeholder + replace (rather than an f-string) keeps the JS
# above readable as JS — it has braces everywhere, and an f-string would need every one
# doubled.
SCAN_REQUIRED_JS = SCAN_REQUIRED_JS.replace("__WIDGET_TELLS__", WIDGET_TELLS_JS)
assert "__WIDGET_TELLS__" not in SCAN_REQUIRED_JS, "the tells placeholder did not substitute"

# The shared tells go into every blob that resolves a TARGET, so `__findAll` reaches frames from all
# of them. One placeholder per blob, asserted — an unsubstituted blob is a page-side SyntaxError that
# surfaces as "no node matching", i.e. as a stale recipe.
_FOCUS_AND_OPEN_JS = _FOCUS_AND_OPEN_JS.replace("__WIDGET_TELLS__", WIDGET_TELLS_JS)
assert "__WIDGET_TELLS__" not in _FOCUS_AND_OPEN_JS

# CHECK_GROUP_JS resolves a target too, and was the blob that never got its tells — so every call
# threw `ReferenceError: __findAll is not defined`, `Runtime.evaluate` returned no value, and the
# endpoint reported a bare `outcome: "error"` with an EMPTY detail. Which means the required-consent
# checkbox step has never actually run on any tenant since it was added (2026-08-11): its recipe
# said "check the acknowledge box", the call always failed, and the failure was worded as though
# the page were at fault. Found 2026-08-13 when it blocked a C&S Workday signup — the operator
# read it as "it couldn't find the Create Account button".
CHECK_GROUP_JS = CHECK_GROUP_JS.replace("__WIDGET_TELLS__", WIDGET_TELLS_JS)
assert "__WIDGET_TELLS__" not in CHECK_GROUP_JS
