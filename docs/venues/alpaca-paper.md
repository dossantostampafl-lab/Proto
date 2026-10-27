# Alpaca US equities, PAPER account only

A third executor venue, `ALPACA_US`, next to `BINANCE_SPOT` (testnet) and `XP_B3` (MT5 demo). It
trades US stocks and ETFs on Alpaca's **paper** account: simulated fills, no money. It is off unless
the operator turns it on, and nothing changes in production while it is off.

```text
agents ─> Risk Engine (USD limits from BRL, US hours) ─permit─> API ─bearer─> Alpaca executor ─> paper-api.alpaca.markets
                                                     API <─ IEX quotes ─ Alpaca executor <─ data.alpaca.markets (feed=iex)
```

This is separate from the read-only research MCP server in `docs/venues/alpaca-mcp.md` and from
the API's read-only `ALPACA_MARKET_DATA_*` equity view. Use a different key for each, and never
give the paper trading key to the API, an agent or an MCP server.

## Labels and boundary

- `financial_connectivity=false` and `real_money_execution=false` on every route. The executor
  reports `venue_environment=TESTNET`, which for this venue means the Alpaca paper account. The
  ledger view labels the Alpaca block `mode: PAPER`.
- **Paper only, pinned.** `services/venues/alpaca.py` sends every trading call to
  `https://paper-api.alpaca.markets` and every data call to `https://data.alpaca.markets`. There is
  no setting, parameter or environment mapping that reaches the live trading host. Redirects are
  never followed, and LIVE credentials are refused when the adapter is built. A test checks the
  source for any other host (`tests/unit/test_alpaca_paper.py`).
- **Quotes are IEX.** The free plan gives the IEX feed: one exchange's quotes, **not** the
  consolidated SIP tape. Spreads can be wider than the national best bid and offer, and outside
  IEX hours there is no quote. `GET /v1/us/quotes` says `feed: IEX` and `consolidated: false`, and
  each quote carries Alpaca's own timestamp and its age.
- **Keys** live only in the Alpaca executor (`PROTO_CRED_ALPACA_US_TESTNET_MAIN_API_KEY` and
  `_API_SECRET`, or their `_FILE` forms, read by `services/venues/credentials.py`). The executor
  fetches the quotes itself, so the keys never leave it. They are sent as the `APCA-API-KEY-ID`
  and `APCA-API-SECRET-KEY` headers only, and they are never logged or printed.

## How orders work

- **Order types.** LIMIT only, whole shares, time in force `ioc` (what the agents send) or `day`.
  GTC would rest overnight and is refused. MARKET is refused.
- **Identity.** The OMS client order id is Alpaca's `client_order_id`. A resubmit that Alpaca
  refuses as a duplicate returns the order it already holds. Query and cancel use the same id.
- **Unknown outcomes.** A timeout, a dropped connection, a 5xx or a redirect raise, so the OMS
  marks the order UNKNOWN and resolves it by query, never by resending.
- **Market hours, three layers, all fail closed:**
  1. The Risk Engine approves US intents only during 09:30–16:00 New York, on weekdays
     (`session_open`). There is no pre-market or after-hours trading.
  2. The adapter asks `/v2/clock` before every new order and refuses it (REJECTED, nothing sent)
     unless Alpaca says the market is open now. This also covers holidays and early closes. An
     unreadable clock refuses too.
  3. Quotes older than 10 s fail the Risk Engine's `fresh_quote` check.
- **Balances and reconciliation.** USD cash plus position quantities from `/v2/account` and
  `/v2/positions`. The ledger opens from what the paper account already holds, and any divergence
  halts the Alpaca executor only. Fees are zero: Alpaca charges no commission on paper equities and
  does not simulate regulatory fees.
- **Assets.** Each configured symbol must be an active, tradable `us_equity` in `/v2/assets`.
  Anything else is left out, and its orders are rejected.

## Limits in BRL, account in USD

The constitutional limits stay as they are (R$10,000 reference, 0.25% per position, 1% daily,
5% drawdown, 1.0x). The USD account is measured against **R$10,000 worth of USD**:

- BRL per USD comes from the same public Binance `USDTBRL` ask that the USDT account uses. There
  is no keyless USDBRL source here, so **USDT stands in for the US dollar**.
- A stablecoin can trade slightly below the dollar, which would turn R$10,000 into slightly more
  dollars than it is worth. So the USD rate is raised by `USD_PROXY_MARGIN` (2%,
  `apps/api/app/fx_rates.py`). The USD reference capital can only come out smaller.
- No fresh, sane rate means a USD reference capital of 0. The Risk Engine then sizes nothing new:
  the account abstains.

**What this means for sizing.** The 0.25% risk rule with a 2% stop allows a position of about
R$1,250 (about US$220). An agent's R$200 is about US$36. In whole shares, **SPY and QQQ cost more
than one agent's budget**, so the two US agents will normally abstain ("below one lot"). That is
correct behaviour, not a bug. To see trades, configure lower-priced liquid symbols. Never loosen
a limit.

## The US agents

`AGENT_US_ENABLED=true` (default false), together with Alpaca routing, adds two own-strategy
agents, `ALPACA_US:SPY` and `ALPACA_US:QQQ`, with a virtual R$200 each. Their codes come from the
roster's monotonic sequence, like every other agent's. They run the same EMA cross as the B3
agents. The lab's hourly momentum assumes 24/7 bars and Binance warm-up klines, so it does not fit
6.5-hour US sessions.

## Enabling it (operator)

1. **Account.** Sign up for free at alpaca.markets. In the dashboard, switch to the **Paper**
   account. It is created with simulated cash, and it needs no funding and no KYC for paper. Then
   generate an API key there. Never generate a key on the live account for this.
2. **Vault secrets** (`docs/runbooks/oracle-setup.md`, "Alpaca paper key"). Create these:

   | Secret | Read by | Value |
   |---|---|---|
   | `proto-alpaca-paper-key` | executor instance only | paper API Key ID, typed by the operator |
   | `proto-alpaca-paper-secret` | executor instance only | paper Secret Key, typed by the operator |
   | `proto-alpaca-executor-token` | API and executor | generated by `create-secrets.sh` |
   | `proto-watchdog-alpaca-token` | watchdog and executor | generated by `create-secrets.sh` |

   The IAM lists are in `infra/oci/vault.tf`. Apply the stack, with `enable_alpaca=true` if the NSG
   should open the Alpaca port (8101) to the API and the watchdog. These are fetched as their own
   roles (`executor-alpaca`, `api-alpaca`, `watchdog-alpaca` in `fetch-secrets.sh`). A missing one
   keeps Alpaca off with a log line. The Binance executor, the API and the watchdog do not wait
   for it.
3. **Turn it on.** On each instance (executor, API, watchdog), as root, create the non-secret file
   `/etc/proto/alpaca.conf`:

   ```text
   ALPACA_SYMBOLS=SPY,QQQ
   ALPACA_EXECUTOR_PORT=8101
   AGENT_US_ENABLED=false
   ```

   Then run `proto-deploy --force` on the executor first, then on the API and the watchdog. The
   executor instance runs a second container, `proto-executor-alpaca`, from the same approved
   `:executor` image, with its own sqlite ledger in a volume. A failing Alpaca container never
   rolls back or stops the Binance executor. Remove the file and run `--force` again to turn it
   off.
4. **Check it.** Look at `GET /v1/oms/executor/status?venue=ALPACA_US`, `GET /v1/us/quotes` (during
   the session) and the `alpaca_us` block in `GET /v1/oms/ledger`.

Settings, for reference:

- Executor: `EXECUTOR_VENUE=ALPACA_US`, with `EXECUTOR_SYMBOLS` required.
- API: `ALPACA_EXECUTOR_URL` and `ALPACA_EXECUTOR_API_TOKEN`, which need
  `OMS_EXECUTION_BACKEND=executor`. The token must be 32+ characters and unlike the others.
- Watchdog: `WATCHDOG_ALPACA_EXECUTOR_URL` and `WATCHDOG_ALPACA_EXECUTOR_TOKEN`, which go
  together.

## Watchdog

With `WATCHDOG_ALPACA_EXECUTOR_URL` set, the watchdog trips on a divergence at any hour, and on a
reconciliation older than 3 minutes during the US regular session. A trip halts the Alpaca
executor only. An unreachable Alpaca executor is reported, not tripped. It cannot be halted over a
dead link, and the API already blocks US risk while it cannot read the account.

## What is NOT done

- No live Alpaca trading, live keys, live host or `CANARY`/`LIVE` stage on this venue. The executor
  refuses every stage except PAPER and SHADOW.
- No fractional shares, MARKET, GTC, short selling, options, crypto, extended hours or margin use.
- No consolidated (SIP) data. IEX only, and labelled as such.
- No holiday calendar in the Risk Engine. Alpaca's clock refuses orders on closed days instead.
- US agents' fills are not yet folded into their virtual books. `sync_fills` reads the Binance
  executor's orders only, which is the same limit the B3 agents have today.
- The dashboard has no Alpaca lane yet. US agents would show on the default (Binance) floor.
- No deposits, withdrawals or transfers. Alpaca paper has none, and the adapter has no such route.
