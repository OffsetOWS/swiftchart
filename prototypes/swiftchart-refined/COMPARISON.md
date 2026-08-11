# Current SwiftChart → vNext → SwiftChart Refined

| Element | Current SwiftChart | vNext | Keep from current | Improve |
| --- | --- | --- | --- | --- |
| Brand header | `SWIFTCHART` wordmark, Crypto/Forex toggle, circular alert button | New square “S” mark, descriptive subtitle, reduced market prominence | Existing wordmark, header balance, market switch, alert position | Tighten alignment and make controls share one height |
| Background | Graphite black with restrained diagonal light and green cast | Near-flat generic black surface | Graphite depth and recognisable green-black atmosphere | Reduce competing highlights and keep contrast consistent |
| Home structure | Market Summary → Regime → Top Opportunities | Page intro → decision panel → mixed sections | Existing order and familiar dashboard scan path | Reduce oversized hero footprint and clarify section spacing |
| Card language | Rounded graphite/glass cards with inner highlights | Mostly flat sections and separator lists | Current card silhouette, subtle gradient, edge highlights | Standardise radius, padding, border, and shadow strength |
| Market summary | Large 97 score and recognisable compact chart | Written market brief with smaller score | Large score, market leader callout, chart motif | Improve label hierarchy and reclaim vertical space |
| Signal identity | Instrument logo, pair, direction/timeframe/R, score capsule, conviction | Direction rail, text-first rows, score/R column | Instrument-first layout, score capsule, conviction label | Improve alignment, direction contrast, TP/SL progress, and touch feedback |
| Crypto / Forex | Persistent two-way switch in the global header | Secondary segmented control inside content | Existing persistent market model and terminology | Make the thumb and labels clearer without changing behaviour |
| Navigation | Four-item floating pill: Home, Scan, History, Account | Five-item bar including separate Trades | Existing four destinations and pill highlight | Shorten highlight motion and improve icon/label optical alignment |
| Scan | Hero scan card → search → three filters → result cards | New command-panel structure and state lab | Existing screen order, CTA placement, search, filters, cards | Add truthful scan progress and clearer TRADE / WAIT / NO TRADE states |
| Signal detail | Bottom sheet, instrument header, score ring, three metrics, horizontal execution ladder | Full-page detail and vertical ladder | Sheet model, score ring, stat strip, reasoning, actions | Extend existing horizontal Trade Plan with TP2/current price and clearer semantics |
| Active trades | Surfaced inside Home/data flows rather than primary navigation | Dedicated primary navigation destination | Keep it subordinate to Home and use existing signal-card language | Add a “View active” subview without altering global navigation |
| History | Total R hero → market filters → outcome filters → timeline card | New flat performance page and filter rail | Current hierarchy, pill filters, timeline | Improve density, outcome labels, typography, and empty state |
| Motion | 300–450ms page/card/nav effects plus some ambient loops | New motion language across more surfaces | Familiar spatial relationships and subtle feedback | Remove decorative loops, shorten frequent motion, use transform/opacity, preserve reduced-motion |
| States | Several useful API/empty messages but visually inconsistent | Comprehensive new state system | Existing copy tone and placement near affected content | Give loading/error/cached/empty states one SwiftChart card template |

The Refined direction starts from the current production structure. vNext is used only as evidence of what not to replace.
