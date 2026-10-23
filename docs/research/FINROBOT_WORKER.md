# FinRobot research sidecar — A1 (experimental, opt-in)

This is a one-shot **research-only** adapter to the original FinRobot SDK.
It is a NEW PROTO integration wrapper, not a rewrite or copy of FinRobot's
financial logic. The unmodified SDK is installed via Git VCS at commit
`2717499b8e30f242640af08c4ad9afd1113c2d45`, under the exact original
Python package subdirectory `finrobot_desktop`.

**This version does not connect to the PROTO API, OMS, ledger, risk engine,
executor, live broker, or web dashboard.** Research is launched only as an
explicit, temporary operator job. Existing `services/orchestration` is
unchanged. Follow-up A1b can add an authenticated research-only job interface
after isolation and secret management receive a separate review.

## Original capabilities (not simulated)

The original [FinRobot SDK](https://github.com/AI4Finance-Foundation/FinRobot/blob/2717499b8e30f242640af08c4ad9afd1113c2d45/finrobot_desktop/finrobot/sdk.py)
already implements:

| CLI `--pipeline` | Original upstream method |
|---|---|
| `research` | `FinRobot.aresearch` |
| `earnings` | `FinRobot.aearnings` |
| `dcf` | `FinRobot.adcf` |
| `comps` | `FinRobot.acomps` |
| `ic-memo` | `FinRobot.aic_memo` |
| `lbo` | `FinRobot.albo` |

These routes create the original five FinRobot subagents via the upstream
`factory.py`. Their instructions remain upstream's source, and the copied A0
Markdown instructions are **reference copies**, not overriding the SDK package.

## Local operation (only on a secured development host)

The operator must supply a secret file **outside the repository**:

```bash
export PROTO_RESEARCH_LLM_KEY_FILE=/absolute/private/path/finrobot-llm-key
docker compose -f docker-compose.research.yml --profile research build finrobot-research
docker compose -f docker-compose.research.yml --profile research run --rm \
  finrobot-research --ticker AAPL --pipeline earnings \
  --model anthropic:claude-sonnet-4-6 --timeout 300
```

The worker receives its LLM key through Docker secret file
`/run/secrets/finrobot_llm_key`, reads the key only in memory, and forwards
it to the ORIGINAL SDK's `provider_keys` option. Never pass API keys as CLI
arguments, share them in chat, or commit them to a `.env` file.

A no-network, no-credential **request syntax check** is possible without
installing the vendor package or starting Docker:

```bash
python -m apps.research_worker --ticker AAPL --pipeline earnings \
  --model anthropic:claude-sonnet-4-6 --validate-only
```

Optional FMP enrichment: the adapter also reads a Docker secret at
`/run/secrets/finrobot_fmp_key`. This is **not** mounted by default; add it
only via a private local Compose override after checking FMP data licensing
and key permissions. Without an FMP key, FinRobot uses its documented
yfinance fallback, with materially narrower financial-data coverage.

## Output, security and limitations

- Outputs one JSON object to stdout: either `RESEARCH_REPORT`,
  `VALIDATED_ONLY`, or **`ABSTAIN`**. The `report` field is a report
  returned by the ORIGINAL SDK, not PROTO-generated analysis.
- Missing credentials, upstream exceptions, timeouts, missing steps,
  warnings and validation failures yield `ABSTAIN` with `report=null`.
- The sidecar is `read_only`, nonroot, has no exposed ports, drops Linux
  capabilities, limits CPU/RAM/PIDs, and uses ephemeral `/tmp` and `/work`.
- A separate Compose network prevents automatic DNS membership in the
  PROTO application network. **This is NOT a network firewall:** egress
  may still reach internet services or published host ports. Real production
  isolation requires an egress allowlist and a host firewall preventing
  access to private executor/control addresses.
- Do not deploy on the same trust/network plane as live trading credentials
  until a documented network-egress policy is enforced and tested.
- Upstream model/data usage incurs third-party API costs and data-license
  obligations. Transitive packages are pulled using upstream
  `pyproject.toml` ranges; build reproducibility and dependency auditing
  must be completed before any production deployment.
- Python model requests may be expensive; the process timeout here is an
  asyncio cancellation budget, **not a hardware-enforced CPU deadline**.
  Supply container runtime timeouts as a further independent boundary.
- The returned report may be delayed or non-point-in-time. It must never
  feed live order pricing, create RiskPermits, or independently authorize a
  trade, even if `status=RESEARCH_REPORT`.
- No claim is made that an external provider or paid LLM call has been tested
  successfully in this PR; offline tests stub only the original SDK boundary.

Source-attribution requirements: see `third_party/finrobot/{LICENSE,NOTICE,UPSTREAM.md}`.
This service must not be merged if it weakens `AGENTS.md` or `CLAUDE.md`.
