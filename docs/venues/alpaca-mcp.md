# Alpaca MCP server (research, read only)

[`alpacahq/alpaca-mcp-server`](https://github.com/alpacahq/alpaca-mcp-server) (MIT) is registered in
`.mcp.json`. The registration is pinned to `2.4.1` and run with `uvx`. It gives Claude Code
**market data only**, for research and for the trading skills in `.claude/skills/`.

## What it can and cannot do

`ALPACA_TOOLSETS` limits the server to read-only toolsets:

- `assets`
- `stock-data`
- `crypto-data`
- `options-data`
- `corporate-actions`
- `news`
- `fixed-income-data`

Each of these toolsets calls only GET market-data or reference endpoints.

Deliberately **not** enabled:

| Toolset | Why |
|---|---|
| `trading` | Places, replaces and cancels orders. |
| `account` | Includes `patchAccountConfig`. |
| `watchlists` | Writes. |
| `locates` | Short-sale requests. |

The AI must never reach a venue directly. The Proto order path is unchanged:

- intent → Risk Engine permit → OMS → isolated executor (`docs/venues/alpaca-paper.md`);
- nothing goes through MCP.

Do not add `trading` or `account` to `ALPACA_TOOLSETS`. An order sent through MCP would bypass
the Risk Permit, which `AGENTS.md` forbids. On the paper account the executor uses, it would also
show up as a reconciliation divergence and halt the executor.

`ALPACA_PAPER_TRADE=true` stays fixed.

## Keys

The server reads `ALPACA_MCP_API_KEY` and `ALPACA_MCP_SECRET_KEY` from the operator's own environment.

- Use a **separate paper key**, not the executor's.
- Never commit the keys, paste them in chat, or put them in the API or web services.
- Without the variables the server simply fails to start. Nothing else is affected.

## Enabling

1. Create a free paper account at <https://app.alpaca.markets/paper/dashboard/overview> and
   generate a paper API key.
2. Export `ALPACA_MCP_API_KEY` and `ALPACA_MCP_SECRET_KEY` in the environment that runs
   Claude Code. For cloud sessions, use the environment's secret settings, never the repository.
3. Approve the `alpaca` project MCP server when Claude Code asks. It needs `uv`/`uvx`
   installed.
