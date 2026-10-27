# Finalização do PROTO (2026-10-27): checklist e relatório final

Estados: `pendente`, `em execução`, `validado` ou `bloqueado`.

Fontes de verdade: `AGENTS.md`, `CLAUDE.md`, `docs/pragma/PRAGMA-CORE.md` e `docs/plans/`. O
relatório detalhado de cada área está em `docs/audits/finalization/<área>.md`.

O PR é #343, no branch `ccr-bc7ddea5-hpnzbk`. A base é `35dad8f` (o `main` depois do #342).

## Checklist

| # | Área | Fronteira de edição | Estado | Evidência |
|---|---|---|---|---|
| 1 | Backend, API e persistência | `apps/api/app`, `services/oms`, `services/execution`, `migrations` | validado | `finalization/backend.md` |
| 2 | Frontend, UX e fidelidade visual | `apps/web` | validado (*) | `finalization/frontend.md` |
| 3 | Agentes, modelos, pesquisa e calibração | `services/agents`, `signals`, `research`, `analytics` | validado | `finalization/agents.md` |
| 4 | Risco, execução, reconciliação e segurança | `services/risk`, `venues`, `apps/executor`, watchdog, promotion, drills | validado | `finalization/risk.md` |
| 5 | Infraestrutura, observabilidade, testes e limpeza | `infra`, `.github`, Docker, `pyproject`, `docs` | validado (*) | `finalization/infra.md` |
| 6 | Revisão independente do diff integrado | somente leitura | validado | 6 defeitos achados e corrigidos (abaixo) |
| 7 | Integração, aceitação e PR | coordenador | validado | seção "Verificações" |

(*) Partes que dependem de serviço real ficaram sem prova, e estão listadas em "Bloqueios".

## Principais correções, por área

Detalhes, arquivos e linhas estão nos relatórios de cada área.

### Risco e execução
- O limite de 0,25% passou a valer **por posição acumulada**. Antes ele valia por ordem e deixava
  compras se empilharem. A conta agora inclui:
  - a posição já mantida;
  - as compras que ainda estão abertas na corretora, sem truncar (`/v1/orders?open=true`).
  Se essas compras não puderem ser lidas, o sistema bloqueia risco novo. Saídas continuam
  liberadas.
- Spread acima de 200 bps impede abrir risco novo.
- Uma bolsa sem horário de pregão modelado é tratada como fechada.
- O halt do executor sobrevive a um reinício.
- Uma resposta truncada ou ilegível conta como falha para o watchdog, em vez de derrubá-lo.
- O XP trava se o login sair da conta DEMO.
- Erros da Binance não carregam mais a query assinada.

### Backend
- O ledger em SQLite guarda valores exatos, sem passar por float.
- Comandos recusados aparecem no journal como `OPERATOR_COMMAND_REJECTED`.
- Iniciar o motor com o kill switch desarmado devolve 409.
- Os rótulos de origem de dados ficaram verdadeiros.
- As ordens trazem `fee_asset`.
- Os sinais do dia são separados por origem.
- As migrações não têm drift, e `decision_memory` ganhou migração própria.
- Os erros de settings não ecoam segredos.

### Agentes
- O gate do revisor tratava SELL de forma invertida; foi corrigido.
- O bloqueio de crescimento passou a ser uma checagem explícita, e não mais um `assert`.
- Lacunas no feed:
  - não confirmam entradas;
  - não emitem sinal com hora atrasada;
  - **mantêm as saídas**.
- As entradas `NaN` e `inf` são recusadas.

### Frontend
- O selo "LIVE" passou a exigir um frame recebido nos últimos 15 s.
- Leituras que falham mostram "—" ou UNAVAILABLE, nunca números inventados.
- Os rótulos de modo foram corrigidos.
- A página não rola mais na horizontal em 1024 px.
- O painel de conversa deixa explícito o que é explicação, proposta e execução.
- O botão "Stop engine" continua usável quando o estado é desconhecido.

### Infraestrutura
- `apps.watchdog` entrou no pacote, o que corrigiu a falha do CI.
- No PostgreSQL, `alembic upgrade head` passou a funcionar além da revisão 0004.
- Testes de contrato novos para métricas, Alembic e CI.
- O `.gitignore` agora cobre bancos de dados e chaves `.pem`.
- O workflow de render confere os assets que estão de fato em uso.
- Os documentos foram alinhados ao estado atual: `OPERATIONS.md`, com a seção "Inicialização
  reproduzível", `README.md`, `RELEASE_READINESS.md`, os avisos nos `LIVE_*.md`, `rollback.md` e
  `oracle-setup.md`.

### Deploy da Alpaca
- O container é recriado quando a chave muda no Vault.
- Ele para depois de 5 falhas.
- Uma falha passageira do Vault mantém o container saudável.
- Um 401 na partida aponta qual variável verificar, sem mostrar o valor.

### Revisão independente

Os 6 achados foram corrigidos e cada um tem teste de regressão:
1. **Deploy da Alpaca:** uma falha passageira do Vault removia o container saudável.
2. **Ledger SQLite:** a mudança para texto exato é só de ida. Ficou documentado em `rollback.md`.
3. **Lacunas no feed:** a correção também suprimia as saídas.
4. **Compras abertas:** só as 500 ordens mais recentes entravam na conta.
5. **Botão "Stop engine":** ficava desabilitado quando o estado era desconhecido.
6. **Migração 0015:** o `downgrade` apagava dados criados pelo runtime.

A revisão também achou um problema que já existia: o PostgreSQL falhava na revisão 0004.

## Limpeza e removidos

Nenhum arquivo do produto foi apagado. Também não foram apagados dados, migrações aplicadas nem
evidências. As remoções foram de código, todas com referências checadas antes (imports
dinâmicos, build, testes, deploy e documentação):

| Removido | Justificativa |
|---|---|
| `drawFloor` | Sem uso desde as texturas CC0. |
| `to_brl` e `iso` em `research` | Sem nenhuma referência. |
| `_floor_to_lot` duplicado no roster | Já existe em `sizing`. |
| Dependência `pytest-cov` | Nada a usa. |

Os avisos de depreciação do three.js e do Vite foram zerados.

## Verificações (branch final)

- `ruff check apps services tests`: sem achados.
- `python -m pytest`: **1488 passed**. A base tinha 1412.
- Web: `npm ci`, `typecheck`, `build`, `contract-check` (31 arquivos) e `bundle-check` passaram.
  Carga inicial de 641 KB, dentro do orçamento de 700 KB.
- CI da #343: verde nas 11 verificações em `7c88f94`, com as cinco áreas integradas. Os commits das
  correções da revisão vão pelo CI do PR.
- Drills (seed 0), todos aprovados e reproduzidos:

  | Drill | Casos | Digest |
  |---|---|---|
  | ADVERSARIAL | 32 | `3913251f82bb…` |
  | RECOVERY | 15 | `045751553aaf…` |
  | ROLLBACK | 17 | `128039b6cda1…` |

  O digest ADVERSARIAL mudou de propósito; evidências de promoção antigas precisam ser registradas
  de novo.
- Alembic:
  - SQLite vazio: `upgrade head`, depois `alembic check`, sem drift.
  - **PostgreSQL 16 local:** sem a correção, a falha (`varchar(32)`) se reproduz. Com ela,
    `upgrade`, `check` e `downgrade base` seguido de `upgrade head` passam.
- Aceitação na API local (SQLite com o plano de controle durável):
  - `/health`, `/ready`, `/metrics`, `/v1/floor/summary`, `/v1/agents/roster`, `/v1/risk/status`,
    `/v1/oms/ledger` e `/orchestration/status` respondem 200.
  - POST sem token responde 401; com token, 200.
  - O kill switch global e o journal (`RISK_KILL_SWITCH_CHANGED`) **persistem depois de
    reiniciar**, e a cadeia do journal é válida.
- Playwright em 1440, 1024 e 390 px, contra a API local:
  - nenhum `pageerror`;
  - nenhuma largura com rolagem horizontal;
  - as 66 chamadas da UI existem na API;
  - capturas no scratchpad da sessão.
- Varredura de segredos (árvore e histórico de 2122 commits): nada a rotacionar.

## O que mocks e testes **não** provam

- **Corretoras:** Binance, XP e Alpaca são simuladas nos testes; nenhuma ordem foi enviada a
  servidor real nesta finalização.
- **Conversa com os agentes:** o conector ChatGPT (contrato do #335, mantido intacto) não foi
  chamado contra o serviço real.
- **Revisor Claude e FinRobot:** são fakes nos testes.
- **PostgreSQL:** não houve suíte completa nele, só as migrações e o `alembic check`.
- **Navegador:** a renderização 3D foi conferida em software (SwiftShader), não em GPU real.

## Configuração externa ainda necessária (operador)

1. **Alpaca paper:** o executor recebe `401 unauthorized` da Alpaca.
   - A auditoria de risco concluiu que nenhum caminho do código gera 401 com um par paper válido.
     Um teste fixa os bytes exatos enviados.
   - É preciso conferir o par no Vault (chave `PK…`, sem aspas ou espaços, sem troca entre chave e
     segredo, da mesma conta paper). O diagnóstico via `curl` está no histórico do PR.
   - Corrigido o valor, o executor pega a chave nova sozinho em até 5 minutos.
2. **GitHub:** configurar *Required reviewers* no ambiente `executor` e proteção de branch em `main`.
3. **Prometheus e Grafana:** não fazem parte do deploy na OCI. Para ter alertas em produção, é
   preciso hospedá-los.
4. **Bancos de produção criados por `create_all`:** não têm `alembic_version`. A política de
   `stamp` precisa ser decidida antes da próxima migração que altere colunas (M3 em `backend.md`).
5. **Executores XP e Alpaca:** um ledger SQLite antigo continua legível. A precisão total vem com o
   volume recriado; ver `rollback.md`.

## Bloqueios e pendências conhecidos

- **Ativação da Alpaca:** depende do item 1 acima. Não é bloqueio do código.
- **`AGENT_REVIEWER=claude`:** não há gerador de casos históricos, então falta evidência para a
  promoção. Continua desligado.
- **`KillSwitchState.LOCKED`:** nunca é atribuído, então o caminho LOCKED→RESET_PENDING fica
  inativo. Registrado, sem mudança.
- **Volatilidade no sizing:** não entra; o stop é fixo em 2%. Registrado como limitação.
- **Produção:** merge e deploy dependem da sua autorização. Nada foi implantado nesta finalização.

**Não está "100% pronto":** os itens acima dependem de configuração externa ou de decisão de
produto. As jornadas essenciais em paper, testnet e demo estão validadas, com as evidências acima.
