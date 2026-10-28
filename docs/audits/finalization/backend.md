# Finalização — Backend, API e persistência (área 1)

Branch local `finalization/backend`, a partir de `2b12863`, com `ccr-bc7ddea5-hpnzbk` (`d0ed2e4`:
áreas 2–5 integradas) mesclado sem conflitos. Fronteira: `apps/api/app/**`, `services/oms/**`,
`services/execution/**`, `migrations/**` e seus testes. Nada foi enviado (sem push). Nenhuma chamada
a venue real: verificação offline (fakes, sqlite/aiosqlite) e a API local em `127.0.0.1:8765`.

**ESTADO: validado (código e testes locais).** CI, merge e deploy: não executados (fora do escopo).

## Verificações finais

| Comando | Resultado |
|---|---|
| `ruff check apps services tests` | `All checks passed!` |
| `python -m pytest` (suíte completa, após o merge de `d0ed2e4`) | `1483 passed, 2 warnings` (base em `2b12863`: `1412 passed`) |
| `DATABASE_URL=sqlite+aiosqlite:///… alembic upgrade head` + `alembic check` | `No new upgrade operations detected.` (antes: 22 índices e 1 tipo de drift, tabela `decision_memory` ausente) |
| `alembic heads` | `0015_decision_memory (head)`, cadeia linear 0001→0015 |
| API local (sqlite, persistência e control plane duráveis, token de operador) | 66 rotas GET que a UI usa: todas 200, salvo 404/409/503 rotulados (ver B2); jornada kill switch / paper / journal abaixo |

Jornada verificada com `curl` na API local (após as correções):

```text
POST /killswitch/trigger sem token   -> 401; journal: OPERATOR_COMMAND_REJECTED (command KILL_SWITCH_TRIGGERED, 401)
POST /killswitch/trigger com token   -> 200; journal: KILL_SWITCH_TRIGGERED
POST /paper/start com kill disparado -> 409 "kill switch is not armed"; journal: OPERATOR_COMMAND_REJECTED
POST /killswitch/reset, /paper/start -> 200, 200; journal: KILL_SWITCH_RESET, PAPER_TRADING_STARTED
restart no mesmo sqlite              -> /events/journal/verify: chain_valid=true, durable_history, 5 eventos
```

## Itens

| # | Item | Estado | Evidência / correção |
|---|---|---|---|
| B1 | Mapa UI → endpoint | validado | Toda rota chamada em `apps/web/src` (REST e `/ws/market-data`, `/ws/floor`) existe (`openapi()` + `@router.websocket`). Nenhuma rota faltando |
| B2 | Respostas não-200 nas rotas da UI | validado | `/live/analytics/{s}` 404 "no live analytics" e `/live/ready` 503 sem monitor ativo; `/live/history` 503 "live persistence is disabled"; `/v1/oms/orders?venue=XP_B3` 409 sem roteamento XP; superfícies sintéticas 503 com `SYNTHETIC_RESEARCH_ENABLED=false` (padrão). Todos rotulados, nenhum 200 com dado inventado |
| B3 | Autenticação de operador | validado + corrigido | `main.py` `operator_authentication_policy`: todo POST/PUT/PATCH/DELETE exige `X-Proto-Operator-Token` (`compare_digest`) quando há token ou em produção (sem token em produção: 503). Exceções com autenticação própria: `/creation/*` (segredo compartilhado), `/v1/signals/tradingview` (segredo no corpo + allowlist de IP + rate limit + limite de corpo), `/v1/watchdog/*` (token só-halt, `_authorize`). GETs são públicos e só leitura. **Defeito:** o middleware de auditoria gravava o nome do comando mesmo quando recusado: `POST /killswitch/trigger` sem token (401) aparecia no journal público como `KILL_SWITCH_TRIGGERED`. Agora 4xx/5xx viram `OPERATOR_COMMAND_REJECTED` com `command` e `status_code` (`2fc00dc`) |
| B4 | Vazamento de segredos | validado + corrigido | Respostas só expõem o tipo da chave de permit (`ed25519/configured/ephemeral`), o driver do banco e o *nome* do erro (`control_state.error = type(exc).__name__`); nenhum token/DSN. **Defeitos:** `ValidationError` de `Settings` ecoava todas as entradas (inclusive o token curto recusado) — `hide_input_in_errors=True` (`26d502c`); o OMS logava o texto da exceção do venue em submit desconhecido (pode carregar a query assinada) — agora só tipo e venue (`b8b3f12`) |
| B5 | Conector ChatGPT (`AgentConversation`, PR #335) | validado, **inalterado** | `conversation_surface.py`: opt-in (`CHATGPT_LOCAL_ENABLED`), cliente e host loopback, mesma origem, sem cabeçalhos `Forwarded/X-Forwarded-*`, token de operador obrigatório mesmo em dev, todas as rotas POST, sem fallback de chave de API paga (usa só o perfil de plano local), filtro de segredos na entrada e na saída, `execution_authority: false`, sem ferramentas. Nenhuma linha alterada |
| B6 | Agentes, roster, orquestração, memória de decisão | validado | `/v1/agents/status|roster|decisions/recent`, `/orchestration/status`, `/orchestration/decision-memory/*` (com `configured: false` quando sem store), `/creation/*`. `order_authority: false` nos agentes |
| B7 | Conselho de pesquisa A0/A1 | validado (fora da fronteira) | Wiring vive em `services/research/**` e na área 3; na API, `/research/*` são POST autenticados e rotulados. Não alterado |
| B8 | Dados, staleness e indisponibilidade | validado | `/live/*` com `stale`, `missing_symbols`, `readiness_failures`; `/universe` com `currently_fresh`; superfícies sintéticas rotuladas `SYNTHETIC_DEMO`/`SYNTHETIC_MODEL_SENSITIVITY` e desligadas por padrão; edge/EV marcados `COSTS_UNAVAILABLE`/parciais |
| B9 | Sinais / probabilidades / edge | validado | `/edge/evaluate` exige custos explícitos (422 `COSTS_UNAVAILABLE`); webhook TradingView: não durável ⇒ não aceito (503), duplicata após restart ⇒ `DUPLICATE` |
| B10 | Replay / simulação / paper | validado + corrigido | **Defeito:** `/paper/start`, `/simulation/start`, `/shadow/start` respondiam 200 com `running=false` quando o kill switch não estava armado, e o journal registrava um start que não aconteceu. Agora 409 como `/replay/start` (`de57037`) |
| B11 | Ordens / fills / posições / ledger / reconciliação / P&L | validado + corrigido | **Defeito (dinheiro de papel, sqlite):** `SqlLedger.amount` era `NUMERIC(38,18)`, que no SQLite passa por float: 0,3 BTC voltava como 0,299999999999999988 e reservar o saldo inteiro era recusado ("insufficient available"); os executores XP e Alpaca guardam o ledger em SQLite em produção. `ExactDecimal` grava texto exato no SQLite, mantém NUMERIC no PostgreSQL e lê coluna NUMERIC legada pelo menor texto de ida-e-volta (`1029057`). Listagem de ordens paper sem `venue`/`execution_backend` (`8954d24`). `fee_asset` serializado (B15). `/pnl/attribution` rotulado `SIMULATION_PORTFOLIO`, `KNOWN_ACCOUNTING_COSTS_ONLY` |
| B12 | Kill switch / idempotência / retries / recuperação (lado API) | validado + corrigido | Kill de risco durável (`persist_controls`), release só se persistido; intent repetido devolve a mesma ordem; UNKNOWN só resolvido por consulta; recover NEW⇒REJECTED. **Defeito:** intent não roteável consumia o permit sem `ORDER_REFUSED` no journal (`afa1247`) |
| B13 | Rótulos verdadeiros | corrigido | `/v1/floor/state` devolvia `promotion: []` com o registro ilegível (a UI mostra "No scope registered"); agora `null` ("Promotion data unavailable"). `/v1/risk/status` dizia sempre `account_source: "paper_ledger"`, também com o executor testnet; agora `PAPER`/`TESTNET` como `/health` (`14a6680`) |
| B14 | Migrações | corrigido | `decision_memory` não tinha migração (só `create_all`) e `env.py` não rastreava `OrchestrationBase`/`DecisionMemoryBase`. `0015_decision_memory` cria a tabela só se ausente (seguro em instalações existentes) e o downgrade a remove (`59fcb7c`). Os modelos canônicos agora declaram os índices `created_at` de 0002/0005; `env.py` compara `ExactDecimal` com NUMERIC(38,18) ou texto (`884f083`). Testes: `upgrade head` cobre toda tabela do runtime e `alembic check` sem drift |
| B15 | `fee_asset` nas ordens (achado da área 3) | corrigido | `ManagedOrder.to_dict` agora traz `fee_asset` (aditivo): definido ao lançar a taxa e, após restart, lido das entradas FEE do ledger (sem mudar o schema do order store) (`34b6e8d`) |
| B16 | `open_buys` no estado de conta do risco (achado da área 4) | corrigido | `account_state` preenche `AccountState.open_buys` com a quantidade base de BUY ainda em aberto no venue (todo status não terminal, inclusive UNKNOWN), do OMS paper ou do executor do venue. Ordens ilegíveis, ou uma ordem em aberto ininterpretável, bloqueiam risco novo como posição sem marcação (`f940de9`) |
| B17 | Floor summary por origem (achado da área 2) | corrigido | `signals_today` inalterado; novos `signals_today_tradingview` e `signals_today_proto` (`d3b2d86`) |
| B18 | WebSockets | validado | `WebSocketHub`: allowlist de origem, limite por canal, timeout de envio (assinante lento é desconectado, nunca trava o broadcast), `ping/pong`, mensagens > 1024 fecham. Reconexão é do cliente; o servidor não guarda sessão, então um reconnect recebe `subscribed` e o próximo evento |
| B19 | Health / readiness / métricas | validado | `/health` (rótulos), `/ready` (503 se banco ou control plane não respondem `SELECT 1`), `/metrics`, `/metrics/prometheus`, `/live/metrics/prometheus`, `/v1/safety/metrics/prometheus` |
| B20 | Código morto / imports / duplicações | validado | ruff (F401) limpo. Todo módulo de `apps/api/app`, `services/oms`, `services/execution` é referenciado (cadeias `research.py` → `*_surface.py`). Nenhum arquivo removido. Duplicações mantidas de propósito: `/portfolio` (rotulado) e `/v1/portfolio` (UI); kill switch de runtime (simulação) e governor (risco) são integrados via `_external_global_kill` |
| B21 | Testes poluídos por Alembic | corrigido | `fileConfig` do `env.py` desabilita loggers existentes; o teste de log do OMS reabilita `proto.oms` e os novos testes de migração não desabilitam loggers (`1d39619`) |

## Arquivos removidos

Nenhum.

## Política de stamping do Alembic (M3, documentação apenas)

Instalações de produção são criadas por `create_all` (`init_database`/`init_control_database`, e
`ledger_metadata.create_all` no executor), sem tabela `alembic_version`; o deploy não executa
Alembic. Comportamento **não** alterado. Política recomendada para quem passar a usar Alembic num
banco existente: confirmar que o schema corresponde à head (`alembic check` contra uma cópia) e então
`alembic stamp head` uma única vez; nunca `upgrade` num banco sem `alembic_version`, que tentaria
recriar tabelas existentes. Bancos SQLite criados antes de `ExactDecimal` mantêm `amount NUMERIC`
(leitura correta até ~15 dígitos significativos); recriar ou migrar a coluna para texto dá exatidão
total.

## O que os mocks não comprovam

- Ledger exato foi provado em SQLite; em PostgreSQL só o caminho NUMERIC inalterado (sem Postgres aqui).
- `open_buys` foi testado com executor falso e OMS paper; não com o executor real nem com a soma no
  Risk Engine integrado de ponta a ponta contra um venue.
- `fee_asset` em BASE foi testado com venue paper modificado; o formato real do Binance testnet vem
  do adapter (área 4).
- Middlewares e WebSockets via `TestClient`/uvicorn local, sem proxy reverso, TLS ou múltiplas réplicas.
- O conector ChatGPT não foi exercitado contra a OpenAI (exigiria perfil autorizado); só leitura de código.
- `alembic check` só em SQLite.

## Achados para outras áreas

| Área | Arquivo:linha | Achado | Correção proposta |
|---|---|---|---|
| Frontend | `apps/web/src/views/Trade.tsx:79`, `System.tsx:51` | Start paper/shadow com kill switch não armado agora responde 409 | Confirmar que o log de comando mostra o `detail` ("kill switch is not armed") |
| Frontend | `apps/web/src/views/floorScene.tsx` (tipo `FloorState.promotion`) | `promotion` pode ser `null` (indisponível) | Tipar como `PromotionScope[] \| null`; o render já trata |
| Infra | `infra/oci/deploy/proto-deploy.sh:164`, `infra/xp-node/start-xp-node.ps1:113` | Ledgers dos executores Alpaca/XP em SQLite com coluna NUMERIC legada | Recriar o volume do ledger em janela de manutenção (com reconciliação antes/depois) ou migrar a coluna para texto |
| Infra/API | `apps/api/app/websockets.py:11` | Origens padrão só `:5173`/`:8000`; outra porta local exige `PUBLIC_ORIGIN` | Documentar em OPERATIONS |
| API (registrado, não alterado) | `apps/api/app/main.py:228` | Recusas 401 de comandos ainda geram uma escrita no journal por requisição (auditoria), limitadas pelo rate limit HTTP | Avaliar amostragem se virar ruído |
| API (registrado, não alterado) | `apps/api/app/models.py:22`, `core_routes.py:516` | `KillSwitchState.LOCKED` nunca é atribuído; o ramo LOCKED→RESET_PENDING é inalcançável | Removido numa mudança própria, depois da finalização |

## Pendências

- CI, merge e deploy não executados; sem push.
- `mutmut`, `docker compose config` e os checks do web não foram rodados nesta área.
