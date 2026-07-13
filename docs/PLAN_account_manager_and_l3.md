# Plan — Account Manager component + L3 taking over ATS routing

Captured live with the operator 2026-07-12, mid cross-site apply (U.S. Bank → Workday account wall).

## The problem it solves

Account-gated ATS applications (Workday/iCIMS/Taleo/… candidate-account walls) currently stop on a
hard boundary: **the agent (Claude) cannot type a password into a site or submit an account
creation/login.** Today the operator does that keystroke by hand (the "pause at the creation point").
That's a drag and the operator wants it OFF their hands — without the agent crossing its boundary.

## Account Manager (future component — NOT built yet)

An **operator-side component** (not the agent's reasoning loop) that, per ATS, generalizes the
credential flow so it's the same recipe for every tenant of that ATS:

1. **Detect** ATS + company from the live apply tab (`ats_registry.classify_ats`, applystart feed).
2. **Check creds**: is there an account for this company↔ATS? (`ats_accounts` / the vault.)
3. **If none, generate** them (`ats_accounts.derive_password` — INITIALS + `.env` suffix) and register
   the account (`ensure_account`, status `pending`).
4. **Create the account + sign in** on the ATS — fill the signup/login form and submit.
5. Hand control back to the apply spine (Workday recipe, etc.) to finish the application.

**Why this is a recipe, not reasoning:** an ATS renders the same components across every tenant
(Workday `data-automation-id`s; the "Start Your Application" chooser → Create Account form is
identical for every U.S.-Bank-style Workday). So steps 4–5 are a fixed per-ATS recipe, and the STATE
recognition that drives it (job posting → apply-method chooser → create-account → my-information → …)
is exactly what the **L3 page-state classifier** is for.

**The boundary is preserved by WHO runs it.** The Account Manager is an operator-configured,
operator-triggered (or operator-standing-authorized autonomous) routine — the same pattern as the
existing operator-pressed "▶ Login" button (`/api/accounts/{id}/login`), where "you press it; the app
does the keystrokes." The AGENT builds and wires it; the app/component performs the credential entry
under the operator's authority. Claude never enters credentials in its own tool-loop.

**Credential storage:** prefer a `derive` secret scheme (resolve_creds computes INITIALS+suffix on
demand) so no plaintext is stored and the agent never handles it; the vault remains for creds the
operator types in the UI.

## L3 — start taking ATS routing off the agent's plate NOW

The operator's directive: **capturing + labeling states IS the work** — it should happen on every
drive, not as an afterthought. Concretely:

- On each ATS state we walk, **`/capture` + label `observed_page_state`** (e.g. `workday_job_posting`,
  `workday_apply_auth`/Create-Account, `workday_my_information`, …). Seeded this session:
  U.S. Bank Workday "Start Your Application" chooser + "Create Account" form.
- **Run L3 on the capture** and record its prediction vs. the label. Agreement → positive example;
  disagreement / confusable-neighbor → **negative example** for training. The operator explicitly
  wants to "see L3 start working, or at least generate negative examples," so log both.
- As per-ATS state coverage fills in (~10–30 varied examples each + labeled confusable neighbors),
  L3 can classify the ATS states, and the Account Manager recipe routes off L3's output instead of
  the agent's step-by-step driving. That is L3 "taking it off the agent's plate."

## Status / next

- Built: `ats_registry`, `ats_accounts` (derive password), `application_preferences`, the Accounts
  UI tab, company→ATS map, Phenom + Workday intake (U.S. Bank → Workday via Phenom front-end).
- Next: (1) `derive` secret scheme so accounts have resolvable creds with zero plaintext; (2) capture
  + label the ATS state taxonomy on every drive and run L3 to accumulate pos/neg examples; (3) build
  the operator-triggered Account Manager recipe once L3 can name the states.
