# SwiftChart MT5 Auto-Trading

SwiftChart production Forex execution uses an MQL5 Expert Advisor installed inside the user's MetaTrader 5 terminal.

Production path:

1. SwiftChart backend receives a Forex signal.
2. The signal validation engine checks structure, confidence, duplicate trade IDs, market session state, and risk limits.
3. Approved signals are added to the pending EA queue.
4. The MQL5 Expert Advisor polls SwiftChart with its API key.
5. The EA executes locally inside the user's MT5 terminal.
6. The EA reports execution, rejection, management, and close updates back to SwiftChart.

SwiftChart does not store MT5 broker login or password. Broker execution happens locally inside the user's MT5 terminal.

## Backend Endpoints

Signal intake:

- `POST /api/forex/signal`
- `POST /api/trade/open`

Both endpoints validate the signal. By default, approved live requests are queued for the MQL5 EA. `dry_run: true` validates without queueing.

EA contract:

- `GET /api/ea/pending-signals`
- `POST /api/ea/trade-update`
- `POST /api/ea/heartbeat`
- `GET /api/ea/config`

Each EA request must include:

```http
X-SwiftChart-EA-Key: <api_key>
```

The current API-key implementation is a backend placeholder. Production should issue per-user EA keys, store only hashes, and scope every signal queue to the authenticated user/client.

## EA Trade States

The EA can report:

- `received`
- `executing`
- `executed`
- `rejected`
- `partially_closed`
- `breakeven_moved`
- `trailing_updated`
- `closed`
- `failed`

## Risk Defaults

- Risk per trade: `1%`
- Maximum daily loss: `3%`
- Maximum daily profit: `5%`
- Maximum trades per day: `3`
- Maximum open trades: `3`
- One open trade per pair: enabled
- Minimum confidence: `75`
- Maximum spread setting exposed to EA config: `2.5` pips

Final spread, margin, broker symbol, and lot-step checks happen inside the EA because the user's broker terminal has the authoritative symbol/account data.

## Signal Example

```json
{
  "pair": "EURUSD",
  "side": "BUY",
  "timeframe": "H1",
  "entry": 1.08452,
  "stop_loss": 1.0812,
  "tp1": 1.0881,
  "tp2": 1.0915,
  "confidence": 84,
  "setup_score": 88,
  "risk_percent": 1,
  "lot_size": 0.3,
  "trade_id": "swiftchart-eurusd-h1-001"
}
```

## Pending Queue Flow

1. SwiftChart receives the signal.
2. Backend validates and risk-checks it.
3. If approved, the signal is saved with status `received`.
4. The EA calls `GET /api/ea/pending-signals`.
5. SwiftChart returns pending signals and marks them `executing`.
6. The EA calls `POST /api/ea/trade-update` as the order lifecycle changes.

## Legacy Python Bridge

The official Python `MetaTrader5` package is not the production path.

Any Python MT5 bridge code is legacy/optional and kept only for experiments or controlled internal tooling. Production users should install the future SwiftChart MQL5 Expert Advisor inside MetaTrader 5 and connect it to the SwiftChart backend with an EA API key.

Legacy direct Python bridge endpoints, if enabled, live under `/api/legacy/...`.
