# TradingView alerts → PROTO signals

Phase 1 of `docs/plans/2026-10-13-tradingview-agent-trading.md`. A TradingView alert becomes a
`Signal` with source `TRADINGVIEW`. A signal is an opinion only: it never becomes an order by
itself. Later phases fuse signals into a `TradeIntent`, and only the Risk Engine can authorize
that intent.

## Prerequisites

- A paid TradingView plan that includes webhook alerts, with two-factor authentication enabled.
  TradingView requires 2FA for webhooks.
- PROTO reachable over HTTPS on port 443. TradingView only delivers webhooks to ports 80 and 443.
- `TRADINGVIEW_WEBHOOK_SECRET` set in the deployment secret store. Generate it with
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Production rejects secrets
  shorter than 32 characters.

## Endpoint

`POST https://<your-host>/v1/signals/tradingview`

| Check | Failure |
|---|---|
| Source IP is one of TradingView's webhook addresses | 403 `ip_not_allowed` |
| At most 120 alerts per minute per source | 429 `rate_limited` |
| Body ≤ 4 KB, a JSON object | 413 `payload_too_large` / 400 `invalid_json` |
| `secret` matches (constant-time comparison, checked before the schema) | 401 `unauthorized` |
| Schema below, no extra fields, timestamp with timezone | 422 `invalid_alert` |
| `fired_at` no older than 30 s and no more than 5 s in the future | 422 `stale_alert` / `future_alert` |
| `instrument_id` is in `TRADINGVIEW_INSTRUMENTS` | 422 `instrument_not_allowed` |

An accepted alert returns `200 {"outcome": "ACCEPTED", "signal_id": …, "order_authority": false}`
and is written to the hash-chained event journal as `TRADINGVIEW_SIGNAL_ACCEPTED`. The secret is
never stored. A repeated `(strategy_id, alert_id)` pair returns `"outcome": "DUPLICATE"` with the
same `signal_id` and nothing is journaled again. `GET /v1/signals/recent` lists the latest
signals.

Behind a reverse proxy or load balancer, set `TRADINGVIEW_TRUST_FORWARDED_FOR=true`. PROTO then
uses the right-most `X-Forwarded-For` entry, which your proxy appended. Never enable this when
clients can reach PROTO directly.

## Message schema

```json
{
  "secret": "<TRADINGVIEW_WEBHOOK_SECRET>",
  "alert_id": "<unique per strategy, e.g. the bar time>",
  "strategy_id": "tv-ema-cross",
  "instrument_id": "BINANCE_SPOT:BTCBRL",
  "direction": "LONG | SHORT | FLAT",
  "strength": 1.0,
  "timeframe": "15",
  "fired_at": 1760356800000
}
```

`fired_at` accepts epoch milliseconds (Pine's `timenow`) or an ISO-8601 timestamp with a timezone.
`strength` is optional, in `[0, 1]`, and defaults to `1`.

## Pine Script template (v5)

```pine
//@version=5
indicator("PROTO · EMA cross signal", overlay = true)

// Keep the script private: anyone who can read the alert can read the secret.
secret     = input.string("", "PROTO webhook secret")
instrument = input.string("BINANCE_SPOT:BTCBRL", "PROTO instrument id")
strategyId = "tv-ema-cross"

fast = ta.ema(close, 9)
slow = ta.ema(close, 21)
plot(fast, color = color.teal)
plot(slow, color = color.orange)

message(direction) =>
    '{"secret":"' + secret +
     '","alert_id":"' + str.tostring(time) +
     '","strategy_id":"' + strategyId +
     '","instrument_id":"' + instrument +
     '","direction":"' + direction +
     '","strength":1,"timeframe":"' + timeframe.period +
     '","fired_at":' + str.tostring(timenow) + '}'

if ta.crossover(fast, slow)
    alert(message("LONG"), alert.freq_once_per_bar_close)
if ta.crossunder(fast, slow)
    alert(message("SHORT"), alert.freq_once_per_bar_close)
```

## Creating the alert

1. Add the indicator to the chart of the instrument (for `BINANCE_SPOT:BTCBRL`, use the
   `BINANCE:BTCBRL` chart) and fill in the secret and the instrument id.
2. Create an alert with condition **PROTO · EMA cross signal → Any alert() function call**.
3. Under **Notifications**, enable **Webhook URL** and enter
   `https://<your-host>/v1/signals/tradingview`.
4. Fire a test and check `GET /v1/signals/recent`.

## Rotating the secret

1. Set a new `TRADINGVIEW_WEBHOOK_SECRET` and restart PROTO.
2. Update the secret input on every chart that uses the indicator.
3. Alerts carrying the old secret get 401 until they are updated.
