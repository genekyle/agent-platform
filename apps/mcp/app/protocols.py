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
  const el = document.querySelector(sel);
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
        f"  const el = document.querySelector({s});"
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


# --- the checkbox-group protocol ----------------------------------------------------
# Greenhouse renders required checkbox groups as question_<id>[]_<optid>. Group by the id
# prefix BEFORE '[]'. A scan that only looks at inputs/selects misses them entirely — we
# missed `restrictions` and `languages` on the first pass, both required. 0 checked =
# unanswered. Match labels EXACTLY: "No" must not match "Yes, non-compete".
CHECK_GROUP_JS = r"""
(cfg) => {
  const log = [];
  const vis = e => { try { const r = e.getBoundingClientRect();
                           return e.offsetParent !== null && r.width > 0 && r.height > 0; }
                     catch (x) { return false; } };
  const txt = e => ((e && (e.innerText || e.textContent)) || '').replace(/\s+/g, ' ').trim();
  const label = c => txt(c.closest('label')) || c.getAttribute('aria-label') || c.value || '';

  const anchor = document.querySelector(cfg.selector);
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

  const available = group.map(label);
  const missing = cfg.values.filter(v => !available.some(a => a === v));
  if (missing.length)
    return {ok: false, code: 'no_option', detail: 'no option(s) ' + JSON.stringify(missing),
            options: available, log: log};

  // Exact match only. Toggle by CLICK (not .checked = true) so React sees the event.
  const changed = [];
  for (const b of group) {
    const want = cfg.values.includes(label(b));
    if (b.checked !== want) { b.scrollIntoView({block: 'center'}); b.click(); changed.push(label(b)); }
  }
  log.push({step: 'select', changed: changed});

  // CONFIRM by re-reading the DOM, not by trusting the clicks.
  const now = group.filter(b => b.checked).map(label);
  const okSet = cfg.values.length === now.length && cfg.values.every(v => now.includes(v));
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
() => {
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
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                 if (l) return txt(l); }
    const al = attr(el, 'aria-label'); if (al) return al;
    const ref = attr(el, 'aria-labelledby');
    if (ref) { const n = document.getElementById(ref); if (n) return txt(n); }
    const own = el.closest('label'); if (own) return txt(own);
    const aid = attr(el, 'data-automation-id'); if (aid) return aid;
    const ph = attr(el, 'placeholder'); if (ph) return ph;
    const wrap = el.closest('[class*=field], fieldset, [role=group], li');
    const lbl = wrap && wrap.querySelector('label, legend');
    return lbl ? txt(lbl) : '(unlabeled)';
  };

  // __isUserField, not __vis: a required control the user cannot TAB to is a validation
  // proxy, not a question. react-select mounts exactly such a proxy (opacity:0, tabIndex=-1)
  // which a rect-based visibility check happily reports as a phantom required field.
  const singles = [...document.querySelectorAll(
    'input:not([type=checkbox]):not([type=radio]):not([type=hidden]), select, textarea, [role=combobox]'
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
  const selectOptions = (el) => {
    if ((el.tagName || '').toLowerCase() !== 'select') return undefined;
    return [...el.options].map(o => txt(o).slice(0, 60)).filter(Boolean).slice(0, 24);
  };
  const seen = new Set();
  for (const el of singles) {
    const disabled = !!(el.disabled || attr(el, 'aria-disabled') === 'true' || el.readOnly);
    const label = labelFor(el);
    let required = false, via = 'none';
    if (disabled) { required = false; via = 'disabled'; }          // rule 1 — short-circuits
    else if (el.required) { required = true; via = 'required-attr'; }
    else if (attr(el, 'aria-required') === 'true') { required = true; via = 'aria-required'; }
    else if (/\*/.test(label)) { required = true; via = 'label-asterisk'; }
    if (!required) continue;

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
    const row = {field: label.slice(0, 90), selector: __idSel(el),
                 kind: __isReactSelect(el) ? 'react_select' : el.tagName.toLowerCase(),
                 required_via: via, value_read_at: truth.read_at,
                 // `answered` is NOT always false on the unanswered list: a FILLED-but-INVALID
                 // field is reported there too, and the gate needs the distinction
                 // (reason "invalid" vs "empty").
                 answered: truth.answered, valid: !invalid, value_preview: truth.preview,
                 options: selectOptions(el)};
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
    while (node && node !== document.body && !group.every(m => node.contains(m)))
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
    for (let i = 0; i < 5 && node && node !== document.body; i++) {
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
    for (let i = 0; i < 5 && node && node !== document.body; i++) {
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
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                 if (l) return txt(l).slice(0, 60); }
    const own = el.closest('label'); if (own) return txt(own).slice(0, 60);
    return (attr(el, 'value') || '').slice(0, 60);
  };
  const optLabels = (group) => group.map(optLabel).filter(Boolean).slice(0, 16);

  // rule 2 — checkbox groups, which the old scan missed entirely.
  const boxes = [...document.querySelectorAll('input[type=checkbox]')].filter(vis);
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
  const radios = [...document.querySelectorAll('input[type=radio]')].filter(vis);
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

  return {unanswered: out, answered: done, url: (location.href || '').slice(0, 140)};
}
"""

# Inject the shared tells. A placeholder + replace (rather than an f-string) keeps the JS
# above readable as JS — it has braces everywhere, and an f-string would need every one
# doubled.
SCAN_REQUIRED_JS = SCAN_REQUIRED_JS.replace("__WIDGET_TELLS__", WIDGET_TELLS_JS)
assert "__WIDGET_TELLS__" not in SCAN_REQUIRED_JS, "the tells placeholder did not substitute"
