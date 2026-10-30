# FinRobots — contrato compartilhado `finrobots/v1`

Especificação versionada entre as duas branches do programa FinRobots. É o único canal de
coordenação: não há comunicação automática entre os ambientes do Claude Code e do Codex.
`AGENTS.md` prevalece sobre tudo aqui.

| Item | Valor |
|---|---|
| Versão do contrato | `finrobots/v1` (`services/finrobots/contracts.py::CONTRACT_VERSION`) |
| Commit-base em `main` | `8dce9e3` (#357) |
| Branch de arquitetura / MCP / UI (Claude Code) | `ccr-0e536765-ff341s`, designada pela sessão (o prompt pedia `feature/finrobots-claude`; ver “Branches”) |
| Branch dos motores (Codex) | `feature/finrobots-codex` |

## 1. O que já existe e é reutilizado (não duplicar)

| Capacidade | Onde está | Uso pelos FinRobots |
|---|---|---|
| Agentes de trading (fusão, reviewer que só reduz, roster R$200) | `services/agents/` | lidos via snapshot `agents.*` |
| Risk Engine, governor, kill switches, permits | `services/risk/`, `apps/api/app/risk_surface.py` | lido via snapshot `risk.status`; **nunca** chamado para avaliar ou emitir |
| OMS, ledger, reconciliação | `services/oms/` | só leitura |
| Orquestração de jobs (ProtoBrain, catálogo, missões) | `services/orchestration/` | não substituída; o FinRobot orchestrator roda ciclos de análise, não jobs |
| Journal operacional | `apps/api/app/event_state.py` | cada ciclo vira `FINROBOT_CYCLE_COMPLETED`; cada leitura do ChatGPT vira `MCP_TOOL_CALLED` |
| Conector ChatGPT por OAuth (explicação) | `services/chatgpt/`, `conversation_surface.py` (#335, #355) | mantido; o MCP é um canal adicional, só leitura |
| Teste de ferramenta via SIWC | PR #358 (aberto, draft) | independente; não alterado |

## 2. Interfaces (implementadas nesta branch)

```python
# services/finrobots/registry.py
class FinRobot(Protocol):
    robot_id: FinRobotId          # um dos 8 ids
    implementation: str           # "risk-guardian@1.0.0"
    async def analyze(self, context: CycleContext) -> FinRobotReport | list[FinRobotReport]: ...

@dataclass(frozen=True)
class CycleContext:
    cycle_id: UUID
    started_at: datetime
    instrument_ids: tuple[str, ...]
    snapshot: Mapping[str, Any]   # chaves: tabela "Snapshot" abaixo
```

### Snapshot `finrobots-snapshot/1.4` (1.2 + `oms.balances` + `quant.evidence`)

Cada chave é a resposta de uma rota GET do próprio PROTO, **verificada como sem efeito**: não
grava checkpoint, journal, métrica, esquema, nem inicia o OMS. Cada chave é lida uma vez por
ciclo.

- **Timeout de 3 s:** vale para as fontes assíncronas (`oms.*`, `quant.evidence`), que fazem I/O
  (executor por HTTP, banco de pesquisa).
- **Fontes síncronas** (`IN_MEMORY_SOURCES`: `agents.*`, `risk.status`, `market.quotes`,
  `watchdog.status`): leem estado do processo, sem I/O. Rodam no event loop **sem** timeout.
  Movê-las para uma thread criaria corrida com o estado alterado no loop, e um timeout de thread
  não interromperia o trabalho. Um teste impõe essa divisão.
- **Metadados:** `snapshot["_sources"]` traz, por chave, `status` (`OK`, `UNAVAILABLE`, `TIMEOUT`),
  `read_at`, `provenance` e `error`. Uma fonte que falhou **não aparece** no snapshot.

| Chave | Origem (rota) | Proveniência | Especialista natural |
|---|---|---|---|
| `agents.status` | `GET /v1/agents/status` | PAPER/TESTNET | Strategy Research, Audit & Recovery |
| `agents.decisions_recent` | `GET /v1/agents/decisions/recent?limit=50` | PAPER/TESTNET | Strategy Research, Model Validator |
| `risk.status` | `GET /v1/risk/status` | PAPER/TESTNET | Risk Guardian |
| `market.quotes` | `agent_surface.live_quote` por instrumento ativo (`bid`, `ask`, `observed_at`, `age_seconds`; `null` sem cotação) | LIVE (pública); `UNAVAILABLE` se nenhuma | Market Intelligence |
| `oms.orders` | `GET /v1/oms/orders?limit=50` (só `BINANCE_SPOT`). No modo paper, `UNAVAILABLE` (`SourceNotReady`) enquanto o OMS não tiver sido iniciado por outra via | PAPER/TESTNET | Execution Supervisor |
| `oms.balances` | `oms_surface.pure_balances()`: `{accounts: {<venue>: {account_id, mode, balances: {<asset>: {total, reserved, available, opening}}, venue_halted?, unavailable?}}, mode}`. Lê só `ledger.balances()` (paper) ou `GET /v1/ledger` de cada executor; não calcula account state nem grava checkpoint. No modo paper, `UNAVAILABLE` (`SourceNotReady`) até o OMS ser iniciado por outra via | PAPER/TESTNET | Portfolio Intelligence |
| `oms.executor` | `GET /v1/oms/executor/status` | PAPER/TESTNET | Execution Supervisor |
| `watchdog.status` | `GET /v1/watchdog/status` | PAPER/TESTNET | Audit & Recovery |
| `quant.evidence` | `services.quant.evidence.recent_quant_evidence(persistence_engine, limit=20)`: `{records: [{evidence_id, ...quant-evidence/1}], contract_version}`; só SELECT, cada linha revalidada por hash. `SourceNotReady` sem o módulo ou sem o store de pesquisa (a API de produção em LIVE_MONITORING não tem) | a dos registros (`input_provenance`: SIMULATED, REPLAY ou PAPER; `MIXED` se variar; nunca LIVE) | Quant Research, Strategy Research, Model Validator |

**Removidas na 1.2:** `oms.ledger`, `reconciliation.status` e `promotion.scopes`, porque cada uma
tem efeitos:

- `GET /v1/oms/ledger` chama `account_state()`, que grava checkpoints de equity/pico; no modo paper
  ainda pode iniciar o OMS (saldos de abertura, `recover()`).
- `reconciliation_status()` incrementa métricas.
- O registry de promoção cria a sua tabela na primeira leitura.
- O roster sincroniza fills.

Portfolio Intelligence usa `oms.balances` (saldos apenas: sem marcação, equity ou exposição; não deduza exposição a partir dele). Se uma
rota devolver `gross_exposure=Infinity`, isso significa apenas exposição desconhecida (sem
marcação, executor ou ordens indisponíveis) e deve ser relatado assim.

**Lacuna confirmada:** não existe hoje uma leitura persistida de resultados quantitativos, backtests
ou validação de modelos. As rotas `validation_*`, `data-snooping` e `research/quant/*` calculam sob
demanda (POST), sem expor histórico. Quant Research, Strategy Research e Model Validator devem
ficar em `ABSTAIN` para essas dimensões até uma rota de leitura com proveniência ser acordada aqui.
A proposta `docs/research/FINROBOTS-QUANT-CONTRACT-PROPOSAL.md` (#359) é o ponto de partida.
Nenhum especialista deve chamar essas rotas POST.

**`quant-evidence/1`: implementado** (Codex `beec583`; leitura no snapshot 1.4) (#360, comentário de 2026-10-30).

| Dono | Parte |
|---|---|
| Codex | Registro por envelope completo (`evidence_id` = SHA-256 do JSON canônico; instrumento canônico, modo, `observed_at` com fuso, custos com fonte e timestamp, amostras estritamente anteriores, versões, resultado em texto exato). Persistência idempotente: conteúdo diferente com o mesmo id é conflito (409), sem sobrescrita. Tabela só por migração Alembic + `init_database`. Leitura pura `services/quant/evidence.py::recent_quant_evidence(engine, *, limit=20, instrument_id=None)`: só SELECT, limite 100, `LookupError` sem engine. |
| Claude Code | Chave `quant.evidence` (snapshot 1.4), com proveniência = `input_provenance` dos registros (nunca LIVE), timeout de 3 s e teste de ausência de efeito. Implementado. |

`FinRobotReport` (pydantic, `extra="forbid"`, congelado): `robot_id`, `implementation`,
`cycle_id`, `instrument_id | None` (None = escopo SYSTEM), `stance`
(`ABSTAIN | SUPPORT | CAUTION | VETO`), `confidence ∈ [0,1]`, `headline`, `rationale[≤8]`,
`evidence[≤16]`, `produced_at`, e `financial_connectivity`/`real_money_execution` fixados em
`False`. Regras validadas:

- stance diferente de `ABSTAIN` exige evidência com proveniência diferente de `UNAVAILABLE`;
- `Evidence.provenance ∈ {LIVE, DELAYED, REPLAY, PAPER, TESTNET, SIMULATED, STALE, UNAVAILABLE}`;
  `observed_at` com fuso; métricas como texto exato;
- texto livre tem caracteres de controle e de bidi removidos e tamanho limitado;
- não existe campo para ordem, permit, tamanho de posição ou limite.

### Council (`services/finrobots/council.py`)

Determinístico, por instrumento: `VETO` de um papel com `may_veto` → `BLOCK_NEW_RISK`;
`CAUTION` (ou veto fora do mandato) → `REDUCE_RISK`; `SUPPORT` ≥ quórum (2) → `CONSISTENT`;
senão `NO_CONSENSUS`. Relatórios com mais de 10 min ou datados no futuro não votam. Veto em
escopo SYSTEM vale para todos os instrumentos. **O veredito é consultivo**: nesta versão não
alimenta o Risk Engine. Uma ligação futura só pode **reduzir** risco (mesmo padrão do reviewer
shrink-only) e precisa de PR próprio.

Papéis com veto: `model-validator`, `risk-guardian`, `execution-supervisor`, `audit-recovery`.

### Orquestrador (`services/finrobots/orchestrator.py`)

Snapshot lido uma vez por ciclo; cada especialista com timeout próprio (10 s) e até `max(16, instrumentos + 1)` relatórios (acima disso, os mais restritivos ficam e o corte vira erro do robô); falha, timeout,
relatório de outro robô ou de outro ciclo vira `errors[robot_id]` e não vota. Histórico de 50
ciclos em memória. Laço contínuo (`FINROBOTS_ENABLED`, `FINROBOTS_INTERVAL_SECONDS`, mín. 5 s),
iniciado no lifespan da API, independente de qualquer sessão do ChatGPT.

## 3. Ponto de integração para a branch dos motores (Codex)

O Codex entrega **um** módulo novo, que esta branch não cria nem edita:

```python
# services/finrobots/robots/__init__.py   (dono: feature/finrobots-codex)
def default_robots() -> list[FinRobot]: ...
```

`apps/api/app/finrobot_surface.py::bind_default_robots` importa esse módulo se existir; se não
existir, todos os especialistas aparecem `UNBOUND` e os ciclos não têm relatórios (verdade, não
simulação). Requisitos para cada especialista:

1. Reutilizar os módulos listados em `SPECS[robot].reuses`; não criar motor paralelo.
2. Nunca importar `services.venues` nem `apps.executor`; nunca chamar
   `risk_surface.assess`, `oms_surface`, `submit_*` ou qualquer rota mutável.
3. Ler o mundo pelo `context.snapshot` ou por funções puras/somente leitura dos serviços.
4. Sem evidência utilizável → `ABSTAIN`. Nunca inventar número; dado velho → `STALE`.
5. `cycle_id` do relatório = `context.cycle_id`; `robot_id` = o do próprio robô.
6. Testes offline com o fixture de robô em `tests/unit/test_finrobots.py::Robot`.

Arquivos de cada branch (para evitar edição concorrente):

| Dono | Arquivos |
|---|---|
| Claude Code | `services/finrobots/{__init__,contracts,registry,council,orchestrator}.py`, `apps/api/app/{finrobot_surface,mcp_surface}.py`, `apps/web/src/views/FinRobots.tsx`, `docs/finrobots/*` |
| Codex | `services/finrobots/robots/**`, testes `tests/unit/test_finrobot_robots*.py` |

Mudança no contrato = nova versão (`finrobots/v2`) neste arquivo, com compatibilidade
declarada; o validador aceita apenas a versão vigente.

## 4. Branches

A sessão do Claude Code foi provisionada com a branch `ccr-0e536765-ff341s` e não pode publicar
em outra sem autorização explícita. O trabalho do Claude está nela (PR para `main`). Se o
operador quiser o nome `feature/finrobots-claude`, basta criar a branch a partir dela; o
conteúdo é o mesmo. Ordem de integração recomendada: esta PR primeiro (define o contrato), a do
Codex depois, rebaseada em `main`.

## 5. Revisão cruzada (checklist para o Codex revisar esta branch)

- contrato sem campo de autoridade; boundary flags fixos;
- council só reduz; não ligado ao Risk Engine;
- `/mcp` sem nenhuma ferramenta mutável; token próprio; origem validada; journal por chamada;
- `POST /v1/finrobots/cycles` atrás do token de operador;
- isolamento: `services/finrobots` não importa venues/executor (`tests/unit/test_venue_isolation.py`).
