# Finalização — Frontend, UX e fidelidade visual (2026-10-27)

Área 2 de `docs/audits/2026-10-27-finalization.md`. Fronteira de edição: `apps/web/**` e este relatório.
Base: `2b12863`. Branch: `worktree-agent-a8f6b6d1398bdf354`.

**ESTADO: PARTIAL.** Código, verificações locais e jornadas Playwright estão validados. As
pendências estão no fim do documento: dados reais de mercado não estão disponíveis no sandbox e
alguns comportamentos (testnet/XP vivos, conversa ChatGPT autenticada) não puderam ser exercitados.

## Verificações

| Comando (em `apps/web`) | Antes | Depois |
|---|---|---|
| `npm ci` | ok | ok (lockfile inalterado, nenhuma dependência alterada) |
| `npm run typecheck` | ok | ok |
| `npm run build` | ok, com o aviso `advancedChunks option is deprecated` | ok, sem avisos |
| `npm run contract-check` | `ok (31 source files)` | `ok (31 source files)` (nenhuma asserção alterada) |
| `npm run bundle-check` | 637 187 B no carregamento inicial; JS do 3D 680 118 B | 641 334 B no carregamento inicial; JS do 3D 680 662 B (todos dentro do orçamento) |

- **API local:** `AGENT_ROSTER_ENABLED=true OPERATOR_API_TOKEN=<local> uvicorn apps.api.app.main:app --port 8000`,
  com sqlite em memória e servindo `apps/web/dist`.
- **Playwright:** 1.56.1 com o Chromium de `/opt/pw-browsers` e WebGL por SwiftShader. Script:
  `scratchpad/drive.mjs`. Ele visita cada view em 1440×900, 1024×768 e 390×844, alterna
  Realista/Prédio/Controle, abre cada mesa e, em 1440, clica em cada botão de cada view. Também
  registra erros do console, requisições com falha e overflow horizontal.
- **Capturas:** ficam em
  `/tmp/claude-0/-home-user-Proto/0c512a93-31d8-5484-9b26-e60ca14ee4be/scratchpad/shots/`.
  O prefixo `before-*` marca a base e `after-*` a versão corrigida, no formato
  `<largura>-<view>[-<modo>].png`. Os diálogos das mesas estão em `*-Floor-dialog-N.png`.

### Console e rede

**Antes:** 13 entradas.
- Avisos do three.js: `PCFSoftShadowMap has been removed` e `THREE.Clock … deprecated`.
- HTTP 503 em `/live/history/*` e `/market-lifecycle`.
- HTTP 404 em `/live/analytics/*`.
- HTTP 401 nos comandos sem token de operador.

**Depois:** 7 entradas, todas esperadas.
- Os avisos do three.js foram eliminados.
- Os 503 e 404 vêm de não haver feed de mercado no sandbox.
  - `/live/history/*` e `/market-lifecycle` respondem 503 (pesquisa sintética desativada).
  - `/live/analytics/*` responde 404 (sem amostras).
- Os 401 vêm dos comandos sem token. A UI mostra "Operator token required — set it in System" no
  Operator log.

Nenhum `pageerror` e nenhuma falha de rede além dessas.

**Overflow horizontal.** Antes, em 1024 px, as 7 views rolavam na horizontal: a barra de status
chegava a 1179 px. Depois, nenhuma view rola na horizontal em 1440, 1024 ou 390. As tabelas largas
rolam dentro de `.table-wrap`.

## Mapeamento UI para endpoint

Todas as 66 chamadas da UI existem na API, com o método correto. A conferência foi feita contra o
`/openapi.json` da API em execução e o grep de `@router.websocket`.

- **GET:**
  - `/health`, `/live/*`, `/system/status`, `/market-lifecycle`, `/paper/status`, `/replay/status`
  - `/v1/{floor/state,floor/summary,agents/*,oms/*,risk/*,signals/recent,strategy/status,watchdog/status,drills/e2e/latest,portfolio}`
  - `/orchestration/*`, `/universe`, `/deployment/info`, `/creation/status`, `/shadow/status`
  - `/models/calibration`, `/pnl/attribution`, `/hawkes/{symbol}`, `/analytics/greeks/{market_id}`
- **POST:**
  - `/killswitch/*`, `/paper/{start,stop}`, `/simulation/reset`, `/replay/*` (9 rotas)
  - `/shadow/{start,stop}`, `/v1/simulate`, `/v1/risk/killswitch`, `/v1/oms/reconcile`
  - `/v1/oms/orders/{id}/{cancel,resolve}`, `/v1/drills/e2e/run`, `/conversation/*` (6 rotas)
- **WS:** `/ws/market-data`, `/ws/floor`.

Nenhuma chamada órfã.

## Estado por view

| View / controle | Estado | Evidência |
|---|---|---|
| **Floor · Realista (3D)** | validado | `after-{1440,1024,390}-Floor-Realista.png`. Mostra a sala, as 7 mesas, a bancada de agentes com etiquetas `AG-000N · R$200,00` e os selos PAPER / FLOOR STATE LIVE / MARKET. O selo de mercado agora diz LAST KNOWN / REST quando não chegam frames. |
| **Floor · Prédio** | validado | `after-1440-Floor-Prédio.png`. Roster, ledger e 2 andares aparecem. Na base, a captura feita aos 6 s mostrava `AGENT ROSTER UNAVAILABLE`, porque a renderização por software estourou o timeout de 3,5 s. O poll seguinte, em 10 s, recupera (estado de recuperação validado). |
| **Floor · Controle** | validado | `after-{1440,1024,390}-Floor-Controle.png`, comparadas a `public/floorcontrol/room-photo.webp`. |
| Desk cards e drawers (6 mesas) | validado | Em 390, os 6 diálogos abrem e fecham com Escape (`after-390-Floor-dialog-*.png`). Em 1440 o drawer lateral foi conferido em `probe-drawer-1440.png`, mostrando controles, limites e watchdog com LIVE · updated. Nas larguras maiores, alguns cliques automáticos estouraram o timeout porque a renderização 3D por software ocupa a thread; é um efeito do ambiente, não um defeito (clique isolado: ~1,5 s com SwiftShader). |
| Controls / Watchdog / Promotion / Agent feed | validado | Mostram "—" e "unavailable" sem estado. Halt all trading sem token responde 401 e registra a falha. |
| **Command** | validado | Os tiles mostram "—" e "acquiring signal" sem dados. LIVE PUBLIC recebe o selo `STALE · LAST KNOWN` quando o quote passa de 30 s. Paper book: UNAVAILABLE em caso de falha, e o risk score fica em "—" sem portfolio. Kill switch e Re-arm alternam conforme o estado. |
| **Agents** | validado | `after-*-Agents.png`. Roster com 6 agentes, detalhe, evidência (NOT CONFIGURED) e contratos. ReadState esconde dados antigos quando a leitura falha. |
| **AgentConversation** | validado (contrato) / pendente (fluxo autenticado) | Os botões aparecem e o host público explica o túnel SSH. Verify sem token responde 401 e a mensagem aparece em status. O fluxo OAuth real não foi exercitado. |
| **Trade** | validado | O engine desconhecido mostra "—" e desabilita Start e as ordens. O selo de Autonomy segue `data_mode` (PAPER aqui). Positions mostra UNAVAILABLE quando a leitura falha. Buy e Sell ficam desabilitados sem quote ou sem pesquisa, com a razão exibida. |
| **Lab** | validado | Sem mercados de pesquisa (503), mostra "Synthetic research is disabled". Samples mostra "—" sem dados, em vez de 0. |
| **Replay** | validado | Start fica desabilitado sem arquivo. O carregamento de JSON inválido mostra o erro. Cursor, speed e progress mostram "—" em caso de falha. Seek exige um inteiro ≥ 0. |
| **System** | validado | O token fica salvo e limpo apenas em `sessionStorage`. Safe scope, durable, shadow e ProtoBrain mostram "—" quando desconhecidos. Decision memory mostra "—" em vez de 0. Universe mostra UNAVAILABLE em caso de falha. O Operator log registra os 401. |
| Header / ticker / footer | validado | Em ≤1280 px as abas vão para uma linha própria. Stream: "REST" quando o socket está aberto mas mudo. |
| Reconexão WS | validado (código) | Os dois sockets usam backoff exponencial até 15 s. A reconciliação REST roda a cada 3 s. O re-poll de `/v1/floor/state` roda a cada 5 s. Uma queda real do socket não foi simulada. |

## Correções (commits)

1. **`fix(web): truthful stream, mode and unknown states; tablet header`**
   - **Stream.** `useMarket.connected` passou a significar socket aberto **e** frame recebido nos
     últimos 15 s. Antes, um socket aberto e mudo fazia o 3D, o Prédio, o Controle e o header
     mostrarem `LIVE PUBLIC` sem dados.
   - **Números inventados removidos:**
     - System: Records/Resolved/Unresolved `?? 0`;
     - Lab: Samples `?? 0`;
     - Replay: `0 / 0` e `0%`;
     - Trade: `STOPPED` com o status desconhecido;
     - Command: risk score `0%`.
   - **Valores antigos.** Leituras com falha não exibem mais o último valor bom como se fosse atual,
     no padrão já usado pelo Agents.
   - **Trade · Autonomy.** O selo deixou de ser `TESTNET` fixo e passou a seguir o modo real.
   - **Controle · Fontes.** A linha "Binance Spot · Online · testnet" vinha da frescura do floor
     state. Agora reflete o venue real: o paper venue, ou o ledger do executor em TESTNET. A linha
     de sinais deixa claro que conta TV + estratégia própria.
   - **Layout e estilo:**
     - fim do overflow de 1024 px no header;
     - status (READY/RUNNING) dentro de `Stat` agora legíveis: antes herdavam o estilo de rótulo,
       10 px e cor muted;
     - `title` nos valores truncados.
2. **`fix(web): agent conversation states explanation vs proposal vs execution`**
   - **Contradição corrigida.** O painel "readiness" dizia "Unavailable in this deployment"
     enquanto a conversa existia. Agora descreve o contrato do PR #335: só explicação, loopback mais
     token de operador e túnel SSH a partir de host público.
   - **Explicação, proposta e execução.** A conversa lista explicitamente as três:
     - explicação: é o que este painel faz;
     - proposta: só os agentes, nunca o chat;
     - execução: só o OMS com permit, em paper ou testnet.
   - **Estilo.** Os botões e campos passaram ao estilo do dashboard.
   - **Escopo.** Nenhum endpoint, checagem de host ou permissão foi alterado.
3. **`chore(web): clear three.js and rolldown deprecations; drop dead drawFloor`**
   - `PCFShadowMap` foi declarado explicitamente. O three já fazia esse fallback, então a imagem
     não muda.
   - `THREE.Timer` substituiu `Clock`.
   - `output.codeSplitting` substituiu `advancedChunks`. O grupo `Floor3DThree` é o mesmo e o
     bundle-check passa.

## Arquivos e código removidos

- `drawFloor` em `src/views/floor3d/screens.ts`: nenhuma referência em src, scripts, docs nem
  em import dinâmico. O piso usa as texturas CC0 de `public/floor3d/textures/`.
- **Nenhum arquivo removido.** As referências foram conferidas para todos os arquivos de `src/`
  e `public/`. As texturas são carregadas por template (`textures/${key}_color.webp`) e todas
  são usadas.
- Exports sem uso externo, como os tipos de `deskData.ts`, são usados dentro do próprio módulo e
  foram mantidos.

## Fidelidade visual

| Referência | Veredito |
|---|---|
| `public/floorcontrol/room-photo.webp` (mockup aprovado) vs Controle | **Fiel.** Os painéis Total, Pregão hoje, Fontes, Agentes, Atividade, Fila/Eventos e as 4 + 2 etiquetas de agente ficam sobre as posições do mockup, em 1440 e 1024. No telefone (390), a foto aparece em largura total com máscaras, e os painéis vão abaixo, como no layout já aprovado. Nenhum valor de amostra do mockup fica visível. |
| `src/assets/floor-room.webp` (sala foto de fallback) | Não exercitada, porque o WebGL estava disponível. O código não foi alterado. |
| `docs/dashboard/trading-floor.md` (3D: 7 mesas em arco, video wall, bancada de 6, placas, selos) | **Fiel** (`after-1440-Floor-Realista.png`). |
| Prédio (sem imagem de referência no repo) | Visual existente preservado. Não há referência para comparar. |

## Ativos de `public/floorcontrol` (para o workflow)

Somente estes três são referenciados:

- **`room-photo.webp`:** `FloorControl.tsx:73` e `contract-check.mjs:175`.
- **`robot.webp`:** `FloorControl.tsx:92`.
- **`ASSETS.md`:** exigido por `bundle-budget-check.mjs:83`.

`room.webp`, `room-portrait.webp` e `src/views/floorControl/anchors.json` **não existem e não são
usados**. O passo "Check render and anchors" de `.github/workflows/render-control-room.yml` falharia
se fosse disparado.

## Achados de outras áreas (não editados)

- **Backend:** `apps/api/app/floor_surface.py:263` (`_SIGNAL_EVENTS`) e `:289`.
  - **Problema:** `signals_today` soma TradingView e PROTO_STRATEGY.
  - **Proposta:** expor também `tradingview_signals_today` e `strategy_signals_today`, para o
    Controle mostrar a linha "Estratégia própria" sem misturar as duas fontes.
- **Infra:** `.github/workflows/render-control-room.yml`, no passo "Check render and anchors".
  - **Problema:** o passo valida ativos que o Controle não usa mais.
  - **Proposta:** validar `room-photo.webp` (≤ 700 KB) e `robot.webp`, e remover o bloco de
    `anchors.json`. Também confirmar se `tools/render_control_room/render.py` ainda deve gerar
    `room*.webp`.
- **Docs:** `docs/dashboard/trading-floor.md` não descreve as vistas Prédio e Controle (só o 3D) e
  pode ganhar uma seção sobre elas (área 5).

## Riscos e pendências

- O feed público de mercado está indisponível no sandbox: `/live/history` responde 503 e não há
  frames. Por isso os estados LIVE de preço (quotes, sparkline e analytics com dados) não foram
  vistos com dados reais, só os estados "—", STALE e REST.
- Não foram exercitados com dados:
  - o modo TESTNET com executor;
  - a lane XP/B3 em LIVE ou HALTED;
  - as ações de operador com token: o drawer do OMS com cancel/resolve, o reconcile e a parada
    global de fato;
  - o fluxo ChatGPT autenticado.
- A renderização por software (SwiftShader) é lenta. Os cliques automáticos nas mesas com o 3D
  ativo podem estourar o tempo limite; manualmente funcionam (`probe-drawer-1440.png`).
- Não há testes unitários de frontend no repo: o CI de web roda os 4 scripts acima. Nenhuma
  checagem foi enfraquecida.
