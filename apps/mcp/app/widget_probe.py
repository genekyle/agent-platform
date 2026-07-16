"""/describe_widget's page-side classifier — the missing primitive.

    "I manually classified every widget by writing the same probe eleven times. That's not
     a missing endpoint — that's a missing primitive."  (docs/PLAN_interaction_api.md §0)

On 2026-07-15 the same "inspect this widget" JS was hand-written ~11 times in one session
(country, location, school, degree, discipline, 3× month, attestation, veteran, gender).
Same shape every time, subtly different every time, and every bug that day lived in one of
those hand-rolled scripts rather than in an endpoint. This is that probe, written once.

Everything else in the API falls out of it: /select_option, /set_date and /check_group
DISPATCH on `widget_type` instead of the caller knowing what it's looking at, which is what
keeps the intent vocabulary small enough for a local model to speak.

--------------------------------------------------------------------------------------
READ-ONLY, and why that differs from the plan
--------------------------------------------------------------------------------------
The plan's §2 sketch has `options: [...] # after open, if enumerable`. This does not open
anything. Opening is an ACTION: it can dismiss another widget's popup, fire a server-side
fetch, and change the state the caller is about to act on. `Intent.DESCRIBE` is in
READ_ONLY_INTENTS and expands to zero ActionIds, so a describe that opens would break its
own contract.

So: options are reported when they are readable WITHOUT opening (native select, checkbox
group, radio group, or a listbox that happens to already be open), and otherwise
`options: null` with `options_enumerable_by: "open"`. That is more honest than a probe that
mutates the page to answer a question about it — and /select_option opens the widget anyway
and reports what it found there.
"""

from __future__ import annotations

# The classifier. Returns the widget's own account of itself.
#
# ORDER MATTERS: checks run most-specific-first. A Greenhouse month field is BOTH a
# react-select and part of a month_year pair; a Workday date section is a spinbutton that
# would otherwise read as `number`. Specific shapes must claim their nodes before the
# generic ones do.
DESCRIBE_WIDGET_JS = r"""
(cfg) => {
  const el = document.querySelector(cfg.selector);
  if (!el) return {found: false, detail: 'no node matching ' + cfg.selector};

  const vis = (n) => { try { const r = n.getBoundingClientRect();
                             return n.offsetParent !== null && r.width > 0 && r.height > 0; }
                       catch (e) { return false; } };
  const txt = (n) => ((n && (n.innerText || n.textContent)) || '').replace(/\s+/g, ' ').trim();
  const attr = (n, a) => (n && n.getAttribute ? n.getAttribute(a) : null);

  // The field's WRAPPER — react-select and Workday both render the real control as a
  // sibling/descendant of a container, so classification has to look around the node, not
  // just at it.
  const wrap = el.closest('[data-automation-id], .select, [class*=select__control], ' +
                          '[class*=field], fieldset, [role=group], li, div') || el.parentElement || el;

  // ---- SHAPE TELLS ------------------------------------------------------------------
  // react-select: renders a .select__* / *singleValue subtree and drives an input with
  // aria-autocomplete=list. THE tell that matters is singleValue: it is where the chosen
  // value actually lives, because the input's own .value goes EMPTY after a pick.
  const reactSelect = !!(wrap.querySelector('[class*=singleValue], [class*=select__control], ' +
                                            '[class*=select__value-container]') ||
                         (attr(el, 'aria-autocomplete') === 'list' &&
                          wrap.className && /select/i.test(String(wrap.className))));

  // Workday hierarchical prompt: a formField-* whose popup carries a searchBox. We cannot
  // see the searchBox without opening, so classify on the STABLE tenant-independent tell —
  // the data-automation-id — plus a listbox/button opener.
  const aid = attr(el, 'data-automation-id') || attr(wrap, 'data-automation-id') || '';
  const promptish = /^formField-/.test(aid) &&
                    !!wrap.querySelector('[data-automation-id=promptIcon], [role=button], button');

  // Workday segmented date: dateSectionMonth/Day/Year-input spinbuttons, linked and
  // auto-advancing. Must be caught BEFORE `number`, which is what they look like.
  const dateSection = !!wrap.querySelector('[data-automation-id*=dateSection]') ||
                      /dateSection/.test(aid);

  // Greenhouse month/year pair: a month react-select + a plain year number input, ids
  // ending -month-N / -year-N. Must be caught BEFORE react_select claims the month.
  const idm = (el.id || '').match(/^(.*?)-?month-?-?(\d+)$/);
  let yearNode = null;
  if (idm) {
    const cands = [el.id.replace(/month/, 'year')];
    for (const c of cands) { const n = document.getElementById(c); if (n) { yearNode = n; break; } }
  }
  const monthYear = !!(idm && yearNode);

  // Checkbox group: Greenhouse renders question_<id>[]_<optid>. Group by the id prefix
  // BEFORE '[]'. A scan that only looks at inputs/selects misses these entirely — that is
  // how two required groups (restrictions, languages) were missed on the first pass.
  const cbs = [...wrap.querySelectorAll('input[type=checkbox]')].filter(vis);
  const groupKey = (n) => (n.id || n.name || '').split('[')[0];
  const cbGroup = cbs.length > 1 && new Set(cbs.map(groupKey)).size === 1;

  const radios = [...wrap.querySelectorAll('input[type=radio], [role=radio]')].filter(vis);

  // ---- CLASSIFY (most specific first) ------------------------------------------------
  let type = 'unknown';
  if (el.tagName === 'INPUT' && el.type === 'file') type = 'file';
  else if (dateSection) type = 'segmented_date';
  else if (monthYear) type = 'month_year';
  else if (cbGroup) type = 'checkbox_group';
  else if (cbs.length === 1 && !cbGroup) type = 'checkbox_group';   // a lone required ack
  else if (radios.length > 1) type = 'radio_group';
  else if (el.tagName === 'SELECT') type = 'native_select';
  else if (reactSelect) type = 'react_select';
  else if (promptish) type = 'prompt_hierarchical';
  else if (attr(el, 'aria-haspopup') === 'listbox' || attr(el, 'aria-expanded') !== null ||
           attr(el, 'role') === 'combobox') type = 'aria_listbox';
  else if (el.tagName === 'INPUT' && el.type === 'number') type = 'number';
  else if (el.tagName === 'TEXTAREA' ||
           (el.tagName === 'INPUT' && ['text','email','tel',''].includes(el.type))) type = 'text';

  // ---- WHERE THE TRUTH LIVES ---------------------------------------------------------
  // The single most valuable field. `.value` on a react-select holds TRANSIENT search text
  // and empties on blur — verifying there "confirmed" an empty field twice. Workday dates
  // DISPLAY while the model stays empty. The widget knows; nothing else has to.
  const VALUE_READ_AT = {
    aria_listbox: 'opener_label', react_select: '[class*=singleValue]',
    prompt_hierarchical: 'opener_label', native_select: '.value',
    radio_group: 'aria-checked', checkbox_group: 'checked',
    segmented_date: 'aria-valuenow', month_year: '[class*=singleValue]',
    text: '.value', number: '.value', file: 'files.length', unknown: '',
  };
  const readAt = VALUE_READ_AT[type] || '';

  // ---- ANSWERED? read at the truth location, never at .value by default ---------------
  let answered = false, valuePreview = '';
  try {
    if (type === 'react_select' || type === 'month_year') {
      const sv = wrap.querySelector('[class*=singleValue]');
      valuePreview = txt(sv); answered = !!valuePreview;
    } else if (type === 'aria_listbox' || type === 'prompt_hierarchical') {
      valuePreview = txt(el) || (el.value || ''); answered = !!valuePreview.trim();
    } else if (type === 'native_select') {
      answered = el.selectedIndex > 0 && el.value !== ''; valuePreview = el.value || '';
    } else if (type === 'checkbox_group') {
      const n = cbs.filter(c => c.checked).length; answered = n > 0; valuePreview = n + ' checked';
    } else if (type === 'radio_group') {
      const c = radios.find(r => r.checked || attr(r, 'aria-checked') === 'true');
      answered = !!c; valuePreview = c ? txt(c.closest('label')) : '';
    } else if (type === 'file') {
      answered = !!(el.files && el.files.length); valuePreview = answered ? el.files[0].name : '';
    } else if (type === 'segmented_date') {
      const secs = [...wrap.querySelectorAll('[data-automation-id*=dateSection]')];
      const vals = secs.map(s => attr(s, 'aria-valuenow') || s.value || '').filter(Boolean);
      answered = vals.length > 0 && vals.length === secs.length; valuePreview = vals.join('/');
    } else {
      answered = !!(el.value && String(el.value).trim()); valuePreview = String(el.value || '');
    }
  } catch (e) { valuePreview = 'read-error:' + e.message; }

  // ---- REQUIRED? disabled BEATS the asterisk -----------------------------------------
  // KKR's work End date keeps its '*' AND aria-required='true' after 'Current role' is
  // ticked — but the input is `disabled`, so it is NOT required. Conversely the conditional
  // "If yes, provide details" fields keep aria-required='true' even when the parent is No,
  // and they DO still need filling. So: disabled short-circuits; otherwise trust the
  // attribute; the label's '*' is the weakest signal and goes stale.
  const disabled = !!(el.disabled || attr(el, 'aria-disabled') === 'true');
  const label = txt(wrap.querySelector('label, legend')) || txt(wrap).slice(0, 120);
  let required = false, requiredVia = 'none';
  if (disabled) { required = false; requiredVia = 'disabled'; }
  else if (el.required) { required = true; requiredVia = 'required-attr'; }
  else if (attr(el, 'aria-required') === 'true') { required = true; requiredVia = 'aria-required'; }
  else if (/\*/.test(label) || /\brequired\b/i.test(label)) { required = true; requiredVia = 'label-asterisk'; }

  // ---- OPENER / POPUP / COMMIT -------------------------------------------------------
  const opener = ['aria_listbox','prompt_hierarchical','react_select','month_year','native_select']
                   .includes(type)
    ? (wrap.querySelector('[role=combobox], [role=button], button') || el) : null;
  // The widget names its own popup via aria-controls/aria-owns. Without that we search
  // document-wide and can click ANOTHER widget's identically-named option — a Workday page
  // had 63 stray [role=option]s from other fields, scoped down to 5. NB react-select does
  // NOT expose aria-controls until it expands, so `document` here is expected, not a miss.
  const ref = opener ? (attr(opener, 'aria-controls') || attr(opener, 'aria-owns')) : null;
  const popupEl = ref ? document.getElementById(ref) : null;
  const scope = popupEl || document;

  // Options, ONLY when readable without opening. See the module docstring.
  let options = null, enumerableBy = 'open';
  if (type === 'native_select') { options = [...el.options].map(o => o.text.trim()); enumerableBy = 'read'; }
  else if (type === 'checkbox_group') {
    options = cbs.map(c => txt(c.closest('label')) || attr(c, 'aria-label') || c.value); enumerableBy = 'read';
  } else if (type === 'radio_group') {
    options = radios.map(r => txt(r.closest('label')) || attr(r, 'aria-label') || r.value); enumerableBy = 'read';
  } else if (popupEl && attr(opener, 'aria-expanded') === 'true') {
    options = [...scope.querySelectorAll('[role=option]')].filter(vis).map(txt); enumerableBy = 'read';
  }

  // react-select FETCHES on real per-char keystrokes; a value-set or insertText leaves
  // aria-expanded=false and no listbox (the same lesson as Workday's prompt searchBox).
  const opensOn = (type === 'react_select' || type === 'month_year' ||
                   type === 'prompt_hierarchical') ? 'keystrokes' : 'click';

  // Commit: a footer button in the popup means selecting only STAGES the value. Absent =
  // applies on select. Only discoverable while open; the recipe's `commit` is authoritative
  // when we can't see one.
  let commitKind = 'on_select', commitLabel = null;
  if (popupEl) {
    const names = ['Update','Apply','Done','Save'];
    const b = [...popupEl.querySelectorAll('button')]
      .find(x => names.some(n => new RegExp('^' + n + '$', 'i').test(txt(x))));
    if (b) { commitKind = 'footer_button'; commitLabel = txt(b); }
  }

  return {
    found: true,
    widget_type: type,
    label: label.slice(0, 120),
    opener: opener ? {role: attr(opener, 'role') || opener.tagName.toLowerCase(),
                      aria_expanded: attr(opener, 'aria-expanded'),
                      accessible_name: (attr(opener, 'aria-label') || txt(opener)).slice(0, 80)} : null,
    popup: {ref: ref, source: ref ? (attr(opener, 'aria-controls') ? 'aria-controls' : 'aria-owns')
                                  : 'document',
            option_count: options ? options.length : null},
    options: options,
    options_enumerable_by: enumerableBy,
    commit: {kind: commitKind, label: commitLabel},
    value_read_at: readAt,
    opens_on: opensOn,
    required: required,
    required_via: requiredVia,
    disabled: disabled,
    answered: answered,
    value_preview: String(valuePreview || '').slice(0, 60),
    // Secondary node for the two-widget shapes, so the caller doesn't re-derive it.
    companion_selector: monthYear && yearNode ? ('#' + yearNode.id) : null,
  };
}
"""
