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

### Snapshot `finrobots-snapshot/1.1` (aditivo sobre 1.0, mesmas chaves antigas)

Cada chave é a resposta de uma rota GET do próprio PROTO (ou leitura pura), lida uma vez por
ciclo.

- **Timeout de 3 s:** vale para as fontes assíncronas (`oms.*`, `reconciliation.status`,
  `promotion.scopes`), que podem falar com o executor por HTTP.
- **Fontes síncronas** (`IN_MEMORY_SOURCES`: `agents.*`, `risk.status`, `market.quotes`,
  `watchdog.status`): leem estado do processo, sem I/O. Rodam no event loop **sem** timeout.
  Movê-las para uma thread criaria corrida com o estado alterado no loop, e um timeout de thread
  não interromperia o trabalho. Um teste impõe essa divisão. `snapshot["_sources"]` traz, por chave, `status` (`OK`, `UNAVAILABLE`,
`TIMEOUT`), `read_at`, `provenance` e `error`. Uma fonte que falhou **não aparece** no snapshot.
Fontes que sincronizam ou gravam ficam fora, por exemplo `GET /v1/agents/roster`, que sincroniza
fills.

| Chave | Origem (rota) | Proveniência | Especialista natural |
|---|---|---|---|
| `agents.status` | `GET /v1/agents/status` | PAPER/TESTNET | Strategy Research |
| `agents.decisions_recent` | `GET /v1/agents/decisions/recent?limit=50` | PAPER/TESTNET | Strategy Research, Model Validator |
| `risk.status` | `GET /v1/risk/status` | PAPER/TESTNET | Risk Guardian |
| `market.quotes` | `agent_surface.live_quote` por instrumento ativo (`bid`, `ask`, `observed_at`, `age_seconds`; `null` sem cotação) | LIVE (pública); `UNAVAILABLE` se nenhuma | Market Intelligence |
| `oms.orders` | `GET /v1/oms/orders?limit=50` (só a venue padrão `BINANCE_SPOT`; ordens XP/Alpaca ainda não entram) | PAPER/TESTNET | Execution Supervisor |
| `oms.ledger` | `GET /v1/oms/ledger` | PAPER/TESTNET | Portfolio Intelligence |
| `oms.executor` | `GET /v1/oms/executor/status` | PAPER/TESTNET | Execution Supervisor |
| `reconciliation.status` | `reconciliation_service.reconciliation_status()` | PAPER/TESTNET | Audit & Recovery |
| `watchdog.status` | `GET /v1/watchdog/status` | PAPER/TESTNET | Audit & Recovery |
| `promotion.scopes` | `GET /v1/promotion/scopes` | PAPER/TESTNET | Execution Supervisor, Model Validator |

**Lacuna confirmada:** não existe hoje uma leitura persistida de resultados quantitativos, backtests
ou validação de modelos. As rotas `validation_*`, `data-snooping` e `research/quant/*` calculam sob
demanda (POST), sem expor histórico. Quant Research, Strategy Research e Model Validator devem
ficar em `ABSTAIN` para essas dimensões até uma rota de leitura com proveniência ser acordada aqui.
A proposta `docs/research/FINROBOTS-QUANT-CONTRACT-PROPOSAL.md` (#359) é o ponto de partida.
Nenhum especialista deve chamar essas rotas POST.

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
