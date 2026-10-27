# Finalização do PROTO (2026-10-27): checklist persistente

Estados possíveis: `pendente`, `em execução`, `validado` e `bloqueado`. Cada item registra
evidência (comando e resultado, ou arquivo e linha) e dependências. As fontes de verdade são
`AGENTS.md`, `CLAUDE.md`, `docs/pragma/PRAGMA-CORE.md` e os planos em `docs/plans/`. O relatório
detalhado de cada área fica em `docs/audits/finalization/<área>.md`.

| # | Área | Responsável (fronteira de edição) | Estado | Evidência / relatório |
|---|---|---|---|---|
| 1 | Backend, API e persistência | `apps/api/app/**`, `services/oms/**`, `services/execution/**`, `migrations/**` | em execução | `finalization/backend.md` |
| 2 | Frontend, UX e fidelidade visual | `apps/web/**` | em execução | `finalization/frontend.md` |
| 3 | Agentes, modelos, pesquisa e calibração | `services/agents/**`, `services/signals/**`, `services/research/**`, `services/analytics/**` | em execução | `finalization/agents.md` |
| 4 | Risco, execução, reconciliação e segurança | `services/risk/**`, `services/venues/**`, `apps/executor/**`, `apps/watchdog/**`, `services/watchdog/**`, `services/promotion/**`, `services/drills/**` | em execução | `finalization/risk.md` |
| 5 | Infraestrutura, observabilidade, testes e limpeza | `infra/**`, `.github/**`, `Dockerfile*`, `docker-compose*`, `scripts/**`, `pyproject.toml`, `docs/**` (fora de `audits/`) | em execução | `finalization/infra.md` |
| 6 | Revisão independente das alterações integradas | somente leitura | pendente | `finalization/review.md` |
| 7 | Integração, suíte de aceitação e PR | coordenador | pendente | este arquivo |

## Fronteiras e regras comuns
- O boundary financeiro do `AGENTS.md` vale para todos:
  - testnet, paper ou demo somente;
  - nenhuma ordem real;
  - limites só podem ser apertados;
  - `financial_connectivity` e `real_money_execution` continuam `false`.
- Nenhum teste é enfraquecido, pulado ou desativado. Mocks só onde for apropriado, e o relatório
  declara o que eles não comprovam.
- Antes de remover qualquer arquivo, verificar referências, carregamento dinâmico, build, testes,
  deploy e documentação.
- Dados, migrações aplicadas e evidências de auditoria são preservados. Não há reescrita de
  histórico.
- Contrato da conversa com agentes (conector ChatGPT): mantido como está, sem integração paga por
  chave de API e sem ampliar permissões.
