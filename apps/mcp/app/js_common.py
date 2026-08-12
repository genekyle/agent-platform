"""Page-side tells, defined ONCE and injected into every JS block that needs them.

Why this file exists — and it is not a hypothetical:

`DESCRIBE_WIDGET_JS` and `SCAN_REQUIRED_JS` were written an hour apart in the same session
and disagreed about how to recognise a react-select. describe_widget checked
`[class*=select__control]` (right); scan_required checked `[class*=singleValue]` (wrong).
That is the identical failure the shared `interaction` package exists to prevent — the
autofill matcher drifting from its Python twin — reproduced inside one session, in one app,
by one author. Copies drift. These are the tells; they live here.

THE TELL THAT MATTERS. `[class*=singleValue]` DOES NOT EXIST until the widget is answered —
react-select renders a placeholder when empty and only mounts singleValue once a value is
picked. So detecting a react-select by singleValue works on exactly the fields you don't
care about and fails on exactly the fields you do (the unanswered ones). Verified live on
KKR's Greenhouse form 2026-07-16:

    #country / #school--0 / #degree--0 (all unanswered):
        singleValue=False  select__control=True  aria-autocomplete=list  role=combobox
    #first_name / #start-date-year-0 (plain inputs):
        singleValue=False  select__control=False aria-autocomplete=None  role=None

That mattered for more than tidiness. `scan_required` fell through to `.value` for every
react-select it returned, and **react-select's `.value` holds TRANSIENT SEARCH TEXT** — so a
half-typed field reads as ANSWERED, drops out of the unanswered list, and the form-complete
gate passes an incomplete form. It was latent only because nothing had typed into those
fields yet.
"""

from __future__ import annotations

#: Prepend to the body of any page-side arrow function. Defines:
#:   __vis(el)        — is it really on screen (offsetParent + a non-zero rect)
#:   __txt(el)        — normalized visible text
#:   __isReactSelect(el)
#:   __valueTruth(el) — {read_at, answered, preview}: WHERE this widget's truth lives and
#:                      what it currently says. The one function that owns the `.value` lie.
#:   __questionOf(el) — {question, source, section}: WHICH QUESTION this control answers, and how
#:                      strong that identity is. The correlation an address is a prediction about.
#:   __sameQuestion(a, b) — do two names refer to one question (truncation- and marker-tolerant)
WIDGET_TELLS_JS = r"""
  const __vis = (n) => { try { const r = n.getBoundingClientRect();
                               return n.offsetParent !== null && r.width > 0 && r.height > 0; }
                         catch (e) { return false; } };
  const __txt = (n) => ((n && (n.innerText || n.textContent)) || '').replace(/\s+/g, ' ').trim();
  const __attr = (n, a) => (n && n.getAttribute ? n.getAttribute(a) : null);

  // A control a HUMAN could actually fill: on screen AND keyboard-reachable.
  //
  // `tabIndex === -1` is the tell, and it is doing real work: Greenhouse's react-select
  // mounts a hidden proxy input (`class*=requiredInput`, tabIndex=-1, opacity:0) purely so
  // native form validation fires. It is `required`, it is unanswered, and __vis() says it is
  // VISIBLE — offsetParent is non-null and it has a 608x22 rect, because it is hidden with
  // OPACITY, not display:none. So it sails through a rect check and shows up as a phantom
  // required question. This is the "hidden required twin" GREENHOUSE_LESSONS warns about
  // ("a duplicate empty-id field is NOT a second question").
  //
  // Do NOT reach for Element.checkVisibility({opacityProperty:true}) here, which looks like
  // the obvious fix. Measured live on KKR 2026-07-16: it rejects the proxy (good) AND
  // rejects #country and #school--0 (fatal) — react-select's own search input is opacity:0
  // whenever the singleValue is showing, so an opacity-aware check drops every react-select
  // from the scan. Opacity does not separate them. tabIndex does: 1 proxy at -1, all 29 real
  // fields at 0.
  const __isUserField = (el) => __vis(el) && el.tabIndex !== -1;

  // The page's own verdict on a value it already holds. A required field can be FILLED and
  // still block: form_complete_gate treats `satisfied = (not required) or (filled and valid)`,
  // so "filled but invalid" is a distinct blocker from "empty" and must not be lost.
  const __invalid = (el) => {
    if (!el) return false;
    if (__attr(el, 'aria-invalid') === 'true') return true;
    try { return typeof el.matches === 'function' && el.matches(':invalid'); }
    catch (e) { return false; }
  };

  // WHICH QUESTION DOES THIS CONTROL ANSWER? The correlation the whole system runs on, and the
  // one nobody was checking: an address (selector, role+name, node id) is a PREDICTION about
  // which question a control answers, and until something reads the control's OWN question there
  // is nothing to compare the prediction against. Workday's applyManually renders two file inputs
  // that are identical in every attribute — same automation-id, same ancestor, both hidden — and
  // differ ONLY in the question above them ("Resume/CV" vs the Attachments box). The resolver
  // matched both, took the first, and reported a clean `ok` three times while the required
  // uploader stayed empty (live 2026-08-12, SolutionHealth JR11587).
  //
  // Requiredness is IRRELEVANT here. An optional control filled with the wrong answer is the same
  // error as a required one, and the census's required-only view is exactly what made the
  // correlation invisible. So this answers for any control, and says WHERE the answer came from —
  // `source` is the provenance that lets a weak identity (a section heading shared by five fields)
  // be treated differently from a strong one (a <label for>).
  //
  // ONE definition, used by both the census (which builds the address book) and the act-time
  // resolver (which confirms the target). Two functions here would be two answers to one question,
  // and the address book would drift from the action — the same class of bug as two looks at one
  // page reading different pages.
  const __questionOf = (el) => {
    if (!el) return {question: '', source: 'none', section: ''};
    // The section that CONTAINS the control: the nearest heading PRECEDING it in document order.
    //
    // Not `ancestor.querySelector('h1,…')`, which was the first cut and is subtly wrong: that
    // searches the ancestor's whole subtree and returns the first heading in it, including headings
    // belonging to SIBLING fields that happen to come earlier. It filed a Workday uploader under
    // "Issued Date" — a neighbouring date field's label — which is worse than no section at all,
    // because a wrong scope confidently addresses the wrong control (live 2026-08-12).
    //
    // Reading BACKWARDS is how a person decides which section they are in: look up the page until
    // you hit a heading. Bounded, because an unbounded walk reaches <body>, whose "section" is the
    // whole page and distinguishes nothing.
    // A <legend> is DEMOTED below a real heading: it labels a fieldset, and a fieldset is often a
    // sub-group inside a section — Workday wraps each date in one, so the nearest "heading" behind
    // an uploader was "Expiration Date" while the section it lives in is several lines further up
    // (live 2026-08-12). Headings first across the whole backward walk; a legend only answers when
    // no heading does.
    let section = '', legend = '';
    const lastIn = (n, sel) => {
      if (!n || !n.matches) return '';
      if (n.matches(sel)) return __txt(n);
      const hs = n.querySelectorAll ? n.querySelectorAll(sel) : [];
      return hs.length ? __txt(hs[hs.length - 1]) : '';   // the LAST one is the closest preceding
    };
    const HEADING = 'h1,h2,h3,h4,h5,[role=heading]';
    for (let n = el, d = 0; n && n !== document.body && d < 12 && !section; d++, n = n.parentElement) {
      for (let sib = n.previousElementSibling; sib && !section; sib = sib.previousElementSibling) {
        const t = lastIn(sib, HEADING);
        if (t && t.length <= 120) section = t;
        if (!legend) { const g = lastIn(sib, 'legend'); if (g && g.length <= 120) legend = g; }
      }
    }
    section = section || legend;
    // Every named candidate is filtered through __isBoilerplate BEFORE it is returned. An
    // association is only worth having if it identifies the QUESTION: Workday's option dropdowns
    // carry `aria-label="Select One Required"`, a perfectly valid ARIA label that says nothing about
    // what is being asked, and taking it made three different required questions one indistinct row
    // (live 2026-08-12). A worthless name must not outrank a real one further down the chain.
    const strong = (q, src) => ({question: (q || '').slice(0, 240), source: src, section});
    const named = [];
    if (el.id) {
      try { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
            if (l) named.push([__txt(l), 'label-for']); } catch (e) { /* bad id */ }
    }
    named.push([__attr(el, 'aria-label'), 'aria-label']);
    const ref = __attr(el, 'aria-labelledby');
    if (ref) {
      const parts = ref.split(/\s+/).map(id => { const n = document.getElementById(id); return n ? __txt(n) : ''; })
                       .filter(Boolean);
      if (parts.length) named.push([parts.join(' '), 'aria-labelledby']);
    }
    const own = el.closest && el.closest('label');
    if (own) named.push([__txt(own), 'own-label']);
    // A candidate that merely repeats the control's CURRENT VALUE is not an identity. An option
    // opener's accessible name BECOMES the chosen value ("No Required" after answering No), so a
    // valid aria-label described the answer and outranked the real question — three different
    // questions all identified as their own answers (live 2026-08-12). No page labels a field with
    // its own contents, so name == value is decisive.
    const __valNorm = (s) => (s || '').toLowerCase().replace(/\s*required\s*$/i, '')
                                      .replace(/[^a-z0-9]+/g, ' ').trim();
    const nowValue = __valNorm((__valueTruth(el) || {}).preview);
    for (const [text, src] of named) {
      if (!text || __isBoilerplate(text)) continue;
      if (nowValue && __valNorm(text) === nowValue) continue;      // that is the answer, not the ask
      return strong(text, src);
    }
    // PROXIMITY — the nearest preceding short text, which is how a sighted user reads a box with
    // no association: by looking just above it. A paragraph is not a label, so prose ends the walk.
    //
    // Text that belongs to the widget itself, or to a NEIGHBOURING field, is not this control's
    // question. Workday's date sub-fields announce themselves ("Expiration Date current value is
    // MM/DD/YYYY") and were duly adopted as an uploader's label by an earlier cut of this walk.
    // A widget's own PLACEHOLDER is not a question. Workday renders every option dropdown with the
    // accessible name "Select One Required", so three different yes/no questions on one screen —
    // "have you worked here before", "are you legally permitted to work", "have you been convicted
    // of a crime" — arrived as three identical rows the census could not tell apart, which is the
    // wrong-target bug in its most dangerous form (live 2026-08-12). Boilerplate is furniture: skip
    // it and read the question the field actually asks.
    const FURNITURE = /^(or|drop files here|select files?|browse|drag and drop|choose file|select one|please select|choose one|select\.\.\.|select…|select an option|-- ?select)/i;
    const NEIGHBOUR = /current value is|^MM$|^DD$|^YYYY$/i;

    // WHAT DOES THIS FIELD SAY, APART FROM THE WIDGET ITSELF? For a composite widget the label is
    // a SIBLING OF THE WIDGET INSIDE THE FIELD WRAPPER, not a previous sibling of anything on the
    // control's own ancestor line — Workday nests
    //     div[formField-] > "Upload a file (5MB max)*" + div[FileUpload] > … > input[type=file]
    // so a walk that only looks at previous siblings climbs straight past the label and lands in
    // the PREVIOUS SECTION ("Skills Type to Add Skills 0 items selected", live 2026-08-12). And a
    // walk that starts at the input reads the widget's own furniture ("Drop files here or Select
    // files"). Subtracting the part we came from leaves exactly the field's own words, which is
    // what a person reads.
    // The length ceiling here is deliberately GENEROUS (a question is long — "Have you previously
    // worked for SolutionHealth, Elliot Health, Southern New Hampshire Health, or Home, Health and
    // Hospice?" is 118 characters) because what remains after the subtraction is the field's own
    // words BY CONSTRUCTION, not a guess that might have wandered into prose. The 60-character cap
    // belongs to the sibling walk below, which has no such guarantee.
    // STOP AT THIS FIELD'S OWN WRAPPER. The subtraction is only "the field's own words" while the
    // ancestor holds ONE control; one level higher it holds the NEIGHBOUR too, and what is left over
    // is the neighbour's question. Measured live 2026-08-12: the "family members employed here"
    // textarea reported the SALARY question above it, and every answered dropdown reported its own
    // answer ("No Required") because the walk had already left the field. Same rule as the
    // uploader's container walk, generalised from "a second file input" to "a second control" —
    // a form field is the largest box around this control that contains no other.
    const CONTROLS = 'input:not([type=hidden]), select, textarea, [role=combobox],' +
                     '[role=listbox], [aria-haspopup=listbox], [role=radiogroup], button';
    // A SIBLING QUESTION, not a sibling NODE. Composite widgets ship their own private controls —
    // Workday pairs each opener with an off-screen proxy input — and counting those as neighbours
    // stopped the walk at level 0, one level below the question text (measured live 2026-08-12).
    // `__isUserField` is the same distinction the census uses for the hidden-required-twin trap: a
    // control a human cannot reach is part of a widget, not a question of its own.
    let start = el;
    for (let n = el.parentElement, d = 0; n && n !== document.body && d < 6; d++, n = n.parentElement) {
      const whole = __txt(n);
      if (whole.length > 400) break;             // n now spans the section, not the field
      let others = 0;
      for (const c of n.querySelectorAll(CONTROLS)) {
        if (c === el || el.contains(c) || c.contains(el)) continue;
        if (__isUserField(c)) others++;
      }
      if (others) break;                         // n has reached a sibling field — do not read it
      const inner = __txt(start);
      const own = (inner && whole.startsWith(inner) ? whole.slice(inner.length)
                                                    : whole.replace(inner, '')).trim();
      if (own && own.length >= 2 && own.length <= 240 && !FURNITURE.test(own) && !NEIGHBOUR.test(own))
        return strong(own, 'proximity');
      start = n;
    }
    for (let hop = start, i = 0; i < 5 && hop; i++, hop = hop.parentElement) {
      for (let sib = hop.previousElementSibling; sib; sib = sib.previousElementSibling) {
        const t = __txt(sib);
        if (NEIGHBOUR.test(t)) break;
        if (t && FURNITURE.test(t)) continue;
        if (t && t.length >= 2 && t.length <= 60) return strong(t, 'proximity');
        if (t && t.length > 60) break;
      }
    }
    // The SECTION alone is a weak identity — it names a group, not a question. Handed back anyway,
    // flagged, because "this control is somewhere under Resume/CV" is exactly enough to tell two
    // otherwise-identical uploaders apart, and refusing to say it would leave the caller blind.
    if (section) return {question: section.slice(0, 120), source: 'section-only', section};
    const aid = __attr(el, 'data-automation-id');
    // A vendor's internal id is the LAST resort and never a question: Workday gives every file
    // input `file-upload-input-ref`, so this identity is shared by controls that answer different
    // questions. Flagged as such so a caller can refuse to act on it.
    if (aid) return {question: aid.slice(0, 120), source: 'automation-id', section};
    return {question: '', source: 'none', section};
  };

  //: Is this "name" just the widget's own placeholder? A control called "Select One Required" or
  //: "Drop files here" has told us what KIND of widget it is and nothing about what it asks — and a
  //: name that identifies nothing is worse than no name, because it looks like an identity and
  //: collides with every sibling that shares it. Exported so the census and the resolver agree on
  //: which names are worthless.
  const __isBoilerplate = (s) => {
    const t = (s || '').trim();
    if (!t || t === '(unlabeled)') return true;
    return /^(select one|please select|choose one|select\.\.\.|select…|select an option|--? ?select|drop files here|select files?|browse|choose file)\b/i
             .test(t.replace(/\s*required\s*$/i, '').trim());
  };

  //: Do these two names refer to the same question? Compared the way a person would: ignore the
  //: required marker, punctuation and case, and accept a TRUNCATION either way (the census cuts
  //: names at ~90 chars, so an exact match would reject the very rows it produced).
  const __sameQuestion = (a, b) => {
    const norm = (s) => (s || '').toLowerCase().replace(/[*:]+/g, ' ')
                                 .replace(/[^a-z0-9]+/g, ' ').trim();
    const x = norm(a), y = norm(b);
    if (!x || !y) return false;
    if (x === y) return true;
    const short = x.length <= y.length ? x : y, long = x.length <= y.length ? y : x;
    return short.length >= 4 && long.includes(short);
  };

  // A react-select, whether or not it has been answered. Order matters: select__control is
  // the structural tell and is present when empty; aria-autocomplete=list is the ARIA tell
  // and catches skinned variants that rename the class.
  const __isReactSelect = (el) => {
    if (!el) return false;
    if (el.closest('[class*=select__control], [class*=select__value-container]')) return true;
    return __attr(el, 'aria-autocomplete') === 'list' && __attr(el, 'role') === 'combobox';
  };

  // WHERE this widget's truth lives, and what it says right now.
  // NEVER read a react-select at .value: that holds the transient search text, empties on
  // blur, and reports a half-typed field as answered.
  const __valueTruth = (el) => {
    if (!el) return {read_at: '', answered: false, preview: ''};
    if (el.tagName === 'INPUT' && el.type === 'file')
      return {read_at: 'files.length', answered: !!(el.files && el.files.length),
              preview: (el.files && el.files.length) ? el.files[0].name : ''};
    if (__isReactSelect(el)) {
      // Scope to the widget's OWN control, not an ancestor that may wrap several fields.
      const ctl = el.closest('[class*=select__control]') ||
                  el.closest('[class*=field], div') || el.parentElement;
      const sv = ctl && ctl.querySelector('[class*=singleValue]');
      const multi = ctl && ctl.querySelectorAll('[class*=multiValue]');
      const t = sv ? __txt(sv) : (multi && multi.length ? __txt(ctl) : '');
      return {read_at: '[class*=singleValue]', answered: !!t, preview: t.slice(0, 40)};
    }
    if (el.tagName === 'SELECT')
      return {read_at: '.value', answered: el.selectedIndex > 0 && el.value !== '',
              preview: (el.value || '').slice(0, 40)};
    // WORKDAY'S MULTISELECT — the third shape of the `.value` lie, same family as react-select.
    // The node the scan holds is a SEARCH box inside `multiselectInputContainer`; the answer is
    // the selected-item pill beside it, and the container's own aria-label states it outright
    // ("1 item selected, United States of America (+1)"). Reading `.value` called an answered
    // Country Phone Code unanswered and blocked a Continue over a field the page considered
    // done (live 2026-08-11, SolutionHealth's Workday). The container's label is the truth the
    // widget publishes about itself, so that is what we read.
    const __ms = el.closest ? el.closest('[data-automation-id=multiselectInputContainer]') : null;
    if (__ms) {
      const aria = __attr(__ms, 'aria-label') || '';
      const m = aria.match(/^\s*(\d+)\s+items?\s+selected\s*,?\s*(.*)$/i);
      const chosen = m ? (m[2] || '').trim() : '';
      const n = m ? parseInt(m[1], 10) : 0;
      // Fall back to the pills when the container states no count (older tenants).
      const pill = chosen || __txt(__ms.querySelector('[data-automation-id*=selectedItem]') || null);
      return {read_at: 'multiselect_selected_items', answered: n > 0 || !!pill,
              preview: (pill || '').slice(0, 40)};
    }
    // An ARIA combobox/listbox OPENER — a div with no .value anywhere. Its truth is its own
    // visible label (the widget protocol's `opener_label`). The generic fallback below read
    // el.value (undefined on a div), so smartapply's question dropdowns scanned as unanswered
    // FOREVER, even freshly committed (live, 2026-08-10). Placeholder text still counts as
    // UNANSWERED on purpose: the dangerous mistake is a placeholder passing form_complete_gate,
    // not a filled field being asked about twice.
    const __role = el.getAttribute ? (el.getAttribute('role') || '') : '';
    if (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA' &&
        (__role === 'combobox' ||
         (el.getAttribute && el.getAttribute('aria-haspopup') === 'listbox'))) {
      const t = __txt(el).trim();
      const ph = /^(select|choose)( an?| your)? (option|answer|one)\b/i.test(t) ||
                 /^(select|choose)(\.{3}|…)?$/i.test(t);
      return {read_at: 'opener_label', answered: !!t && !ph, preview: t.slice(0, 40)};
    }
    const v = String(el.value == null ? '' : el.value);
    return {read_at: '.value', answered: !!v.trim(), preview: v.slice(0, 40)};
  };
"""
