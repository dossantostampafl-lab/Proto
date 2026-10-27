# Finalização 2026-10-27, área 5: infraestrutura, observabilidade, testes e limpeza

Base: `2b12863` (branch `finalization/infra`). Ambiente: container Linux x86_64, Python 3.13.16,
Node 22.22.0, npm 10.9.4, Docker Compose v5.6.0 (sem daemon), sem `terraform`, `shellcheck` ou
`promtool`. Venv novo em diretório temporário; nenhuma credencial real usada.

**ESTADO: PARTIAL.** Todos os gates locais estão verdes e a documentação foi alinhada. Ficam
pendentes: a validação de regras do Prometheus com `promtool` (download de binário externo negado
pelo ambiente) e `terraform validate` (ferramenta ausente). Há ainda achados de migração que
pertencem a outra área (seção 9).

## 1. Itens

| # | Item | Estado | Evidência |
|---|---|---|---|
| 1.1 | `pip install -e '.[dev]'` do zero | validado | venv novo, exit 0 |
| 1.2 | `ruff check apps services tests` | validado | `All checks passed!` (antes e depois das mudanças) |
| 1.3 | `python -m pytest` | validado | base: `1412 passed, 1 warning in 54.47s`; final: `1416 passed, 1 warning in 54.44s` (+4 testes novos) |
| 1.4 | Web `npm ci && typecheck && build && contract-check && bundle-check` | validado | exit 0; `frontend contracts: ok (31 source files)`; `bundle budget: ok (637187 bytes up front, 3D floor JS 680118 bytes, ...)`; `npm ci`: `found 0 vulnerabilities` |
| 1.5 | API em SQLite: `/health`, `/ready`, `/metrics` e métricas Prometheus | validado | ver seção 2 |
| 1.6 | `docker compose config` | validado | `docker-compose.yml` exit 0; `docker-compose.live.yml` exit 0 (com `PROTO_DB_PASSWORD`/`GRAFANA_ADMIN_PASSWORD` fictícios); `docker-compose.research.yml` exit 0 com `PROTO_RESEARCH_LLM_KEY_FILE=/dev/null` (sem a variável falha de propósito) |
| 2.1 | Cadeia Alembic linear | validado | `alembic heads` → `0014_agent_conversations (head)`; `alembic history` de `<base>` a `0014`, sem merges; novo teste `test_alembic_history_is_one_linear_chain` |
| 2.2 | Upgrade de SQLite vazio | validado | `alembic upgrade head` exit 0 (38 tabelas); `downgrade base` exit 0; novo `upgrade head` exit 0; a API sobe nessa base com todos os endpoints 200 |
| 2.3 | Compatibilidade com instalações existentes | **pendente (outra área)** | uma base criada pela API (`create_all`) não tem `alembic_version`, e `alembic upgrade head` nela falha com `table simulation_fills already exists`. O deploy na Oracle nunca roda Alembic: o comportamento é conhecido e agora está documentado (`docs/OPERATIONS.md`, `docs/runbooks/rollback.md`). Há drift de esquema; ver seção 9 |
| 3.1 | Health, readiness e metrics | validado | seção 2 |
| 3.2 | Alertas e dashboards coerentes com as métricas emitidas | validado | todos os 41 nomes `proto_*` usados em `infra/monitoring/*-alerts.yml` e nos dashboards existem nos emissores. Sete aparecem só com dados (rótulos por símbolo ou escopo de promoção, ou `pruned_rows`): `proto_live_symbol_*_age_seconds`, `proto_live_journal_pruned_rows`, `proto_promotion_{stage,clean_days,violations,blockers}`. Novo teste de regressão `test_every_alert_and_dashboard_metric_is_emitted_by_the_api` |
| 3.3 | Sintaxe das regras Prometheus | validado (estático) / pendente (`promtool`) | YAML e JSON parseiam: 13 regras de live e 8 de safety, todas com `alert` e `expr`. A execução do `promtool check rules` foi negada (binário externo) |
| 3.4 | Logs sem segredos | validado, com um achado | não há `logger`/`print` com token, segredo ou chave em `apps/` e `services/`; `proto-deploy` mascara os segredos do papel em `show_logs`; `fetch-secrets.sh` nunca ecoa valores. **Achado:** erro de validação de `Settings` ecoa a entrada (seção 9, item C1) |
| 4.1 | CI roda o que o CLAUDE.md diz | validado | `ci.yml`: job python = `pip install -e '.[dev]'`, `ruff check apps services tests`, `pytest`; job web = `npm ci`, `typecheck`, `contract-check`, `build`, `bundle-check`; mais os jobs `mutation-safety` (`mutmut run`) e `compose`. CLAUDE.md agora cita esses dois jobs. Novo teste `test_ci_runs_exactly_the_documented_gates` |
| 4.2 | Deploy pull-based com aprovação do executor | validado | `deploy.yml` só publica imagens (`packages: write`) e não tem credencial Oracle; `image` exige CI verde de push em `main` ou dispatch em `main`; o job `executor` usa `environment: executor`; as instâncias puxam sua tag (`proto-deploy.sh`) e voltam à última imagem saudável. Coberto por `tests/unit/test_oci_infra.py` |
| 4.3 | Nada vai para produção sem aprovação | validado, com dependência externa | `:stable` (API e watchdog) segue o merge em `main` com CI verde: a revisão do PR é a aprovação. `:executor` só se move com aprovação **se** o ambiente `executor` tiver *Required reviewers*. Um ambiente inexistente é criado pelo GitHub sem proteção (seção 7) |
| 5 | Limpeza | validado | seção 4 |
| 6 | Varredura de segredos | validado | seção 5 |
| 7 | Coerência da documentação | validado | seção 3 |
| 8 | Drills reprodutíveis | validado | `python -m services.drills run <DRILL> --seed 7 --out ...` e depois `verify`: `ADVERSARIAL_SUITE ... reproduced 3a71a7e1…`, `RECOVERY_DRILL ... reproduced 1e70bd73…`, `ROLLBACK_DRILL ... reproduced c2b988fb…` |
| 9 | Scripts shell de deploy | validado (sintaxe) | `bash -n` em `infra/oci/deploy/*.sh` e `infra/oci/scripts/*.sh`: todos ok. `shellcheck` não está disponível |
| 10 | Terraform | pendente | sem `terraform` no container; os contratos estáticos de `test_oci_infra.py` passam |
| 11 | Auditoria de dependências | validado | `pip-audit`: `No known vulnerabilities found`; `npm ci`: 0 vulnerabilities |

## 2. Inicialização da API (evidência)

Execução: `DATABASE_URL=sqlite+aiosqlite:///<tmp>/api.db PERSISTENCE_ENABLED=true
CONTROL_PERSISTENCE_ENABLED=true ORCHESTRATION_PERSISTENCE_ENABLED=true
LIVE_MONITORING_AUTOSTART=false uvicorn apps.api.app.main:app --port 8765`

| Endpoint | Resultado |
|---|---|
| `/health` | 200 `status: ok`, `account_source: PAPER`, `control_plane.status: DURABLE`, `financial_connectivity: false`, `real_money_execution: false` |
| `/ready` | 200 `database: ready`, `control_plane.durable: true`, `accepts_new_risk: true` |
| `/metrics` | 200 `real_money_execution: false` e contadores HTTP |
| `/metrics/prometheus` | 200 (`proto_http_requests_total` etc.) |
| `/live/metrics/prometheus` | 200 (`proto_live_*`) |
| `/v1/safety/metrics/prometheus` | 200 (`proto_watchdog_*` etc.) |
| `/live/ready` | 503 `MONITOR_STOPPED, SOURCE_DISCONNECTED, ...`, o esperado com o autostart desligado e sem rede |
| `/v1/reconciliation` | 200 `consistent: true` |

Os mesmos endpoints responderam igual numa base criada por `alembic upgrade head`.

## 3. Correções feitas

| Commit | Mudança |
|---|---|
| `5831a65` | `tests/unit/test_infra_contracts.py` (novo, 4 testes): métricas de alertas e dashboards emitidas; `rule_files` existentes e regras com `alert`/`expr`; cadeia Alembic linear com uma head; CI = comandos do CLAUDE.md, Python 3.13 e Node 22 |
| `466ad95` | `pyproject.toml`: remove `pytest-cov`, sem uso (nenhum `--cov` em CI, configuração, scripts ou docs) |
| `a23db80` | `docs/OPERATIONS.md`: reescrita conforme a realidade atual, com a seção **"Inicialização reproduzível"** (comandos verificados e respostas esperadas). `README.md`: fronteira de segurança, `npm ci` e deploy pull-based. `docs/RELEASE_READINESS.md`: a fronteira não diz mais "no broker/exchange credentials". `docs/LIVE_*` (7 arquivos): aviso de escopo (só o `live_app` só-leitura); o gate de isolamento não chama mais o `main:app` de legado. `docs/runbooks/rollback.md`: quais migrações alteram tabelas e o fato de a produção não rodar Alembic. `docs/runbooks/oracle-setup.md`: o bridge MT5 existe, com link para o runbook do PC. `CLAUDE.md`: linhas de layout para `apps/mt5_bridge`, roster e `infra/`, e a nota da CI |
| `2080531` | `.gitignore`: `*.pem`, `*.db`, `*.sqlite`, `*.sqlite3`. O `.dockerignore` já excluía esses arquivos; o repositório não. Nenhum arquivo versionado casa com os padrões |
| `d2b6757` | `ci.yml`, job `compose`: valida também `docker-compose.research.yml`, com a variável apontando para o caminho `/dev/null` (nunca um valor). É um gate a mais; nenhum foi removido |

Nenhum teste, gate ou limite foi enfraquecido. Não houve mudança em `apps/` nem em `services/`.

## 4. Limpeza: removidos e mantidos

**Removido:** a dependência `pytest-cov` (motivo acima). Nenhum arquivo foi apagado.

**Mantido após verificação (não é código morto):**

- `scripts/check_conversation_browser.py`: sem referência em CI ou docs, mas é o harness manual
  do contrato de conversa com ChatGPT (mantido como está, conforme a regra comum). Uso: rodar à
  mão com o Playwright instalado.
- `infra/watchdog/proto-watchdog.service`: citado em `docs/watchdog/watchdog.md:94` e testado em
  `tests/unit/test_safety_ops.py:184`.
- `tools/render_control_room/*` e `.github/workflows/render-control-room.yml`: geram
  `public/floorcontrol/robot.webp`, que ainda é usado (`FloorControl.tsx:92`). O fundo
  `room.webp`/`room-portrait.webp` e o `anchors.json` que o workflow verifica foram trocados pela
  foto aprovada `room-photo.webp` (o workflow é só `workflow_dispatch`, sem impacto em PRs). A
  decisão de enxugar fica com o frontend (seção 9, F1).
- `docs/LIVE_*_PHASE_GATE.md`: são evidência histórica dos gates do monitor só-leitura, que ainda
  existe (`live_app`, `Dockerfile.api`). Foram anotados com o escopo, não apagados.
- `docs/pragma/PROMPT_CONTROLE_MISSION_CONTROL.md` e `FLOOR_CONTROL_VISUAL_FIDELITY.md`: prompts
  e registros de decisão, preservados como evidência.
- Dependências Python: `asyncpg` e `aiosqlite` não aparecem em `import`, mas são carregadas pelo
  dialeto da URL do SQLAlchemy; `mutmut` é usado pela CI. As dependências npm são todas usadas
  (área do frontend).

## 5. Varredura de segredos (sem valores)

- **Árvore versionada** (`git ls-files`): 37 ocorrências, todas falso-positivo:
  - mapeamentos `VAR=nome-do-segredo-no-Vault`: `infra/oci/scripts/fetch-secrets.sh:21-46` e
    `tests/unit/test_oci_infra.py:310`;
  - leituras de settings: `apps/api/app/{settings.py:230,245; oms_surface.py:138,140;
    signal_surface.py:58; equity_market_surface.py:41,50}`, `apps/executor/main.py:67` e
    `apps/watchdog/__main__.py:45-55`;
  - placeholders: `.claude/skills/theme-detector/SKILL.md:84` (`your…`) e
    `.claude/skills/portfolio-manager/scripts/tests/test_check_alpaca_connection.py:14`
    (fixture com `pragma: allowlist`);
  - URLs de banco locais ou de exemplo, com senha de dev `prediction` ou `<password>`:
    `.env.example:10`, `docker-compose.yml:15`, `docs/runbooks/oracle-setup.md:143-144`, e o
    gerador `infra/oci/scripts/create-secrets.sh:87`;
  - chave RSA gerada em teste: `tests/unit/test_chatgpt_connect.py:85` e a cópia em
    `third_party/the_creation_os/test_chatgpt_connect.py.txt:76`;
  - hash de integridade npm: `apps/web/package-lock.json:604`.
- **Árvore inteira**, incluindo os arquivos ignorados: só mais 2 ocorrências, em
  `.pytest_cache/v/cache/nodeids` (nomes de testes).
- **Histórico Git completo** (2122 commits, linhas adicionadas, padrões de alta confiança: bloco de
  chave privada, AWS, GitHub, Anthropic, OpenAI, Slack, Google, JWT, atribuições Binance, Alpaca
  ou `PROTO_CRED`): **0 ocorrências**.
- Nenhum arquivo `.env`, `.pem`, `.key`, `tfvars` ou `tfstate` versionado.
- **Rotação necessária: nenhuma.** Padrões conhecidos: o default de dev
  `CREATION_BRIDGE_SHARED_SECRET` em `docker-compose.yml:18` (texto "change-me", só local) e
  `GRAFANA_ADMIN_PASSWORD` em `.env.example:75` e `docker-compose.yml` (placeholder local). Nenhum
  dos dois é usado no deploy da Oracle, que lê os segredos do Vault.

## 6. CI e deploy: observações

- As actions estão fixadas por tag (`@v5`, `@v4`...), não por SHA. Fixar por SHA endurece a supply
  chain; não foi alterado para não mudar comportamento.
- `security.yml`: CodeQL com SARIF local bloqueante, `pip-audit` e `npm audit --audit-level=high`.
- O `graphify.yml` instala `graphifyy` sem versão fixada (não determinístico). É apenas artefato,
  sem gate de merge declarado.

## 7. Configuração externa ainda necessária

1. GitHub → *Settings → Environments* → `executor` com **Required reviewers**. Sem isso, o GitHub
   cria o ambiente sem proteção e o `:executor` se move sem aprovação.
2. Branch protection em `main` exigindo os jobs `python`, `mutation-safety`, `compose`, `web`,
   `Security` e `Graphify` (não verificável daqui).
3. Pacote `ghcr.io/<owner>/proto` público, ou credenciais de pull nas instâncias
   (`docs/runbooks/oracle-setup.md`, seção 6).
4. Segredos no OCI Vault (`infra/oci/scripts/create-secrets.sh`); chave Binance **testnet** com o
   IP de NAT liberado; opcionalmente `proto-alpaca-paper-key` e `/etc/proto/alpaca.conf`, ou
   `/etc/proto/xp.env` para o nó XP.
5. Prometheus e Grafana não fazem parte do deploy da Oracle: só existem no `docker-compose*.yml`.
   Para alertas em produção é preciso hospedar o Prometheus com `prometheus-trading.yml`.

## 8. Riscos e pendências

- `promtool check rules` e `terraform validate` não foram executados (seção 1).
- O aviso `ResourceWarning: unclosed database` aparece em
  `tests/unit/test_agent_roster.py::test_roster_route_is_labelled_virtual_and_read_only` e em
  `test_control_persistence.py::test_kill_switches_and_lockdown_survive_a_restart`. Não falha, mas
  indica engine SQLite não fechado no teste.
- `pyyaml` é usado pelos testes (`test_oci_infra.py`, `test_infra_contracts.py`) mas chega só de
  forma transitiva (`uvicorn[standard]`). Declará-lo em `dev` seria mais robusto; não mudei para
  manter a dependência mínima.

## 9. Achados para outras áreas

**Backend / migrations (área 1):**

- **M1. Drift entre os modelos e as migrações.** `alembic check` em uma base com
  `upgrade head` falha. Causas e correções:
  - (a) `OrchestrationBase.metadata` (`services/orchestration/store.py:15-20`, tabela
    `orchestration_job_runs`, criada em `0006`) não está em `target_metadata`
    (`migrations/env.py:30-40`). Correção: registrar esse metadata lá.
  - (b) A migração `0002` cria `ix_<tabela>_created_at` para as tabelas canônicas
    (`migrations/versions/0002_canonical_persistence.py:76-78`), mas os modelos não declaram esse
    índice. Correção: `index=True` em `created_at` nos modelos, ou `Index(...)` no
    `schema_registry`.
  - Depois disso, um teste com `alembic check` (ou `compare_metadata`) evita regressão.
- **M2. A tabela `decision_memory` não tem migração.** Está em
  `services/orchestration/memory.py:88` e só é criada por `create_all` (`persistence.py:105`).
  Correção: migração `0015_decision_memory` e registro em `env.py`.
- **M3. Instalações existentes não têm `alembic_version`.** O deploy cria o esquema só com
  `create_all` (`apps/api/app/persistence.py:99-110`, `apps/executor/runtime.py:150`). Alterações
  de coluna futuras (como `0003`, `0004` e `0011`) nunca chegam a essas bases. Correção sugerida:
  definir a política (rodar `alembic upgrade head` no arranque ou num job do `proto-deploy`, com
  `alembic stamp` assistido para as bases existentes, depois de verificar o esquema) antes da
  próxima migração que altere colunas.

**API, executor e watchdog (áreas 1 e 4):**

- **C1. Erro de validação ecoa a entrada.** Um `ValidationError` de `Settings` (por exemplo, com
  `OPERATOR_API_TOKEN` curto em produção) inclui `input_value={...}` com o dict de entrada, e o
  trecho final traz o valor do token. Reproduzido com valores fictícios. O `show_logs` do
  `proto-deploy` só mascara valores com 16 caracteres ou mais, e o token que falha por ser curto
  pode ter menos. Correção: `hide_input_in_errors=True` no `SettingsConfigDict` de
  `apps/api/app/settings.py:455`, `apps/executor/settings.py:16` e `apps/watchdog/settings.py:15`.

**Frontend (área 2):**

- **F1. Workflow de render obsoleto.** O `render-control-room.yml` (linhas 35, 42 e 80) ainda
  verifica e publica `room.webp`, `room-portrait.webp` e `anchors.json`, que a view Controle não
  usa mais (ela usa `room-photo.webp`). Decidir entre reduzir o workflow ao `robot.webp` e
  remover a parte do fundo, ou marcar como histórico. O mesmo vale para
  `docs/pragma/FLOOR_CONTROL_VISUAL_FIDELITY.md:16-18`.

## 10. Bloqueios

Nenhum bloqueio de acesso para o escopo desta área. Itens não executáveis no ambiente:
`promtool` (execução de binário baixado negada) e `terraform` (ausente).
