# Public feed golden fixtures

These files are frozen offline parser fixtures derived from public vendor wire examples. They contain no credentials, account identifiers, private user data, or authenticated-channel payloads.

## `coinbase_advanced_ticker.json`

- Source: Coinbase Advanced Trade WebSocket Channels documentation, public `ticker` channel example.
- Vendor documentation: `https://docs.cdp.coinbase.com/coinbase-business/advanced-trade-apis/websocket/websocket-channels`
- Retrieval context: benchmark-hardening cycle, 2026-08-31.
- Purpose: detect parser/schema drift using a stable public-wire example.
- Classification: official vendor documentation sample, not a Proto live capture.

## `coinbase_advanced_level2.json`

- Source: Coinbase Advanced Trade WebSocket Channels documentation, public `level2` channel example.
- Wire channel: `l2_data`; subscription channel: `level2`.
- Vendor documentation: `https://docs.cdp.coinbase.com/coinbase-business/advanced-trade-apis/websocket/websocket-channels`
- Retrieval context: empirical-alpha data-contract cycle, 2026-08-31.
- Purpose: freeze the vendor snapshot/update schema before public L2 corpus collection.
- Classification: official vendor documentation sample, not a Proto live capture.
- Authentication: none required for the public `level2` channel; no JWT/API key is stored here.

When adding future fixtures, preserve exact public wire shape where practical, remove any credentials/account data, record provenance here, and keep tests network-free.
