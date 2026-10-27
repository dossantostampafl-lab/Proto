# Finalização — Risco, execução, reconciliação e segurança (área 4)

Branch `finalization/risk`, a partir de `2b12863`. Fronteira: `services/risk/**`, `services/venues/**`,
`apps/executor/**`, `apps/watchdog/**`, `services/watchdog/**`, `services/promotion/**`,
`services/drills/**` e seus testes. Nada foi enviado (sem push). Nenhuma chamada de rede real:
toda verificação é offline (fakes, `httpx.MockTransport`, sqlite).

**ESTADO: validado (código e testes locais).** CI, merge e deploy: não executados (fora do escopo).

## Itens

| # | Item | Estado | Evidência / correção |
|---|---|---|---|
| R1 | Limites constitucionais (R$10k, 0,25%, 1%, 5%, 1,0x) | validado + corrigido | `services/execution/constitution.py` inalterado. **Defeito:** `risk_per_position` media só a ordem nova; compras pequenas empilhadas formavam posição várias vezes acima de 0,25%. Agora conta posição atual + compras ainda em aberto/UNKNOWN (`AccountState.open_buys`) + ordem (`6370a93`, `4c26e77`). O oráculo independente do drill adversarial passou a contar a posição e acusou **7 violações** no seed 0 antes da correção, 0 depois |
| R2 | Exposição/alavancagem bruta | validado | `gross_leverage` = exposição + notional ≤ min(equity, ref)×1,0. Livro desconhecido (API: exposição infinita) recusa risco novo — teste `test_an_unknown_book_adds_no_risk` |
| R3 | Liquidez | corrigido (aperto) | Novo check `quote_spread`: spread > `max_spread_bps` (padrão 200, limitado a (0, 500]) recusa risco novo; saídas reduce-only continuam permitidas (`4c26e77`) |
| R4 | Volatilidade / concentração | parcial | Concentração por instrumento agora coberta por R1. Volatilidade **não** é modelada: `stop_distance` fixo de 2% (ver Pendências) |
| R5 | Drawdown / perda diária / latch | validado | `account_breach` usa min(base, capital de referência); base desconhecida = violada; governor só aperta; release recusado enquanto violado (testes existentes) |
| R6 | Conversão FX só aperta | validado | `reference_capital_in`/`canary_capital_in` usam o ask; `conservative_equity` = min(equity, ref convertida, 10.000); taxa ausente ⇒ 0 ⇒ nada novo. Frescor da taxa vive em `apps/api/app/fx_rates.py` (não auditado aqui) |
| R7 | Fail-closed sem cotação/estado | validado | `fresh_quote` (presente, não cruzada, idade ≤ 10 s, não futura); executor inalcançável ⇒ exposição infinita (API) |
| R8 | Horário de sessão por venue | corrigido | Fuso sem sessão modelada recebia o horário da B3 por adivinhação; agora fechado (`48b911c`). B3 10:00–17:55, NY 9:30–16:00, cripto 24/7; Alpaca também consulta `/v2/clock` |
| R9 | Permits (HMAC/Ed25519, ≤60 s, uso único durável) | validado | `compare_digest` no HMAC; Ed25519 no executor (produção usa `EXECUTOR_PERMIT_PUBLIC_KEY`); TTL validado no contrato e no signer; gasto por PK em `consumed_permits` antes do envio (`SqlPermitLedger`) |
| R10 | Kill switches / lockdowns / watchdog só-halt | validado + corrigido | Tokens do watchdog só leem status e haltam (testes existentes). **Defeito:** resposta 200 não-JSON (página de proxy) derrubava o ciclo ⇒ nada era haltado, todo ciclo (fail-open). Agora conta como inalcançável e dispara quando sustentado; timestamp naive = stale (`095bcc5`). `repr(Watchdog)` imprimia os tokens (`710a01b`) |
| R11 | Halt do executor / release recusado em divergência | corrigido | **Defeito:** halt só em memória; um restart (inclusive `on-failure` do podman) liberava um halt do watchdog com venue consistente. Agora persistido em `executor_halts` ao lado do ledger, restaurado no start, apagado só pelo release do operador (`c4f9a4b`) |
| R12 | Executor: único com credenciais; real-money recusado | validado | `test_venue_isolation` intacto e passando (pegou inclusive uma tentativa minha de citar `PROTO_CRED_` fora de `credentials.py`, resolvida com `variable_prefix`). Settings recusam LIVE; `Executor.start` e `OrderManager` recusam `real_money_execution`; Alpaca: host fixo `paper-api`, sem redirects; Binance: testnet; XP: DEMO |
| R13 | XP demo | corrigido | Modo da conta lido só no connect. Agora `/v1/balances` (toda reconciliação) trava LIVE se o terminal sair de DEMO e todo submit seguinte é recusado antes da bridge; 2xx ilegível = UNKNOWN, 4xx não-JSON = recusa, 3xx = UNKNOWN (`23dda5c`) |
| R14 | Idempotência (client order id) | validado | id derivado do intent; reenvio duplicado devolve a ordem existente (Binance/Alpaca/bridge); executor devolve a ordem de um intent já visto |
| R15 | Timeouts/5xx ⇒ UNKNOWN ⇒ reconcile | validado + corrigido | Todos os adapters: transporte/5xx/3xx ⇒ `VenueOutcomeUnknown`; OMS marca UNKNOWN e resolve só por consulta. Binance agora trata 3xx como UNKNOWN (`3e76edd`) |
| R16 | Recuperação após restart | validado | `OrderManager.recover`: NEW ⇒ REJECTED + liberação; abertas ⇒ UNKNOWN; start reconcilia antes de aceitar ordens; halt agora também sobrevive (R11) |
| R17 | Divergência ⇒ kill switch | validado | `reconcile` halta; `release_halt` re-reconcilia e recusa se ainda divergente (teste existente) |
| R18 | Atribuição de P&L | parcial | Adapters: preço médio Binance = `cummulativeQuoteQty/executedQty`, taxa no ativo cobrado, Alpaca taxa 0 (rotulado). Atribuição por estratégia vive no ledger/analytics (áreas 1 e 3): não auditada aqui |
| S1 | Credenciais nunca logadas/repr/retornadas | corrigido | `VenueCredentials` `repr=False`; **defeito:** mensagens de erro Binance continham a query assinada (com `signature`), e o OMS as loga (`services/oms/manager.py:183`). Agora só o endpoint (`3e76edd`). Valores de credencial inválidos são recusados pelo **nome** da variável, nunca o valor |
| S1b | Erros de settings ecoando segredos (achado da área 5) | corrigido | `ValidationError` do pydantic imprimia o token curto recusado. `hide_input_in_errors=True` em `ExecutorSettings` e `WatchdogSettings`; teste falha sem a correção (`fix(executor,watchdog)`) |
| S2 | Segredos no repo (scan) | validado | `git grep` por chaves privadas PEM, AKIA, `PK…/AK…` Alpaca, `ghp_`, `sk-`, `xox`, `AIza` e atribuições `*_TOKEN/SECRET/KEY=` longas: nada além de nomes de segredos do Vault, placeholders e fixtures de teste. Só `.env.example` versionado |
| S3 | Comparações de token em tempo constante | validado | `hmac.compare_digest` em executor (API e watchdog), permits, bridge; `==` restantes são validações de configuração (tokens diferentes entre si) |
| S4 | Validação de entrada da API privada | validado + apertado | Modelos pydantic; `/v1/quotes` limitado a 20 e só símbolos configurados; motivos de halt do watchdog agora ≤ 200 caracteres cada (`c4f9a4b`) |
| P1 | Promoção (gates, demoção automática) | validado | Avança um estágio por vez; CANARY exige operador, 7 dias limpos e evidências frescas (≤14 d, gravadas no estágio, a última passando); LIVE nunca; automação só demove |

## Alpaca `401: unauthorized` — veredito

**Nenhum caminho de código causa 401 com um par de chaves PAPER válido.** Verificado ponta a ponta:

1. **Nomes de variáveis:** `fetch-secrets.sh executor-alpaca` escreve
   `PROTO_CRED_ALPACA_US_TESTNET_MAIN_API_KEY=<proto-alpaca-paper-key>` e `..._API_SECRET=<proto-alpaca-paper-secret>`;
   o container recebe `EXECUTOR_VENUE=ALPACA_US`, `EXECUTOR_ENVIRONMENT=TESTNET`, `EXECUTOR_ACCOUNT=main`;
   `load_credentials` monta exatamente esse prefixo. Variável ausente daria `CredentialsUnavailable`, não 401.
2. **Troca chave/segredo no código:** não há. Key ID ⇒ `APCA-API-KEY-ID`, segredo ⇒ `APCA-API-SECRET-KEY`
   (`alpaca.py::_headers`), nomes de header corretos; nenhum `Authorization` concorrente.
3. **Host:** `https://paper-api.alpaca.markets/v2/account` fixo; redirects nunca seguidos.
4. **Espaços/encoding:** o loader faz `strip()` (espaço, `\r\n`, NBSP); httpx recusaria não-ASCII com
   `UnicodeEncodeError` (erro diferente, não 401).
5. Teste novo `test_the_vault_env_file_reaches_alpaca_byte_for_byte` fixa os bytes exatos enviados a partir de
   um env file no formato do deploy (com `\r\n`/espaços) e da forma `_FILE`.

O 401 vem portanto do **valor** das chaves. Causas que o código não podia distinguir e que agora são
recusadas no start, por nome, sem revelar valores (`1b755b7`):

- valor com aspas, espaço interno, dois valores num campo ou caractere invisível/não-ASCII
  (o env file do podman passa aspas literalmente) ⇒ `CredentialsUnavailable(<VAR> holds spaces, quotes...)`;
- Key ID **live** (`AK…`) ⇒ recusado antes de qualquer request (a chave live nunca é enviada);
- par trocado no Vault (segredo com cara de Key ID) ⇒ recusado.

O que continua possível e só o operador resolve: par de **outra conta paper** ou **regenerado** no
dashboard (regenerar invalida o segredo antigo, e o `create-secrets.sh` pula segredos já existentes no
Vault, então o valor velho permanece); um 401/403 no connect agora diz quais variáveis conferir. Ação
recomendada: gerar o par em app.alpaca.markets → Paper, gravar **nova versão** dos dois segredos no Vault,
deixar o deploy recriar o container (o `8dba87a` já recria quando o env muda).

## Drills (offline, seed 0, reproduzidos com `python -m services.drills verify`)

| Drill | Casos | Passou | Digest |
|---|---|---|---|
| ADVERSARIAL_SUITE | 32 | sim | `3913251f82bb0af85d16316a88e02d7d2073490077be4cc5ae85057cfafedd6b` |
| RECOVERY_DRILL | 15 | sim | `045751553aaf9d149d1d5f9bff6a7ef0bab5499f2e3ecd8cccde93feb688fa7e` |
| ROLLBACK_DRILL | 17 | sim | `128039b6cda1a7dba5b6b23591882017bddde0a304e6092dd99af41df02f91bc` |

O digest adversarial mudou de propósito (antes `114addcb…` com o oráculo novo e o motor antigo, falhando com
7 violações; aprovações no fuzz caíram de 34 para 27 com a correção). Evidências de promoção gravadas com o
digest antigo deixam de reproduzir: registrar novas antes de qualquer pedido de CANARY.

## Verificações

- Testes afetados após cada correção. Ao final: `ruff check apps services tests` → `All checks passed!`;
  `python -m pytest` completo → exit 0, nenhuma falha (asyncio_mode=auto, --maxfail=1).
- Achado da área 3 (empilhamento de LONGs por posição) coberto por R1: posição preenchida + `open_buys`,
  saídas reduce-only continuam permitidas, livro desconhecido recusa risco novo.
- Nenhum teste foi removido, pulado ou afrouxado; testes existentes intactos (o teste da bridge que recusa conta
  REAL continua exercitando a bridge: a trava nova só age após mudança de modo).

## O que os testes NÃO provam

- Nada fala com Alpaca, Binance ou MT5 reais: os fakes reproduzem o contrato documentado, não o servidor. O
  veredito do 401 é por leitura de código + bytes enviados; não reproduz a resposta real da Alpaca.
- Prefixos `PK`/`AK` das chaves Alpaca são convenção pública da Alpaca, não contrato garantido; se mudar, a
  recusa de `AK…` pode exigir revisão (ela só aperta).
- `open_buys` só protege quando a API o preenche (hoje vem vazio: ver cruzados). A trava contra empilhamento
  por **posições preenchidas** já age.
- Feriados de bolsa não são modelados (venue/relógio Alpaca recusam).
- A persistência do halt foi testada com sqlite; em Postgres a tabela é criada por `create_all` como o ledger
  do executor, sem migração Alembic.
- Volatilidade real não entra no sizing (stop fixo de 2%).

## Achados entre áreas (não editados)

1. **API (área 1) — `apps/api/app/oms_surface.py:364,403,417`**: preencher `AccountState.open_buys` com a
   quantidade de compra ainda em aberto/UNKNOWN por instrumento (ordens do executor/paper OMS:
   `quantity - filled_quantity` para ACKNOWLEDGED/PARTIALLY_FILLED/SUBMITTED/UNKNOWN). Sem isso, ordens
   repousando ainda podem empilhar antes de preencher.
2. **Docs (área 5) — `docs/risk/risk-engine.md:26,28`**: `risk_per_position` passa a ser
   `(posição + compras em aberto + ordem) × stop_distance ≤ min(equity, R$10.000) × 0,25%`; acrescentar
   `quote_spread` (padrão 200 bps, saídas isentas) e "fuso sem sessão modelada = fechado" (NY 9:30–16:00 também
   falta na tabela). `docs/venues/alpaca-paper.md`: seção de diagnóstico do 401 (texto do veredito acima) e
   "regenerar a chave = nova versão dos dois segredos no Vault". `docs/executor/isolated-executor.md`: halt
   persistente (`executor_halts`).
3. **Infra (área 5) — `infra/oci/scripts/create-secrets.sh:95`**: pula segredo existente; oferecer
   `--rotate proto-alpaca-paper-key proto-alpaca-paper-secret` (nova versão) para o caso de chave regenerada.
4. **OMS (área 1) — `services/oms/manager.py:183`**: loga `exc` de outcome desconhecido; o adapter Binance já não
   inclui a query, mas um adapter futuro poderia: preferir `type(exc).__name__` + endpoint.
5. **Infra — `docker-compose.yml:18`**: default `CREATION_BRIDGE_SHARED_SECRET` embutido (local apenas);
   preferir sem default.
6. **API (área 1)**: `apps/api/app/settings.py` não expõe `risk_max_spread_bps`; o padrão 200 vale. Se exposto,
   manter limite (0, 500].

## Arquivos removidos

Nenhum.

## Pendências / riscos

- Volatilidade: sizing com `stop_distance` fixo de 2% subestima risco em ativos com stop real maior.
  Requer dado de volatilidade (ATR) da camada de agentes; não implementado aqui.
- Re-registrar evidências ADVERSARIAL_SUITE com o digest novo antes de promoções.
- Bloqueios: nenhum.
