# Prediction Market Quant Engine

Plataforma quantitativa de pesquisa, simulação e paper trading para criptoativos e mercados binários de previsão.

## Escopo inicial

- BTC, ETH e SOL
- Mercados binários de previsão
- Fair probability e edge estatístico
- Microestrutura e order flow
- Volatilidade, hedge e risco
- Fills simulados, P&L e exposição
- Paper trading / simulação como modo padrão

## Arquitetura alvo

- `apps/api` — API e orquestração Python
- `apps/web` — terminal web
- `services/quant` — modelos quantitativos e sinais
- `services/simulator` — matching/fills simulados
- `services/risk` — limites, exposição e kill switch de simulação
- `crates/engine` — componentes Rust de baixa latência
- `packages/contracts` — contratos/eventos compartilhados
- `infra` — containers e observabilidade
- `tests` — testes unitários, integração, propriedades e cenários
- `docs` — arquitetura, ADRs e runbooks

## Princípio de segurança do MVP

O MVP não envia ordens financeiras reais. Qualquer adaptador de execução deve permanecer desacoplado e desabilitado por padrão. O sistema começa em `SIMULATION`/`PAPER`.

## Estado

Bootstrap iniciado em 2026-08-28.
