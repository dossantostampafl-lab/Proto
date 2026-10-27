# Finalização — Área 3: agentes, modelos, pesquisa e calibração

- **Data:** 2026-10-27.
- **Base:** `2b12863`. Branch: `worktree-agent-a0341064a1fea30cf`.
- **Fronteira de edição:** `services/agents/**`, `services/signals/**`, `services/research/**`,
  `services/analytics/**`, `apps/research_worker/**` e os testes deles.
- **Somente leitura (auditados):** `services/quant/**`, `services/orchestration/memory.py`,
  `apps/api/app/*_surface.py`, `apps/api/app/settings.py`, `services/risk/engine.py` e
  `services/oms/manager.py`.

**ESTADO: PARCIAL.** Todos os defeitos encontrados na fronteira foram corrigidos e testados. Ficam
pendentes dois achados de outras áreas que afetam agentes (fee_asset do OMS e risco acumulado por
posição) e a reprodução das linhas 1m, 5m e 15m do laboratório.

## Verificações

| Comando | Resultado |
|---|---|
| `ruff check apps services tests` | `All checks passed!` |
| `python -m pytest` (suíte completa, final) | `1443 passed, 2 warnings in 38.83s`. A base tinha 1412 testes, todos verdes. |
| Testes afetados, rodados após cada correção | verdes (ver commits) |
| Reprodução do laboratório (klines públicas 1h, 2025-10-19 → 2026-10-18, 8.760 barras por símbolo, 0 lacunas) | linhas 1h e 4h e tabela de "survivors" **idênticas** ao relatório (retorno, DD, Sharpe, trades, PF, @10 bps, ano completo) |

## Itens

| # | Item | Estado | Evidência / correção |
|---|---|---|---|
| 1 | Roster: R$200 virtual, códigos únicos nunca reutilizados, eliminação abaixo da ordem mínima, multiplicador de sobrevivência, reposição | validado | `roster.py`: sequência `max+1`, `UniqueConstraint`, linhas nunca apagadas. Piso `max(R$30, min_notional×BRL)`. Multiplicador `(eq/200)^2` limitado a [0,1]. `min_score` só sobe. Spawn recusado acima de R$10.000. Coberto por `test_agent_roster.py`, `test_b3_agents.py` e `test_alpaca_routing.py`. |
| 2 | Fusão + revisor que só encolhe | validado (corrigido) | `apply_review` usava `assert` para impedir crescimento. Com `python -O`, ou com uma revisão criada por `model_construct`, um REDUCE ≥1 aumentava a ordem. Agora é uma checagem explícita que falha fechada. Testes novos: REDUCE nunca cresce em 14 combinações e revisão forjada (1.0/1.5/10) → `None`. |
| 3 | Sizing ≤ 0,25% de risco e ≤ 1,0x | validado por ordem / pendente por posição | `build_candidate` usa `conservative_equity × 0,25% × convicção ÷ stop` e o headroom de 1,0x. O roster encolhe para 1,0x do patrimônio do próprio agente (R$200 × 2% de stop = R$4, ≈0,04% de R$10k). Sem o roster, sinais LONG repetidos empilham posição até 1,0x. Ver o achado entre áreas B. |
| 4 | Estratégias: EMA arm-and-confirm, lab horário atrás de `AGENT_LAB_STRATEGIES_ENABLED` + `AGENT_LAB_WARMUP_KLINES`, B3/US atrás de flags | validado (corrigido) | **Defeito 1:** depois de uma lacuna no feed (queda, cotação velha, pregão B3 fechado), os dois adaptadores fechavam a barra antiga no próximo tick e podiam emitir sinal de uma barra fechada há muito tempo, carimbado com a hora tardia. Reproduzido no código antigo: barra das 12:29 emitida como 12:59. Agora a barra entra no histórico mas não decide nada, e o EMA descarta o cruzamento armado. **Defeito 2:** um mid `inf` podia virar SHORT com força 1.0, porque `min(1.0, nan)` dá 1.0. Agora mids não finitos são recusados. **Defeito 3:** `warm()` duplicava barras num warm-up tardio ou repetido. Agora só antepõe barras mais antigas, deduplicadas e ordenadas. As flags estão validadas em `Settings`: lab sem warm-up recusa iniciar (teste existente). |
| 5 | Sinais / probabilidades / fair value / edge (NaN, inf, divisão por zero, dado velho) | validado (corrigido na fronteira) | `greeks`: volatilidade negativa zerava o passo do vega (`ZeroDivisionError`); agora o domínio é validado como no pipeline quant. `live_market`: mid ≤0 ou não finito agora dá `ValueError` claro. `PnLAttributionInput` recusa NaN/inf. `services/quant/core.compute_edge` com NaN resulta em REJECT (falha fechada). `estimate_probability` com NaN levanta erro de validação. |
| 6 | Métricas de calibração e integração com replay | validado (somente leitura) | `services/quant/calibration.py`: Brier, log-loss com epsilon, ECE/MCE corretos; probabilidade NaN levanta erro. `test_quant_calibration`, `test_calibration_read`, `test_quant_replay_pipeline`, `test_replay_*` e `test_models_calibration_api` verdes. Achado menor C. |
| 7 | Gate de promoção do revisor de IA (`evaluate_reviewer`) | validado (corrigido) / pendente | **Defeito:** o sinal de `realized_return` era invertido para candidatos SELL, embora o campo seja documentado como retorno do próprio trade. Um revisor que vetasse boas saídas parecia melhor que a base. O retorno agora é usado como dado; há teste com SELL. **Pendente:** não existe ferramenta que monte `ReplayCase` a partir do histórico real, então `AGENT_REVIEWER=claude` não tem evidência de promoção. |
| 8 | Conselho de pesquisa A0 | validado | `test_finrobot_source_integrity.py` (hashes do upstream, offline) verde. Sem imports em runtime. |
| 9 | Conselho A1: worker FinRobot isolado (opt-in, falha fechada, sem segredos, não implantado) | validado (corrigido) | O timeout cobria só a chamada do pipeline; abrir e fechar o SDK (`__aenter__`/`__aexit__`) podia travar sem limite. Agora um único `asyncio.timeout` cobre o ciclo inteiro, e timeout inválido (≤0, NaN, inf, >900 s) dá ABSTAIN `INVALID_REQUEST` antes de ler segredos. Chaves vêm só de `/run/secrets`. Só roda via `docker-compose.research.yml --profile research`. Ausente de `infra/oci/deploy` e do compose principal. Ver o achado F. |
| 10 | Memória de decisões | validado (somente leitura) | `services/orchestration/memory.py`: entrada imutável por `decision_id`, desfecho imutável, timestamps com fuso, desfecho não antecede a observação. Achado menor D. |
| 11 | Laboratório de estratégias reproduzível (walk-forward, custos, DSR) e ressalva estatística | validado em 1h/4h / pendente em 1m/5m/15m | Linhas 1h e 4h e os survivors foram reproduzidos exatamente com o código atual (incluindo as correções desta área). O candidato tem DSR ≤0,04 mesmo com só 76 tentativas (o relatório usa 288 e reporta 0,00). A ressalva "candidato para forward-test, não edge comprovado" é verdadeira. 1m/5m/15m não foram reexecutados: download de ~525 mil barras de 1m por símbolo. Fórmulas de DSR e E[max SR] conferidas com Bailey & López de Prado (curtose não excedente, (γ4−1)/4). |
| 12 | Fronteira de ordens (agentes só produzem intents) | validado (teste novo) | `test_agent_order_boundary.py`: `services/{agents,signals,research,analytics}` e `apps/research_worker` não importam `services.venues`, `services.oms`, `services.risk`, `apps.executor` nem `apps.api`. |
| 13 | Rotulagem verdadeira | validado | `AgentDecision.to_dict`: `order_authority/financial_connectivity/real_money_execution=false`. Roster: `capital: VIRTUAL`. Worker: `research_only=true`, `execution_authority=false`. O pipeline recusa os estágios CANARY/LIVE. Os documentos do lab rotulam backtest e simulação. |

## Código removido ou consolidado

- `services/research/backtest.py`: removidos `to_brl()` e `iso()`. Não têm referência em código,
  testes, docs, Docker, CI ou carga dinâmica (busca por `rg -w` no repositório inteiro).
- `services/agents/roster.py`: removidos a cópia de `_floor_to_lot`, que agora vem de `.fusion`, e o
  alias `_active`, que duplicava `active_for`.
- **Duplicações mantidas de propósito:**
  - `proto_strategy._ema` e `research.indicators.ema`: a produção não deve depender do pacote de
    pesquisa, e a paridade é coberta por `test_ema_cross_matches_the_production_strategy`.
  - `_floor` em `live.py` e em `proto_strategy.py`: os dois tratam fuso de forma diferente.

## O que os testes e mocks NÃO comprovam

- **Revisor Claude:** o cliente é fake. Não há chamada real à API nem medição de custo ou latência,
  e nada mostra que o revisor melhora PnL (não existe replay histórico real).
- **Worker FinRobot:** o SDK é stub. O build da imagem e o import do SDK original só rodam no
  workflow `research-worker.yml` (Docker), não aqui. O isolamento de rede do compose **não** é
  firewall, como já diz `FINROBOT_WORKER.md`.
- **Laboratório:** reproduz backtest (próxima abertura + 2 bps, taxa 0,1%), não fills de testnet nem
  de mainnet. O FX fixo de 5,4 ignora câmbio. Com DSR ≈0, nenhum resultado positivo é edge
  comprovado.
- **Lacunas do feed:** testadas com relógio sintético. A política (barra após lacuna não decide) foi
  verificada em unidade, não no feed vivo.
- **Livro do agente:** com ordens reais da Binance, fica sujeito ao achado A (taxa em BTC).

## Achados entre áreas (fora da minha fronteira)

**A. Backend/OMS (alta).** `services/oms/manager.py:69` (`ManagedOrder.to_dict`) não expõe
`fee_asset` nem `filled_notional`, e `services/oms/store.py:52` não persiste `fee_asset`.

- `services/agents/roster.py:881` lê `order.get("fee_asset")`. Sem esse campo, toda taxa é tratada
  como moeda de cotação.
- Na Binance, a taxa de compra vem no ativo base (BTC). O livro do agente então superestima a
  posição em cerca de 0,1% por compra, subestima as taxas, e uma venda "de tudo que é dele" pode
  consumir moedas do faucet.
- **Correção:** guardar `report.fee_asset` no `ManagedOrder` em `_post_fill` (`manager.py:376`),
  persistir na store e incluir `fee_asset` (e `filled_notional`) em `to_dict`. O roster já trata
  taxa no ativo base (`test_agent_roster.py:302`).

**B. Risco (média, decisão do dono).** `services/risk/engine.py:256-261` aplica `risk_per_position`
à **ordem nova** (`notional × stop ≤ 0,25%`), não à posição acumulada.

- Com `AGENT_ROSTER_ENABLED=false`, LONGs repetidos empilham posição até 1,0x, cada um com 0,25%.
- **Correção proposta (só aperta):** somar a posição existente do instrumento ao notional nas
  intents que não reduzem. Antes disso, decidir como contar as moedas do faucet da testnet, que hoje
  já bloqueariam BTC.

**C. Quant (baixa).** `services/quant/calibration.py:34` usa `int(outcome)`, que trunca `0.9` para
`0`. Proposta: aceitar só `bool` ou `0`/`1` exatos.

**D. Orquestração (baixa, desempenho).** `services/orchestration/memory.py:212` (`snapshot`) carrega
todas as linhas. Proposta: usar `count(*)` e
`count(*) filter (where outcome_json is not null)`.

**E. Configuração/risco (baixa).** `apps/api/app/settings.py:82,88`: `agent_stop_distance` e
`risk_stop_distance` são independentes e aceitam valores perto de zero.

- Um `risk_stop_distance` minúsculo multiplica o notional permitido por ordem; o limite de 1,0x
  continua valendo.
- Proposta: piso documentado, por exemplo ≥0,005, e `risk_stop_distance ≥ agent_stop_distance`.

**F. Infra (baixa).** `Dockerfile:42` e `Dockerfile.api:11` (`COPY apps ./apps`) levam o código
inerte de `apps/research_worker` para as imagens de API e executor. O FinRobot não está instalado
lá e nada importa o worker. Proposta: excluir `apps/research_worker` no `.dockerignore` dessas
imagens, ou aceitar e documentar.

**G. Docs (baixa, área infra).**

1. Em `docs/research/strategy-lab-2026-10-19.md:305`, o exemplo de "Reproduce" roda
   `ts_momentum` sem o wrapper `regime`. Não é o candidato. Sugestão: acrescentar
   `backtest --spec` com `LAB_HOURLY_TS_MOMENTUM`.
2. No mesmo arquivo (linha 292, "Live adapter") e em `docs/agents/autonomy.md`, citar que uma
   barra fechada depois de uma lacuna do feed não emite sinal.
3. Em `docs/agents/hybrid-pipeline.md:58`, dizer que `realized_return` já vem com sinal do trade e
   que ainda não existe gerador de casos históricos.

**H. Sinais TradingView (informativo).** Em `services/signals/tradingview.py:52`, um alerta sem
`strength` vale 1.0 e passa o piso de entrada 0,6. É escolha de design. Recomendo documentar no
template do alerta ou exigir o campo.

## Bloqueios

Nenhum bloqueio de acesso. A reprodução de 1m/5m/15m depende de baixar cerca de 1,6 milhão de
barras públicas e de minutos de CPU; ficou fora por custo, não por impedimento.
