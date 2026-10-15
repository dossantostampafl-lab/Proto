---
name: pragma-benchmark
description: Medir e otimizar custo de tokens, latência, cache e qualidade de agentes Codex/Claude. Use em avaliações de eficiência, comparações antes/depois e revisão de orçamentos.
---

# PRAGMA Benchmark: reduzir custo por tarefa bem-sucedida
1. Defina tarefas representativas (bug pequeno, feature, investigação, audit, release simulado); use a mesma especificação, repo e critérios para versão-base e candidata.
2. Colete, quando suportado: versão/modelo; tokens entrada não-cache, leitura/escrita de cache, saída; nº requisições/subagentes; ferramentas; tempo; custo com tarifa vigente; sucesso; regressões.
3. Diferencie limite do plano/assinatura, contabilidade de tokens e custo API. Cache reduz custo em casos específicos, **não é token zero**.
4. Rode múltiplas repetições por grupo quando possível; controles equivalentes, ordem alternada e cenários frios/quentes separados. Declare mudanças de condições externas.
5. Avalie taxa de sucesso, custo **por tarefa correta**, p50/p95 de latência e classes de erro; não aceite ganho de custo com degradação inaceitável de segurança/qualidade.
6. Otimize primeiro fontes de desperdício verificadas: prompts base extensos, leituras duplicadas, logs grandes, MCP inativo, agentes redundantes, modelo inadequado e loops repetidos.
7. Reporte valores reais e intervalos/incerteza; se não houver dados, escreva **NÃO MEDIDO**, jamais estime porcentagem como fato.
8. Quando aplicável, apresente opções de configuração de custo (modelo, orçamento do runtime, redução de ferramentas), sem afirmar que um prompt por si as ativou.

Use `docs/pragma/BENCHMARK_MODELO.csv` como esquema de coleta; não preencha linhas com dados inventados.
