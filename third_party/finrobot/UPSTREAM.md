# FinRobot — importação de fontes originais (etapa A0)

Status: **VENDORED / INATIVO**. Estes arquivos são cópias fiéis de fontes open-source, **não** implementam agentes executáveis no PROTO e não são carregados pelo runtime. Esta etapa prepara uma trilha verificável antes da integração em worker isolado.

## Origem imutável

- Repositório: https://github.com/AI4Finance-Foundation/FinRobot
- Commit: `2717499b8e30f242640af08c4ad9afd1113c2d45`
- Licença original: `LICENSE` (Apache License 2.0, cópia integral preservada neste diretório)
- Sem patches nem edição nos arquivos importados nesta etapa. O `NOTICE` original também é preservado, inclusive sua política de marca registrada.
- Código executável original do criador de agentes, **não importado nesta etapa**: [factory.py](https://github.com/AI4Finance-Foundation/FinRobot/blob/2717499b8e30f242640af08c4ad9afd1113c2d45/finrobot_desktop/finrobot/engine/agents/factory.py). Esse módulo depende de outras partes do FinRobot e de `pydantic_ai`; não é um componente isoladamente executável.

| Arquivo local | Caminho original no commit acima | Git blob SHA-1 |
|---|---|---|
| `LICENSE` | `LICENSE` | `6cc0d28c44e482825bba73c84761e3d65f138f42` |
| `NOTICE` | `NOTICE` | `4ad1671f14994630695c91b14ee7cfa14cacb6af` |
| `agents/instructions/data_agent.md` | `finrobot_desktop/finrobot/engine/agents/instructions/data_agent.md` | `333559ee755fab7de14c05608cfaf16817144c57` |
| `agents/instructions/modeling_agent.md` | `finrobot_desktop/finrobot/engine/agents/instructions/modeling_agent.md` | `10fa0907d88782d5f1396d734c95d9b10720bc4c` |
| `agents/instructions/synthesis_agent.md` | `finrobot_desktop/finrobot/engine/agents/instructions/synthesis_agent.md` | `ffe0c2e5e34d7785689869fa7fdea7d546d31e8b` |
| `agents/instructions/report_agent.md` | `finrobot_desktop/finrobot/engine/agents/instructions/report_agent.md` | `37702beb5d00c6a1ff17bcb47cb51808d573e89c` |

A verificação offline `tests/unit/test_finrobot_source_integrity.py` confirma a igualdade byte a byte dos Git blobs acima (sem chamadas externas).

## Políticas de adoção

1. **Não duplicar** a orquestração ou os mecanismos de risco já existentes em `services/orchestration`, `services/agents`, `services/risk` e `services/oms`.
2. Os documentos originais são instruções especializadas, **não agentes ativos**. Nenhum endpoint, chave ou conexão com corretora foi acrescentado.
3. Na etapa A1, integrar somente em um worker de pesquisa isolado, com autenticação, dados datados, origem verificável, limite de custo e `ABSTAIN` quando houver informação insuficiente.
4. Nunca oferecer `RiskPermit`, contas, credenciais, limites, OMS ou executor como ferramentas para esses papéis. Toda recomendação é consultiva.
5. Preservar o upstream em sua árvore própria; adaptações futuras deverão ser commits/arquivos separados e testes que demonstrem os desvios.
6. Cada novo upstream requer seu próprio manifesto, licença e verificação de integridade, conforme `AGENTS.md`.
7. Revisar NOTICE, dados e termos de fornecedores antes de redistribuir código adicional ou usar dados financeiros.

## Próximas integrações: ainda não implementadas

- TradingAgents: analistas fundamentalistas, notícias, sentimento, mercado, debate otimista/pessimista e gestão de pesquisa, em **worker separado**, sem Trader/OMS externo.
- FinRobot: operadores originais de valuation e resultados em execução isolada com dependências próprias; modelos numéricos calculados em código, nunca completados por LLM.
- RD-Agent: somente sandbox de pesquisa de fatores e modelos com validação do PROTO.
- FinGPT: ferramentas de sentimento e Forex condicionadas a licenças de modelos/datasets e qualidade dos dados.

Referência: plano de expansão especializado aprovado em 23/10/2026; `AGENTS.md` é a autoridade final.
