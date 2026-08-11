# SwiftChart Refined — Motion review

## Summary

The prototype uses motion as feedback and orientation, not as a new visual identity. It keeps SwiftChart's existing sliding navigation model, tightens timings, removes decorative card-by-card movement, and includes a reduced-motion fallback.

## Before / after

| Area | Current SwiftChart | SwiftChart Refined | Why |
|---|---|---|---|
| Screen change | Multiple card/stage rise animations around 300–420ms | One 180ms screen entry, 4px travel | Preserves continuity without making a frequent trading workflow feel staged. |
| Bottom navigation | Sliding selection and active-icon emphasis around 320ms | Selection slides in 220ms; press feedback is 120ms | Same familiar interaction, with faster acknowledgement and less visual wobble. |
| Buttons and signal rows | Mixed transition treatment | Consistent 120ms press compression and 160ms color response | Gives taps immediate, restrained feedback across the system. |
| Scan progress | General easing | 200ms ease-in-out between determinate progress values | Progress communicates ongoing work without a decorative loop. |
| Trade Plan | Moving price marker could animate layout position | Prototype marker is stable; future live movement should use transform | Avoids layout work and keeps price changes legible rather than theatrical. |
| Reduced motion | Existing support varies by component | Global reduced-motion override plus removal of entry transform | Keeps hierarchy and state changes understandable without motion. |

## Standards check

- No `transition: all`.
- No `ease-in` for interface entrances or exits.
- No `scale(0)` entrances.
- No infinite decorative animation.
- No large blur animation.
- No interaction depends on animation to communicate state.

## Verdict

**Approve.** Motion is subtle, functional, interruptible at the interaction level, and subordinate to SwiftChart's existing visual identity.
