# SwiftChart Refined — production integration plan

Status: planning only. Do not deploy or modify production from this document.

## Scope guardrails

The integration is limited to presentation and interaction quality. It must not change trading logic, API calls or response normalization, backend behavior, authentication/redirect behavior, signal calculations, route/path behavior, or the current Crypto/Forex market-selection behavior.

The production data flow in `MobileDemo.jsx` stays authoritative. In particular, keep `normalizeIdea`, `normalizePendingSetup`, `normalizeActiveCryptoTrade`, `normalizeForexSignal`, `activeForexSignals`, `filterForexSignalsByTimeframe`, `executionLadderState`, `scanForexNow`, `saveTradeToHistory`, and every API call unchanged.

## Change map

| Refined change | Classification | Existing production target | Integration detail |
|---|---|---|---|
| Tighten the graphite type scale | visual-only | `global.css`: shared graphite labels, headings, body copy, numeric values | Keep Inter/system font and current weights; normalize label, title, body, metadata, and tabular-number sizes. No font package. |
| Normalize screen spacing and mobile density | visual-only | `.graphite-content`, `.graphite-screen`, `@media (max-width: 380px)`, compact/large-text overrides | Use the Refined 10–14px outer gutter, 8–13px vertical rhythm, and consistent 40–52px control targets. Preserve safe-area padding and scrolling container. |
| Standardize graphite card surfaces | visual-only | shared card selector containing hero, regime, scan, history, state, reasoning, ladder and detail classes | Keep `#050607`, `#101214`, teal/profit/loss tokens and current rounded glass language; standardize radius, border, inset highlight and shadow strength. |
| Align the wordmark, market toggle and alert control | visual-only | `GraphiteHeader`, `MarketToggle`; `.graphite-header`, `.graphite-brand`, `.graphite-market-toggle`, `.graphite-alert-button` | Keep the same controls, labels, handlers and visibility rules. Equalize heights, tighten gaps and improve selected-toggle contrast. |
| Improve market-toggle tap feedback | motion change | `MarketToggle`; toggle button CSS | Add 120ms press feedback and 160ms color/background response. Do not change `setActiveMarket`, URL parameters, local storage or Forex loading effects. |
| Refine Crypto Market Summary and Regime hierarchy | visual-only | `HomeScreen`; `.graphite-hero-card`, `.graphite-hero-copy`, `.graphite-chart`, `.graphite-regime-card` | Preserve Summary → Regime → signal sections and dynamic best-signal/bias copy. Reduce competing emphasis and align content to the Refined proportions. |
| Preserve and polish the Forex session Home | visual-only | `ForexHomeScreen`, `ForexSignalGroups`; `.graphite-hero-card.forex`, `.graphite-session-stack`, Forex group styles | Preserve session clock, overview message, status grouping and limit opportunities. Apply the same spacing/card refinements without converting Forex into the Crypto layout. |
| Improve signal-card scanability | component refactor | `SignalRow`, `ScoreBadge`, `Direction`, `statusForSignal`; `.graphite-row` family | Keep the same signal object and selection callback. Reorder only visual slots if needed, standardize the score capsule, direction color and status column, and remove the redundant chevron on dense mobile rows if usability review confirms it. |
| Clarify active trades inside Home | component refactor | `HomeScreen` active-trades section and existing `SignalRow` | Keep Active Trades inside Home; do not add a route or fifth nav item. Add a presentation variant to `SignalRow` for lifecycle/result emphasis using already-normalized fields. |
| Refine Scan hero, search and filters | visual-only | both branches of `ScanScreen`; `.graphite-scan-card`, `.graphite-search`, `.graphite-filters` | Preserve form submission, selectors, timeframe values, exchanges, cooldown and scan handlers. Improve alignment, density, focus treatment and control consistency only. |
| Unify loading/error/empty/cached/result presentation | new reusable component | replace `EmptyState` usage and present existing `scanState`, `forexFeedState`, `forexScanResult`, `forexScanError` consistently | Add `GraphiteStateCard`; it receives already-computed state and callbacks. It must not fetch, retry automatically, interpret API errors or create new trading states. Remove the duplicated Forex loading card currently rendered twice. |
| Expose Crypto scan progress/state more clearly | state/interaction change | local `scanState` inside `ScanScreen` | Present the existing `idle/scanning/done/error` state with `aria-live`, disabled CTA text and deterministic status copy. Do not add timers, simulate percentage progress or alter `onCryptoScan`. |
| Clarify Forex scan outcomes | component refactor | Forex branch of `ScanScreen`, `forex-manual-result` | Reuse `GraphiteStateCard` framing around existing `NO_TRADE`, `FAILED`, created/reused, cached and error data. Keep authenticated scan, cooldown, filters and retry behavior intact. |
| Refine Signal Detail sheet | visual-only | `TradeAnalysis`; `.graphite-detail*`, `.graphite-trade-meta`, `.graphite-context-pills`, `.graphite-reasoning`, action styles | Keep the bottom sheet, score ring, fields, reasoning generation, Save/Take Trade behavior and terminal-state disabling. Improve hierarchy, sticky-action separation and 390px density. |
| Present the execution ladder as “Trade Plan” without changing calculations | component refactor | `ExecutionLadder` and its CSS; `executionLadderState` remains untouched | Change the display label and add a visible TP2 value/current-price slot from existing signal/progress data. Preserve marker/target positions and all LONG/SHORT calculations. If TP2 is absent, omit that visual cell rather than infer a value. |
| Move the live ladder marker efficiently | motion change | `.graphite-ladder-rail i.current-price` | Replace the `left 450ms ease` transition with a transform-based 180–220ms strong ease-out implementation, while retaining exact computed positions and the reduced-motion override. |
| Improve History density and empty state | visual-only | `HistoryScreen`; `.graphite-history-hero`, `.graphite-filter-row`, `.graphite-timeline`, `.graphite-history-row` | Keep totals, market/status filtering and selection behavior. Tighten the hero and row layout, improve result-tone legibility and render the unified state card when no rows match. |
| Tighten bottom-navigation motion | motion change | `.graphite-nav::before`, nav button/icon styles | Preserve four destinations, active index and `selectTab`. Reduce the highlight to about 220ms, remove active-icon scale, and keep focus/reduced-motion behavior. |
| Add consistent press/focus feedback | motion change | signal rows, primary/secondary buttons, toggle, filters and nav | Use transform/color/opacity only, approximately 120–160ms. No hover-only meaning, ambient loops or `transition: all`. |
| Use one restrained screen entrance | motion change | `.graphite-screen` and existing `graphiteRise` animation | Replace per-card repeated rise animations with one 180ms, 4px screen entrance. Respect OS reduced motion and the existing `preferences.appearance.reduceAnimations` class. |
| Preserve responsive accessibility preferences | component refactor | root class composition and compact/large-text/reduce-animation CSS overrides | Reconcile Refined spacing with existing compact layout, large text and reduced animations. Do not rename preference keys or alter `useAppPreferences`. |

## Exact production files

### Files that would change

1. `frontend/src/components/MobileDemo.jsx`
   - Import `GraphiteStateCard`.
   - Keep all normalization, API, auth, routing, persistence and calculation functions unchanged.
   - Give `SignalRow` a presentation-only variant for active lifecycle information.
   - Replace current `EmptyState` and scattered scan-state markup with `GraphiteStateCard` while passing the same messages/actions/state values.
   - Remove the duplicate `Loading Forex signals` render.
   - Keep the Crypto and Forex render branches separate and intact.
   - Update `ExecutionLadder` markup to show Trade Plan, current price, entry, stop, TP1 and optional TP2 using existing values.
   - Keep Active Trades as a Home section; no new tab, path or navigation handler.
   - Add only ARIA/state annotations needed by the presentation (`aria-live`, `aria-busy`, current labels).

2. `frontend/src/styles/global.css`
   - Refine existing graphite variables and component selectors; do not introduce a new theme or brand palette.
   - Normalize typography, spacing, card borders/radii/shadows, control heights and mobile breakpoints.
   - Add styles for `GraphiteStateCard` variants.
   - Refine Crypto Home, Forex Home, Scan, signal rows, Trade Analysis, History and bottom navigation in their existing selectors.
   - Replace per-card rise motion and the layout-position ladder transition with the approved subtle motion values.
   - Preserve and verify `.is-light`, `.is-compact-layout`, `.has-large-text`, `.reduce-animations` and `prefers-reduced-motion` overrides.

3. `frontend/src/lib/mobileOnly.test.js`
   - Keep the existing routing/deployment safety tests.
   - Add source-contract assertions that the nav remains Home/Scan/History/Account, Active Trades remains inside Home, the Crypto and Forex branches remain present, and `GraphiteStateCard` does not replace existing handlers.
   - Add a regression assertion preventing the duplicate Forex loading-state render.

### New files

4. `frontend/src/components/GraphiteStateCard.jsx`
   - New reusable presentation component.
   - Proposed props: `tone`, `eyebrow`, `title`, `message`, `meta`, `progress`, `actionLabel`, `onAction`, `secondaryAction`, `busy`, and `live`.
   - No data fetching, timers, API interpretation, navigation or trading decisions.
   - Covers loading, error, cached, empty, wait/no-trade and result framing using existing production state.

5. `frontend/src/lib/refinedUiContracts.test.js`
   - Native `node:test` source-contract checks for the new state component, four-item navigation invariant, separate Crypto/Forex branches, reduced-motion support and prohibited motion patterns such as `transition: all`.
   - No DOM runner or browser-test dependency.

### Files explicitly not changed

- `frontend/src/App.jsx`
- `frontend/src/lib/api.js`
- `frontend/src/lib/markets.js`
- `frontend/src/lib/forexSignals.js`
- `frontend/src/lib/executionLadder.js`
- `frontend/src/lib/paperTrades.js`
- `frontend/src/lib/AuthContext.jsx`
- every backend, bot, MQL5, deployment and infrastructure file

`InstrumentLogo.jsx`, `NotificationCenter.jsx`, `SettingsSupport.jsx`, and `AppSplashScreen.jsx` remain unchanged; their rendered states are regression-tested visually because global graphite CSS can affect them.

## Dependency changes

None. Use existing React, Lucide, Web3Icons and native `node:test`. Do not add a component library, animation library, font package, CSS framework, test runner or runtime dependency.

## Regression risks and containment

| Risk | Why it matters | Containment |
|---|---|---|
| Crypto/Forex branch drift | The two markets intentionally have different Home and Scan structures | Snapshot both branches at every target state; do not merge their data or control logic. |
| Signal semantics altered by presentation | Direction, score tier, lifecycle and terminal status drive trading comprehension | Reuse `Direction`, `statusForSignal` and normalized fields; do not recompute or rename statuses. |
| Execution ladder inversion | LONG and SHORT ladders have different favorable directions | Leave `executionLadderState.js` untouched and run its full tests; visually capture one LONG and one SHORT. |
| Missing or partial price values | Pending/legacy signals may omit current price, TP2 or timestamps | Render absence truthfully; never synthesize targets or prices. |
| Scan action regression | Forex authentication, cooldown, retry and Crypto refresh behavior are stateful | Keep handlers and disabled conditions byte-for-byte where possible; wrap only their output. |
| Auth/routing regression | History and Account redirect unauthenticated users | No changes to `selectTab`, `navigateAccountView`, route parsing or `App.jsx`; smoke-test protected navigation. |
| Navigation overlap | The fixed pill can cover dense content or sticky detail actions | Test 390×844 and ≤380px with safe-area emulation and long lists. |
| Header overflow | Wordmark, toggle, unread count and large-text preference compete for width | Capture 320/375/390/430px and unread counts 0, 4 and 99+. |
| Global CSS collateral damage | `global.css` also styles notifications, account/settings and light/compact modes | Keep selectors scoped under `.graphite-app`; perform screenshots on unaffected surfaces. |
| Accessibility preference regression | Motion/density refinements may override reduce-animation or large-text settings | Test OS reduced motion plus in-app reduced animations, compact layout and large text independently. |
| Focus and keyboard regression | New state/action framing may hide outlines or change button order | Keyboard-walk Home, Scan, Detail, History and notification popover; retain native controls. |
| Mobile Safari differences | `100dvh`, safe areas, sticky actions and backdrop filters vary | Verify on WebKit or an iOS device before release. |

## Required screenshot matrix

All primary approval captures: 390×844, dark mode, with a companion 320px-width overflow check.

1. Crypto Home — populated signals and active trades.
2. Crypto Home — no confirmed opportunity/empty state.
3. Forex Home — live/open session with Active and Pending Entry groups.
4. Forex Home — closed-market or overview-message state.
5. Crypto Scan — default, scanning, refresh error and no matching result.
6. Forex Scan — loading, cached-feed notice, network/API error and authentication-unavailable states.
7. Forex Scan — scanning/cooldown, NO_TRADE, trade-found and FAILED outcomes.
8. Signal Detail — Crypto LONG and Crypto SHORT.
9. Signal Detail — Forex BUY/SELL with TP2 and a signal without TP2.
10. Signal Detail — terminal/disabled action and saved-history result.
11. History — Crypto filtered, Forex filtered and no matching rows.
12. Navigation — each of the four active destinations, verifying the moving pill and content clearance.
13. Header — Crypto/Forex selected, notifications unread count, notification popover open.
14. Preference regression — compact layout, large text, light theme and reduced animations.

The acceptance comparison should pair the final five canonical screens—Home, Scan, Signal Detail, Home with Active Trades visible, and History—against the approved Refined references. A separate route for Active Trades must not be introduced because routing behavior is frozen.

## Frontend tests and build checks

### Automated checks

Run from `frontend/`:

```bash
npm test
npm run build
```

Required assertions:

- Existing `executionLadder.test.js` passes unchanged for LONG, SHORT, terminal, TP and clamp behavior.
- Existing `forexSignals.test.js` passes unchanged for timeframe/status filtering.
- Existing `mobileOnly.test.js` routing, Take Trade, History and deployment invariants pass.
- New UI contracts confirm four nav items, separate Crypto/Forex branches, one Forex loading card, reduced-motion coverage and no prohibited broad/ambient motion.
- Production build completes without new warnings, missing assets or chunk failures.

### Browser interaction smoke tests

- Toggle Crypto → Forex → Crypto on Home and Scan; confirm URL/local preference and displayed data remain synchronized.
- Open Crypto and Forex signals, close the sheet, and verify the originating tab/market is unchanged.
- Run Crypto refresh and every existing Forex scan outcome; verify action disable/cooldown/retry behavior is unchanged.
- Apply every Scan and History filter and verify only presentation changes.
- Trigger Take Trade logged out and logged in; verify redirect/save behavior is unchanged.
- Navigate Home/Scan/History/Account and browser back/forward; verify paths remain unchanged.
- Open/close notifications and select a notification action.
- Keyboard-test focus order and Escape dismissal.
- Test reduced motion, compact layout, large text and light theme.

## Integration order

1. Add typography/spacing/card tokens and capture baseline diffs.
2. Refine header, buttons and shared signal rows.
3. Refine Crypto Home, then Forex Home as a separate preservation check.
4. Add `GraphiteStateCard` and migrate states without changing state sources.
5. Refine Crypto Scan, then Forex Scan and all outcome states.
6. Refine Trade Analysis and Trade Plan while holding ladder tests constant.
7. Refine History and its empty state.
8. Apply navigation/press/screen motion and reduced-motion coverage.
9. Run the full automated, interaction and screenshot matrix before considering deployment.

## Release gate

Implementation is ready for deployment consideration only when an existing SwiftChart user can identify every screen immediately, both Crypto and Forex paths pass the same behavior checks as before, the ladder tests are unchanged, and the screenshots read as “polished SwiftChart” rather than a replacement product.
