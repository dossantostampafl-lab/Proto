# Plano: automação completa de trading com TradingView e agentes

- Status: proposta para revisão do dono do repositório
- Data: 2026-10-13
- Base: `main` após o PR #268 (reformulação do Proto)
- Fronteira obrigatória: `AGENTS.md` e `docs/adr/autonomous-real-money-execution-boundary.md`

## Decisões já tomadas

| Pergunta | Resposta |
|---|---|
| Mercados | Todos: cripto spot, B3 e forex |
| Papel do TradingView | Fonte de sinais via webhook, combinada com sinais dos agentes do Proto |
| Primeira entrega | Até canary com dinheiro real |
| Decisão dos agentes | Híbrida: regras/modelos determinísticos geram o sinal; agentes de IA filtram, contextualizam e explicam |

## Princípio central

Nenhum alerta do TradingView, agente de IA, estratégia ou ação de UI envia ordem para corretora. Todos produzem
**intents tipados** que percorrem a cadeia de autoridade do ADR:

```text
TradingView alert ─┐
Estratégias Proto ─┼─> Signal ─> Fusão ─> Agente IA (filtro) ─> TradeIntent
Agentes Proto ─────┘                                              │
                                                                  v
                     Risk Engine determinístico ─> RiskPermit (curto, uso único)
                                                                  │
                                                                  v
                     OMS canônico ─> Executor isolado (credenciais) ─> Corretora
                                                                  │
                                                                  v
                     Ledger transacional <─> Reconciliação contínua ─> Painel/alertas
```

O agente de IA só pode **reduzir, adiar, abster ou vetar**. Ele nunca aumenta tamanho, nunca vê credenciais e,
se falhar ou estourar o tempo, o resultado é abstenção.

## O que o Proto já tem (ponto de partida)

| Peça | Hoje | Lacuna para canary |
|---|---|---|
| Dados de mercado | Feeds públicos Binance/Coinbase (BTC/ETH/SOL); leitura de ações via Alpaca/brapi | Calendários de pregão B3/forex, feeds forex, guarda de dado velho por classe de ativo |
| Estratégia | `paper_autopilot` (desequilíbrio do book) | Contrato `Signal` genérico, múltiplas estratégias, fusão |
| Orquestração | `services/orchestration` (missions, memory, supervisor, modos seguros) | Agentes de decisão com saída estruturada e trilha de auditoria |
| Risco | Gate Python do simulador (notional, posição, nº de posições, edge/confiança, lock) | Risk Engine independente com limites constitucionais e `RiskPermit` |
| OMS | Simulador com idempotência por sessão | Máquina de estados de ordem real, `UNKNOWN`, retries, client order ids |
| Ledger | Portfólio + journal de eventos + reconciliação básica | Ledger transacional (posições, saldos, fills, taxas, reservas) e reconciliação contra a corretora |
| Execução real | Inexistente (`real_money_execution=false`) | Executor isolado + adapters por corretora |
| Gates | Phase gates de live monitoring | Evidência de replay/shadow/paper/adversarial/recovery/canary por tupla |

## Fases

Cada fase termina com CI verde e evidência reproduzível. As fases 1 a 4 são independentes de corretora e valem
para todos os mercados.

### Fase 0 — Contratos canônicos (fundação)

- `InstrumentSpec` multi-ativo: classe (crypto/B3/forex), venue, tick, lote, moeda, calendário de sessão.
- `Signal`, `TradeIntent`, `RiskPermit`, `OrderState`, `Fill`, `LedgerEntry` como modelos Pydantic congelados e
  versionados.
- Interface `VenueAdapter` (submit, cancel, query, stream de fills, saldos) com um `PaperVenueAdapter` que embrulha o
  simulador atual.
- ADR: escolha de corretoras e hospedagem do executor.

### Fase 1 — Ingestão de sinais do TradingView

- `POST /v1/signals/tradingview`:
  - O TradingView não envia cabeçalhos personalizados. A autenticação usa segredo no corpo JSON, comparado em tempo
    constante, mais allowlist dos IPs oficiais de webhook do TradingView.
  - O endpoint só aceita a porta 443.
- Validação de esquema, deduplicação por id do alerta, rejeição de alerta velho (`max_age`) e limite de taxa.
- O alerta vira `Signal` com proveniência `tradingview`, gravado no journal. Ele nunca vira ordem diretamente.
- Templates Pine Script com payload padronizado (símbolo, lado, força, timeframe, id da estratégia, timestamp).
- Pré-requisito do dono: plano pago do TradingView com webhooks e 2FA ativado.

### Fase 2 — Agentes híbridos

- Estratégias determinísticas (autopilot atual e novas) e sinais do TradingView alimentam um módulo de **fusão** que
  gera `TradeIntent` candidato com tamanho calculado deterministicamente (`services/portfolio/sizing.py`).
- Agente de IA (Claude, modelo mais recente disponível) recebe contexto (mercado, notícias, regime, posições) e
  responde em saída estruturada: `APPROVE | REDUCE | DEFER | VETO`, fator de redução ≤ 1 e justificativa.
  - O agente nunca recebe credenciais.
  - Prompt e resposta ficam na decision memory (auditoria).
  - Se o agente falhar ou estourar o tempo limite, o resultado é `DEFER`.
- Avaliação offline do agente em replay: ele só entra se não piorar o resultado da estratégia determinística.

### Fase 3 — Risk Engine determinístico e RiskPermit

- Processo/módulo independente dos agentes, sem dependência de LLM.
- Limites constitucionais (não alteráveis por automação):

  | Limite | Valor |
  |---|---|
  | Capital de referência | R$10.000 |
  | Risco por posição | 0,25% do patrimônio |
  | Perda diária | 1% |
  | Drawdown | 5% |
  | Alavancagem bruta | 1,0x |

  Somam-se limites por venue e por classe de ativo, e guarda de horário de pregão.
- `RiskPermit`:
  - assinado (HMAC);
  - vinculado ao hash do intent;
  - validade de segundos;
  - uso único.
- O OMS rejeita qualquer ordem sem permit válido.
- Kill switch global, por venue e por estratégia. Guarda de dado velho. Lockdown automático ao atingir perda diária ou
  drawdown.

### Fase 4 — OMS canônico, ledger e reconciliação

- Máquina de estados: `NEW → SUBMITTED → ACK → PARTIAL → FILLED | CANCELED | REJECTED | UNKNOWN`.
- `client_order_id` determinístico (idempotência), retries com backoff e resolução de `UNKNOWN` por consulta à
  corretora antes de qualquer reenvio.
- Ledger transacional em Postgres (Alembic) para posições, saldos, fills, taxas e reservas.
- Reconciliação contínua contra a corretora. Divergência acima da tolerância → halt da venue.

### Fase 5 — Executor isolado e adapters de corretora

- Executor em processo/contêiner separado. É o único com credenciais, lidas de um secret manager e separadas por
  ambiente, conta e venue.
- Credenciais de trade sem permissão de saque/transferência, com allowlist de IP na corretora.
- Ordem de implementação recomendada (um caminho certificado por vez):
  1. **Cripto spot — Binance** (pares BRL/USDT). O Proto já tem feed e simulação desses ativos.
  2. **B3 — corretora com API ou ponte MetaTrader 5.** A ponte MT5 exige host Windows. A corretora ainda precisa ser
     escolhida.
  3. **Forex — corretora com API REST.** É preciso confirmar se a corretora aceita residentes no Brasil. A alavancagem
     fica limitada a 1,0x.

### Fase 6 — Gates de promoção (por conta × venue × estratégia × modelo × credencial)

`RESEARCH → REPLAY → SHADOW → PAPER → CANARY → LIVE`

| Gate | Evidência exigida |
|---|---|
| Replay | Determinístico, dados point-in-time, resultado reproduzível |
| Shadow | Sinais reais e ordens só registradas; comparação com o mercado |
| Paper | Mínimo de semanas a definir; invariantes de risco e OMS sem violação |
| Adversarial/chaos | Queda de feed, corretora fora, ordem `UNKNOWN`, fill parcial, relógio, duplicatas |
| Recovery | Reinício no meio de ordens; reconciliação fecha sem divergência |
| Canary | Capital mínimo, limites reduzidos, watchdog independente, rollback testado |

Qualquer falha rebaixa o caminho automaticamente para um estado mais seguro.

### Fase 7 — Painel, observabilidade e operação

- A UI e a API identificam cada dado como live, delayed, replay, paper, simulated, stale, unavailable ou unknown.
- Botão de kill switch, estado de cada caminho de promoção, decisões dos agentes com justificativa e alertas
  (Grafana/infra existente).
- Runbook de incidente e de rollback.

## Como vamos executar (skills)

| Etapa | Skill |
|---|---|
| Refinar requisitos e alternativas | Superpowers `brainstorming` (após instalar), `engineering:system-design` |
| ADRs (corretoras, executor, permit) | `engineering:architecture` |
| Plano detalhado por fase | Superpowers `writing-plans` |
| Implementação | Superpowers `subagent-driven-development` + `test-driven-development`, um workstream por vez (arquitetura, Python, dados, frontend, testes, segurança, performance) |
| Revisão | `code-review`, `security-review` antes de cada merge |
| Verificação | Superpowers `verification-before-completion`, CI do repositório |

Ciclo de cada PR: PLAN → IMPLEMENT → TEST → AUDIT → FIND DEFECTS → FIX → RETEST → RE-AUDIT.

## Decisões do dono

| Tema | Decisão |
|---|---|
| Cripto | Binance (spot) |
| B3 | XP, via MetaTrader 5 em VM Windows. Confirmar com a XP que MT5 para B3 e ordens automáticas são permitidos na conta |
| Hospedagem do executor | Oracle Cloud, em instância separada e subnet privada (ver `docs/adr/venues-and-executor-hosting.md`) |
| Forex | A definir. Fica fora do escopo até escolher uma corretora |
| Capital do canary | R$ 500, com mínimo de 1 semana em PAPER sem violação (ver `docs/promotion/gates.md`) |
| Plano do TradingView | A confirmar antes da Fase 1. O webhook exige plano pago e 2FA |

## Fora de escopo

- Depósitos, saques, custódia e transferências.
- Mudança autônoma dos limites constitucionais.
- Mercados de predição. Eles exigem gates jurídicos próprios antes de qualquer ativação.
