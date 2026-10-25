# Prompt — vista "Controle" (mission control) igual à referência

Cole este prompt inteiro no Codex. Ele é autocontido.

---

## Contexto

Repositório `dossantostampafl-lab/Proto`. Antes de qualquer coisa leia `AGENTS.md` (vinculante),
`CLAUDE.md` e `docs/pragma/PRAGMA-CORE.md`. Trabalhe num branch novo a partir da `main`, abra PR
em rascunho e não faça merge.

O dashboard web está em `apps/web` (React + Vite + three.js). A aba **Pregão** (`src/views/Floor.tsx`)
tem três vistas: Realista | Prédio | **Controle**. A vista Controle está em
`src/views/floorControl/` (`FloorControl.tsx` = sobreposições React com dados reais;
`engine.ts` = sala 3D em three.js). A versão atual é 3D em tempo real e o dono do produto a
rejeitou: **precisa ficar visualmente igual à referência**.

## A referência (o que "igual" significa)

Um vídeo de um painel "mission control" visto de cima, em ~60–70° de inclinação, estilo render
fotorrealista (Unreal/Blender), noite, iluminação azul/ciano com acentos âmbar:

1. **Centro:** grande área circular rebaixada com um **globo holográfico da Terra** azul com
   continentes verdes, brilhando, flutuando acima de um **anel de consoles curvos com telas azuis
   acesas**. Em volta, vários **anéis de luz azul concêntricos** no piso, alguns tracejados, um
   anel de arcos girando, e uma mesa de comando curva atrás do globo.
2. **Esquerda e direita:** blocos de **mesas de trading** em fileiras (2–3 monitores acesos por
   mesa, cadeiras pretas), em salas separadas por **divisórias de vidro baixas com faixa de LED
   âmbar** no topo, que reflete no piso. **Vasos de plantas** nos cantos. Lounge com sofás num canto.
3. **Piso** escuro azul-marinho brilhante com reflexos, linhas tracejadas de corredor.
4. **Personagens:** robozinhos amarelos (um por agente) andando entre a mesa e o hub central, com
   uma **trilha tracejada laranja** e um **marcador circular ciano** no destino.
5. **Etiquetas** escuras arredondadas sobre cada robô: `● NOME` (bold, branco, ponto verde) e uma
   linha de status em fonte mono verde, terminando em "…".
6. **Placar** no topo central, cartão escuro translúcido de cantos arredondados: rótulo
   espaçado em maiúsculas, **número grande branco**, ícone, divisor vertical, segundo bloco com
   número grande e barra de progresso verde.
7. **Painel "FONTES"** à direita, com pin verde e lista de fontes.
8. **Fila** embaixo, cartão com **borda âmbar**: título bold ("N ações recentes no pregão"),
   botão ×, item atual, paginação em pílula âmbar "1 / 55 ›", e a legenda "PREGÃO" com
   `● Nome papel` de cada agente.
9. Cabeçalho "PROTO · CONTROLE" à esquerda e data/hora à direita.

## Abordagem obrigatória (é a única que chega a "igual")

Uma cena 3D em tempo real com primitivas **não** chega ao visual. Faça como a referência:
**fundo pré-renderizado fotorrealista + sobreposições vivas**.

1. **Render offline com Blender (bpy) em Python.** Crie `tools/render_control_room/render.py`,
   executável com `pip install bpy==5.2.3 pillow` e `python tools/render_control_room/render.py`.
   O script:
   - modela a sala inteira em código (itens 1–3 acima, sem pessoas, sem robôs, sem texto, sem
     logos, **telas sem números**): materiais Principled com reflexo no piso (roughness ~0.2),
     vidro, emissão nas telas, anéis, faixas âmbar e globo (textura equiretangular desenhada com
     Pillow, mais casca em wireframe emissiva e halo);
   - ilumina com área-lights quentes sobre os blocos de mesas, luz ciano no hub e mundo azul
     escuro; renderiza com **Cycles na CPU**, ~128 samples e denoise, 1920×1080;
   - aplica **bloom** em pós-processamento (Pillow/numpy: bright pass + blurs somados);
   - grava `apps/web/public/floorcontrol/room.webp` (≤ 600 KB) e `room-portrait.webp`
     (enquadramento 9:16 para celular, ≤ 400 KB);
   - renderiza o **sprite do robô** (próprio do PROTO, amarelo com visor ciano, **não** Minion ou
     qualquer personagem conhecido) em 4 quadros de caminhada, fundo transparente, com o mesmo
     ângulo de câmera: `apps/web/public/floorcontrol/robot.webp` (sprite sheet, ≤ 120 KB);
   - exporta `apps/web/src/views/floorControl/anchors.json` com as coordenadas **projetadas na
     imagem** (0–1, origem no canto superior esquerdo) de: cada assento (cadeira) com o
     andar (`binance` ou `xp`), o ponto do hub para onde cada assento caminha, e o centro e o raio
     do globo. Calcule com `bpy_extras.object_utils.world_to_camera_view`; nada à mão.
   - Documente fontes e licença em `apps/web/public/floorcontrol/ASSETS.md` (tudo gerado pelo
     script, sem assets de terceiros).
2. **`FloorControl.tsx`** passa a ser `<img>`/CSS do fundo + sobreposições, **sem three.js** (o
   chunk fica pequeno). Mantenha tudo o que já é real: `GET /v1/floor/summary` (placar),
   `/v1/agents/roster` (agentes), `/v1/oms/ledger` (perda diária), estado do pregão (fila, fontes),
   `useXpLane`. Robôs como sprites posicionados em `anchors.json`; quando uma estação do pipeline
   trabalha no símbolo do agente, ele **anda** até o ponto do hub (transição CSS/rAF), com a trilha
   tracejada laranja em SVG e o marcador ciano. Etiquetas, placar, fontes e fila com o estilo
   descrito acima. Um brilho CSS pulsando sobre o globo (posição de `anchors.json`).
3. Responsivo: em telas retrato use `room-portrait.webp` e as âncoras dele; sem rolagem
   horizontal em 390 px.

## Regras inegociáveis (CI e AGENTS.md)

- **Só dados reais.** Nenhum número inventado; fonte ausente mostra "—" e UNAVAILABLE. Mantenha os
  rótulos verdadeiros já existentes (`MODE UNAVAILABLE`, `TESTNET · NO REAL MONEY`,
  `SUMMARY UNAVAILABLE`, `AGENT ROSTER UNAVAILABLE`, `NOT CONFIGURED`, "perda diária: não medida",
  "fora do pregão", "≥" para janela cheia). `financial_connectivity`/`real_money_execution` ficam
  falsos. Nenhuma palavra de dinheiro real na UI.
- Não importe `services.venues` nem `apps.executor` em `apps/api`. Nada de credenciais.
- Movimento: respeite `prefers-reduced-motion`, botão "Pausar animação", pause fora da tela
  (`IntersectionObserver`) e com a aba oculta (`visibilitychange`).
- `localStorage` só em `floorViewPref.ts`.
- Orçamento: atualize `apps/web/scripts/bundle-budget-check.mjs` e
  `apps/web/scripts/contract-check.mjs` para a nova forma (fundo + sprites carregados **só** quando
  a vista Controle é escolhida, nunca pré-carregados; teto: room ≤ 600 KB, portrait ≤ 400 KB,
  robot ≤ 120 KB, chunk `FloorControl-*` ≤ 60 KB). Não enfraqueça nenhuma outra regra dos checks.
- Atualize a seção "Bundle budget" do `CLAUDE.md`.

## Validação (cole as saídas no PR)

```bash
pip install -e '.[dev]' && ruff check apps services tests && pytest
cd apps/web && npm ci && npm run typecheck && npm run build && npm run contract-check && npm run bundle-check
```

Depois rode a API (`AGENT_ROSTER_ENABLED=true uvicorn apps.api.app.main:app --port 8000`) e tire
**screenshots com Playwright** da vista Controle a 1440×900 e 390×844, com 6 agentes no roster.
Anexe-as ao PR ao lado da referência. Critério de aceite: quem olha as duas imagens lado a lado
reconhece a mesma sala (globo central com anéis, mesas à esquerda e à direita com divisórias
âmbar, placar no topo, fila âmbar embaixo, robôs com etiquetas), com dados reais.

## Entrega

PR em rascunho com: script de render, assets gerados, `anchors.json`, `FloorControl.tsx`
novo, checks atualizados, screenshots e a lista **ESTADO / ALTERAÇÕES / VERIFICAÇÕES /
RISCOS-PENDÊNCIAS**. O Claude Code vai revisar o PR.
