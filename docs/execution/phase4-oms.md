# Fase 4 — OMS canônico, ledger e reconciliação

Referências vinculantes: `AGENTS.md`, `docs/plans/2026-10-13-tradingview-agent-trading.md` e ADR de execução financeira. PR #273.

## Autoridade e integração

O pipeline de agentes produz propostas tipadas; ele nunca acessa um broker. Quando
`CANONICAL_PAPER_OMS_ENABLED=true`, as propostas aprovadas percorrem
`HybridDecisionPipeline → RiskGovernor → RiskEngine → RiskPermit → auditoria
operacional durável → Phase4PaperRuntime → CanonicalPaperOMS → PaperVenueAdapter`.
O `agent_surface` aguarda o portfólio durável antes do dimensionamento.
O `risk_surface` obtém exposições, posições, patrimônio e checkpoints de
perda/drawdown do ledger em vez de usar o capital de referência estático.
Rejeições, cotações velhas, erros de journal ou indisponibilidade do OMS
impedem nova execução. Uma aprovação de IA jamais autoriza a ordem por si só.

Os objetos permanecem paper: `financial_connectivity=false`,
`real_money_execution=false`. A identificação da venue
`BINANCE_SPOT` no `PaperVenueAdapter` é simulação isolada, sem API de
negociação, credenciais ou envio de ordens reais. Forex, B3 e mercados de
predição não são liberados pela Fase 4.

## Integridade financeira

- `RiskPermit` assinado, expiração curta e uso único entre processos/reinícios.
  O consumo e a criação da ordem compartilham a mesma transação SQL.
- `client_order_id` determinístico, proibição de duplicação e relatório
  cumulativo de execução validado pela máquina de estados.
- Ordens `UNKNOWN` são resolvidas exclusivamente por consulta à venue.
  Não existe reenvio cego para tentar resolver confirmação perdida.
- Reservas bloqueiam simultaneamente saldo disponível. Compras incluem
  o custo máximo LIMIT acrescido da taxa paper configurada; sem orçamento
  verificável, a operação é recusada. MARKET sem limite é recusada.
- Fills incrementais e movimentos de base/cotação/taxa são persistidos
  atomicamente como decimais; cancelamento e preenchimento liberam reservas.
- `reconcile()` é serializado com a submissão de ordens, consulta ordens
  pendentes e compara saldos do ledger e adapter. Divergência → halt
  persistido; halts concorrentes são idempotentes.
- Um reconciliador supervisionado é iniciado e encerrado pelo ciclo de
  vida da API, não apenas declarado como função.
- Reinício paper com todas as ordens encerradas reconstitui saldos pelo
  ledger. Ordens abertas, parciais ou `UNKNOWN` bloqueiam a retomada
  automática e exigem investigação, pois o broker paper em memória
  não preserva ordens de trabalho entre processos.
- A conta de risco é marcada em BRL: caixa mais posições marcadas pelo
  bid, exposição pelo ask e compromissos BUY pendentes. Cotações ausentes,
  velhas ou ativos/moedas sem valoração são recusados; não se presume
  paridade BRL/USDT. Checkpoints diários e máximos de patrimônio persistem.

## Banco e configuração

Alembic `0008_execution_consumed_permits` persiste permits consumidos.
A migração `0009_execution_oms` instala ordens, fills, movimentos de
saldo, saldos iniciais, reservas, halts e checkpoints de patrimônio.

A habilitação é opt-in (desabilitada por padrão). Exige persistência
durável e `RISK_PERMIT_SIGNING_KEY` estável com pelo menos 32 bytes.
Em produção exige banco PostgreSQL configurado; o journal operacional
também deve usar armazenamento durável (persistence ou orchestration)
e o bootstrap recusa journal apenas em memória. A API expõe
`GET /v1/execution/status`, sem endpoint para envio arbitrário de ordens.

## Contratos ainda não liberados

A Fase 4 é o subsistema broker-independente **paper**, não a
certificação de execução financeira real. O código atual
`apps/api/app/market_quotes.py` fornece cotação ao vivo para pares
Binance USDT; não há fonte BRL/FX equivalente certificada para as
propostas paper BRL. Consequentemente, sem feed BRL válido a integração
real de sinais abstém e **não envia ordens**. Não se pode dizer que a
automação live de ponta a ponta já funciona na Oracle somente pelos
testes com cotações injetadas.

Estão fora desta fase o executor isolado, credenciais, adaptadores de
corretora Binance/XP, equivalência FX/BRL certificada, reconciliação de
contas reais e os gates SHADOW/PAPER/CANARY/LIVE por conta e credencial.
Esses itens permanecem condicionados às fases seguintes e à comprovação
operacional. Nenhum merge/deploy equivale a liberação de dinheiro real.

## Validação reproduzível

```bash
ruff check apps services tests
pytest
alembic upgrade head
```

Os testes específicos em `tests/unit/test_phase4_control_plane.py`,
`test_phase4_paper_runtime.py`, `test_canonical_oms.py`,
`test_durable_risk_permits.py` e `test_paper_venue_adapter.py`
cobrem integração tipada, travas, auditoria, fills, reservas,
idempotência, reconciliação e reinício. A evidência efetiva de CI deve
ser verificada no último SHA do PR; documentação isolada não atesta deploy.
