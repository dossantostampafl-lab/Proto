# Public feed golden fixtures

These files are frozen offline parser fixtures derived from public vendor wire examples. They contain no credentials, account identifiers, private user data, or authenticated-channel payloads.

## `coinbase_advanced_ticker.json`

- Source: Coinbase Advanced Trade WebSocket Channels documentation, public `ticker` channel example.
- Vendor documentation: `https://docs.cdp.coinbase.com/coinbase-business/advanced-trade-apis/websocket/websocket-channels`
- Retrieval context: benchmark-hardening cycle, 2026-08-31.
- Purpose: detect parser/schema drift using a stable public-wire example.
- Classification: official vendor documentation sample, not a Proto live capture.

When adding future fixtures, preserve exact public wire shape where practical, remove any credentials/account data, record provenance here, and keep tests network-free.
