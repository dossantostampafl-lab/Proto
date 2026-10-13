# Fase 4 — OMS canônico e ledger paper

Implementação no PR #273, seguindo `docs/plans/2026-10-13-tradingview-agent-trading.md` e `AGENTS.md`.

## Cadeia de autoridade

1. Um `TradeIntent` validado pelo Risk Engine recebe `RiskPermit` assinado.
2. `CanonicalPaperOMS.place` aceita exclusivamente estágio `PAPER` com `VenueAdapter.real_money_execution=False` e venue idêntica ao intent.
3. A transação SQL registra **reserva + consumo do permit + ordem SUBMITTED** antes de chamar qualquer adapter. O `client_order_id` é determinístico e único. Erros de persistência impedem a ordem.
4. O adapter paper confirma o resultado. A aplicação de um `ExecutionReport` cumulativo é outra transação: atualiza o estado, grava fill delta imutável, movimentos de base/moeda de cotação/taxa e reduz a reserva.
5. Uma exceção ou ACK incerto leva a `UNKNOWN` e halt da venue. `resolve_unknown` consulta a venue por `client_order_id`; **nunca reenvia a ordem**.
6. `reconcile` compara o saldo inicial durável mais todos os movimentos do ledger com os saldos do adapter. Divergência superior à tolerância → halt persistido. `reconciliation_loop` oferece execução periódica a ser supervisionada pelo host.

## Persistência

- Alembic `0008_execution_consumed_permits`: `execution_consumed_permits`.
- Alembic `0009_execution_oms`: `execution_orders`, `execution_fills`, `execution_balance_entries`, `execution_opening_balances`, `execution_reservations` e `execution_venue_halts`.
- Movimentos financeiros são `Decimal` serializados como texto; não são convertidos para float.
- Assinaturas de permits não são persistidas. IDs já consumidos não podem ser reusados por outros workers.
- Uma conta paper é modelada por venue nesta implementação. Saldo inicial é capturado antes da primeira ordem; não há importação de saldos reais.

## Contratos relevantes

- LIMIT precisa estar na grade da venue e possuir saldo disponível para reserva.
- MARKET é rejeitada enquanto não houver limite garantido para o gasto.
- IOC não negociável é cancelada imediatamente, sem deixar ordem em aberto.
- SELL requer `reduce_only` e saldo em ativo base.
- Relatórios cumulativos não podem reverter quantidade, notional ou taxas; transições inválidas falham fechadas.

## Restrições ainda não liberadas

O OMS está implementado como componente paper testável, **não como endpoint ou executor ativado**. A cadeia de agentes existente ainda não despacha permits para o OMS, e `risk_surface.account_state()` ainda usa o capital de referência sem as posições do novo ledger. A fase de integração de dados reais, contas, modelos, instância executora isolada e credenciais pertence à Fase 5 e aos gates seguintes.

`reconciliation_loop` ainda precisa ser ligada a um supervisor do runtime antes de afirmar que a reconciliação funciona continuamente em produção. Halt persiste e não é liberado automaticamente; a liberação operacional segura exige controles próprios.

Não há trading com dinheiro real, conexão financeira ou custódia. Nenhuma mudança nos limites constitucionais foi realizada.

## Verificações

```bash
ruff check apps services tests
pytest tests/unit/test_durable_risk_permits.py tests/unit/test_canonical_oms.py tests/unit/test_paper_venue_adapter.py
pytest
alembic upgrade head
```

CI é a fonte de evidência atual; nenhuma alegação de disponibilidade em produção decorre de testes unitários.
