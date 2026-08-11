# SwiftChart Refined

An isolated, preservation-first SwiftChart mobile prototype.

It deliberately keeps the production visual identity and screen structure while refining typography, spacing, hierarchy, component consistency, trading semantics, state design, and motion.

## Run

```bash
python3 -m http.server 4176 -d prototypes/swiftchart-refined
```

Open `http://localhost:4176/?screen=home`.

Screens:

- `?screen=home`
- `?screen=scan`
- `?screen=detail`
- `?screen=active`
- `?screen=history`

Side-by-side review: `http://localhost:4176/compare.html?screen=home` (replace `home` with any screen key above).

The production Detail screenshot available in the repository contains a white render artifact. Its comparison therefore uses the current Home signal-card entry point and labels that limitation explicitly; the structural differences are documented in `COMPARISON.md`.

## Review artifacts

- `COMPARISON.md` — current production vs vNext preservation table.
- `MOTION_REVIEW.md` — Emil-style motion quality-control review.
- `PRODUCTION_INTEGRATION_PLAN.md` — read-only mapping from Refined changes to production files, risks, screenshots, and checks.
- `screenshots/refined/` — five 390×844 Refined screens.
- `screenshots/comparisons/` — five production-vs-Refined side-by-side sheets.

## Safety

- No production components or styles are modified.
- All data and actions are mocked locally.
- No API, authentication, persistence, or trading mutation occurs.
