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

from app.js_common import WIDGET_TELLS_JS

# The classifier. Returns the widget's own account of itself.
#
# ORDER MATTERS: checks run most-specific-first. A Greenhouse month field is BOTH a
# react-select and part of a month_year pair; a Workday date section is a spinbutton that
# would otherwise read as `number`. Specific shapes must claim their nodes before the
# generic ones do.
DESCRIBE_WIDGET_JS = r"""
(cfg) => {
  __WIDGET_TELLS__
  const vis = __vis, txt = __txt, attr = __attr;

  // FRAME-AWARE TARGET LOOKUP. A page is not its top document: iCIMS renders its whole apply flow
  // inside `icims_content_iframe`, and every layer here had to learn this separately — the act-time
  // resolver, the census, the captcha rail, the native-select protocol, and now the classifier and
  // the popup protocols. `__findAll` is the one definition (app/js_common.py).
  const el = __findAll(cfg.selector)[0] || null;
  if (!el) return {found: false, detail: 'no node matching ' + cfg.selector};

  // The field's WRAPPER — react-select and Workday both render the real control as a
  // sibling/descendant of a container, so classification has to look around the node, not
  // just at it.
  const wrap = el.closest('[data-automation-id], .select, [class*=select__control], ' +
                          '[class*=field], fieldset, [role=group], li, div') || el.parentElement || el;

  // ---- SHAPE TELLS ------------------------------------------------------------------
  // react-select — via the SHARED tell (app/js_common.py), not a local copy. A local copy
  // is exactly how this block and SCAN_REQUIRED_JS came to disagree about what a
  // react-select even is, an hour apart, in one session.
  const reactSelect = __isReactSelect(el);

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

  // ---- ANSWERED, AND WHERE THE TRUTH LIVES -------------------------------------------
  // ASK THE SHARED TELL, never a local copy. `__valueTruth` (app/js_common.py) is the one
  // function that owns the `.value` lie, and every shape of it that has cost us a live drive
  // is already answered in there: react-select's transient search text, iCIMS selects whose
  // real answer sits at option 0, Workday's multiselect pills, an ARIA opener showing its
  // placeholder, and the hidden native <select> a react-select fronts.
  //
  // This block used to hand-roll all of that, and it drifted — which is the whole reason
  // js_common.py exists. Measured live 2026-08-14 on BrassRing (Boston Children's, Questions
  // step, `#custom_10112_465_fname_slt_0_10112-input`): `/scan_required` read the field
  // ANSWERED "LinkedIn" at `companion_select`, and `/describe_widget` read the SAME node
  // `answered: false` at `[class*=singleValue]`, because only the census resolved the hidden
  // native twin. A false "unanswered" invites a retry, and a retry on a stateful widget is not
  // idempotent — see docs/LEARNINGS.md 2026-08-13, the react-select retry that set the wrong
  // question. `answered` must not depend on which endpoint asked.
  //
  // The GROUP shapes stay here because __valueTruth models a single control and these are
  // several: a checkbox group, a radio group, and Workday's linked date sections are answered
  // by their members, not by the node the caller handed us.
  const GROUP_READ_AT = {radio_group: 'aria-checked', checkbox_group: 'checked',
                         segmented_date: 'aria-valuenow'};
  let answered = false, valuePreview = '', readAt = GROUP_READ_AT[type] || '';
  try {
    if (type === 'checkbox_group') {
      const n = cbs.filter(c => c.checked).length; answered = n > 0; valuePreview = n + ' checked';
    } else if (type === 'radio_group') {
      const c = radios.find(r => r.checked || attr(r, 'aria-checked') === 'true');
      answered = !!c; valuePreview = c ? txt(c.closest('label')) : '';
    } else if (type === 'segmented_date') {
      const secs = [...wrap.querySelectorAll('[data-automation-id*=dateSection]')];
      const vals = secs.map(s => attr(s, 'aria-valuenow') || s.value || '').filter(Boolean);
      answered = vals.length > 0 && vals.length === secs.length; valuePreview = vals.join('/');
    } else {
      const truth = __valueTruth(el);
      answered = truth.answered; valuePreview = truth.preview; readAt = truth.read_at;
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

  // The SECOND NODE, when this widget is really two. Two unrelated shapes wear that name and
  // both are worth handing over: the year input of a month/year pair, and the hidden native
  // <select> a react-select fronts. Named by the SHARED resolver, so the node reported here is
  // the same node `answered` was read from — a companion_selector found by a second lookup
  // could point somewhere the value never came from.
  let companionSel = null;
  if (monthYear && yearNode) companionSel = '#' + yearNode.id;
  else if (reactSelect) {
    const comp = __companionSelect(el);
    if (comp && comp.node && comp.node.id) {
      try { companionSel = '#' + CSS.escape(comp.node.id); } catch (e) { companionSel = null; }
    }
  }

  return {
    found: true,
    widget_type: type,
    // The raw tag rides along for the DIALECT layer's structural filter: which protocols are
    // even possible for this node (a bare <select> can never be a react-select) is a cheaper
    // and steadier fact than the full classification above it.
    tag: el.tagName.toLowerCase(),
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
    companion_selector: companionSel,
  };
}
"""

# See protocols.py for why this is a placeholder-replace and not an f-string.
DESCRIBE_WIDGET_JS = DESCRIBE_WIDGET_JS.replace("__WIDGET_TELLS__", WIDGET_TELLS_JS)
assert "__WIDGET_TELLS__" not in DESCRIBE_WIDGET_JS, "the tells placeholder did not substitute"
