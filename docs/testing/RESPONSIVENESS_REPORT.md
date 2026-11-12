# Responsiveness and accessibility report

Final runs: 12 November 2026, on the final build (Vite 8.3.8), gateway with a real SUMO run
(environment: [test plan §1](TEST_PLAN.md#1-environment-all-reported-runs)).

## 1. Layout at every screen width

**Objective.** Every page works from a large desktop to a small phone: no horizontal page scroll,
nothing cut off, no errors.
**Procedure.** `node tests/e2e/responsive.mjs <url> <email> <password> [out dir]` opens each of the
18 signed-in pages at six widths. At each one it checks for horizontal overflow of the page and
for console or page errors, and saves a screenshot.

| Widths | 1920, 1440, 1024, 768, 390, 360 px |
|---|---|
| Pages | Live traffic, Live overview, Signals, VANET, Emergency, Navigation, Incidents, Work zone, Eco-driving, Events, Guided demo, Results, GA optimisation, Run history, Documentation, Run control, Administration, Account |

**Expected.** 108 of 108 combinations without overflow or errors.
**Actual.** **108 of 108**: no overflow, no console errors. **Status: pass.**

How the layout adapts:

- **Sidebar.** It collapses to icons on request; at 900 px and below it becomes a drawer opened with ☰.
- **Map.** It keeps its aspect ratio and stays usable with touch: drag to pan, buttons to zoom.
- **Wide tables.** They scroll sideways inside their own box (reachable by keyboard), so the page
  itself never scrolls sideways.
- **Content.** Card grids drop to one column on phones.

Defects found by this test in Phase 4–5 (fixed):

- a dropdown 6 px wider than the page at 390 px;
- mobile-only buttons visible on desktop.

Screenshots of every page at every width were reviewed by hand in Phases 4–8.

## 2. Accessibility (WCAG 2.2 AA, automated)

**Objective.** No violations of the WCAG 2.0, 2.1 and 2.2 level A and AA rules that axe-core checks.
**Procedure.** `node tests/e2e/a11y.mjs <url> <email> <password> [out.json]` runs axe-core 4.14 on:

- the 18 signed-in pages in the dark and the light theme (36 audits);
- the sign-in, forgot-password and reset pages in both themes (6);
- three pages at 390 px (3).

`tests/e2e/a11y-ambulance.mjs` adds the live map while an ambulance has priority, in both themes.

**Expected.** 0 violations.
**Actual** ([`data/final-a11y.json`](data/final-a11y.json)):

- **0 violations in 45 audits**;
- **0 violations** in both ambulance-priority audits.

**Status.** Pass.

Defects found and fixed:

| Phase | Rule | Where | Fix |
|---|---|---|---|
| 8 | colour contrast | white on the blue buttons, dark theme (3.98 : 1) | accent `#0b72b0` (5.19 : 1) |
| 8 | label / name mismatch | account button on phones showed only an initial | the name stays available to screen readers |
| 8 | ARIA on plain elements | queue bars, map layer groups | `role="img"` / `role="group"` |
| 8 | scrollable region not keyboard-reachable | formula boxes, wide tables on phones, the event list | formulas wrap; regions focusable |
| 10 | label / name mismatch | a junction during ambulance priority showed *AMBULANCE*, its name did not | note drawn outside the button; the name says "ambulance priority" |

The Phase 10 defect appeared only when the audit happened to run during an ambulance's green. The
first full audit of the final round caught it; earlier audits had not happened to hit that moment.
A dedicated test now creates that state on purpose.

## 3. Keyboard operation

Checked by `a11y.mjs` on the final build:

| Check | Result |
|---|---|
| "Skip to content" is the first Tab stop and moves focus to the main content | yes |
| All 18 navigation links reachable by Tab | 18 / 18 |
| Every focus stop has a visible focus ring | 0 without |
| Map: zoom (`+` `-`), pan (arrows), reset (`0`) from the keyboard | yes |
| Junctions selectable with Enter / Space | yes (focus ring added in Phase 8) |

## 4. Limitations

- Automated rules find only part of the possible accessibility problems. No test with a real
  screen reader (NVDA, JAWS, VoiceOver) and no test with users was done.
- The moving cars on the map are not announced to screen readers one by one, by design: the
  junction cards, the tables and the followed car's panel carry the same information as text.
- Only Edge (Chromium) was used; layout in Firefox and Safari was not tested.
