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
# Replaces /scan_form, which is actively misleading: on Workday every field returns the whole
# fieldset's text as its label, so First/Middle/Last are indistinguishable — it was abandoned
# mid-session and hand-rolled instead.
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
  const vis = e => { try { const r = e.getBoundingClientRect();
                           return e.offsetParent !== null && r.width > 0 && r.height > 0; }
                     catch (x) { return false; } };
  const txt = e => ((e && (e.innerText || e.textContent)) || '').replace(/\s+/g, ' ').trim();
  const attr = (e, a) => (e && e.getAttribute ? e.getAttribute(a) : null);

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

  const singles = [...document.querySelectorAll(
    'input:not([type=checkbox]):not([type=radio]):not([type=hidden]), select, textarea, [role=combobox]'
  )].filter(vis);

  const out = [];
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

    // Answered? Read at the widget's own truth, not blindly at .value.
    const wrap = el.closest('[class*=select__control], .select, [class*=field], div') || el.parentElement;
    const sv = wrap && wrap.querySelector('[class*=singleValue]');
    let answered, readAt;
    if (sv !== null && sv !== undefined) { answered = !!txt(sv); readAt = '[class*=singleValue]'; }
    else if (el.tagName === 'SELECT') { answered = el.selectedIndex > 0 && el.value !== ''; readAt = '.value'; }
    else { answered = !!(el.value && String(el.value).trim()); readAt = '.value'; }
    if (answered) continue;

    const id = el.id || label;
    if (seen.has(id)) continue;          // a hidden required TWIN is not a second question
    seen.add(id);
    out.push({field: label.slice(0, 90), selector: el.id ? '#' + el.id : null,
              kind: el.tagName.toLowerCase(), required_via: via, value_read_at: readAt,
              answered: false});
  }

  // rule 2 — checkbox groups, which the old scan missed entirely.
  const boxes = [...document.querySelectorAll('input[type=checkbox]')].filter(vis);
  const groups = {};
  for (const b of boxes) {
    const k = (b.id || b.name || '').split('[')[0];
    if (!k) continue;
    (groups[k] = groups[k] || []).push(b);
  }
  for (const [k, group] of Object.entries(groups)) {
    const wrap = group[0].closest('fieldset, [role=group], [class*=field], li, div');
    const legend = wrap && wrap.querySelector('legend, label');
    const label = legend ? txt(legend) : k;
    const anyDisabled = group.every(b => b.disabled);
    const req = !anyDisabled &&
                (group.some(b => b.required || attr(b, 'aria-required') === 'true') || /\*/.test(label));
    if (!req) continue;
    if (group.some(b => b.checked)) continue;      // 0 checked = unanswered
    out.push({field: label.slice(0, 90), selector: group[0].id ? '#' + group[0].id : null,
              kind: 'checkbox_group', required_via: 'group', value_read_at: 'checked',
              answered: false});
  }

  // Radio groups, same reasoning.
  const radios = [...document.querySelectorAll('input[type=radio]')].filter(vis);
  const rgroups = {};
  for (const r of radios) { const k = r.name || r.id; if (k) (rgroups[k] = rgroups[k] || []).push(r); }
  for (const [k, group] of Object.entries(rgroups)) {
    if (group.some(r => r.checked)) continue;
    const wrap = group[0].closest('fieldset, [role=group], [class*=field], li, div');
    const legend = wrap && wrap.querySelector('legend, label');
    const label = legend ? txt(legend) : k;
    const req = group.some(r => r.required || attr(r, 'aria-required') === 'true') || /\*/.test(label);
    if (!req) continue;
    out.push({field: label.slice(0, 90), selector: group[0].id ? '#' + group[0].id : null,
              kind: 'radio_group', required_via: 'group', value_read_at: 'aria-checked',
              answered: false});
  }

  return {unanswered: out, url: (location.href || '').slice(0, 140)};
}
"""
