# Quant research job: proposta de contrato v1

Estado: **proposta para revisão entre branches**, não implementada nem acordada. O catálogo existente `JOB_CATALOG["quant-research"]` continua sendo a referência de modos e critérios; não adicionar uma segunda fila ou autoridade de execução.

## Entrada e evidência

- A tarefa usa o contrato `QuantPipelineInput` existente, com todos os custos numéricos explícitos e `observed_at` com timezone. O envelope deve trazer versão do contrato, identificador e timestamp da fonte para cada custo; não usar defaults para preencher evidência ausente.
- Cada amostra de calibração deve carregar timestamp e proveniência. Apenas amostras estritamente anteriores a `observed_at` podem alimentar o cálculo. Entradas ausentes, futuras ou inválidas devem falhar de forma explícita, sem inventar dados.
- Persistir o envelope completo de entrada e sua identidade determinística juntamente com os resultados e versões do modelo e das features. Mesma identidade e mesmo conteúdo devem permitir retry; conteúdo diferente deve produzir conflito, sem sobrescrever evidências.
- O resultado informa identidade, estado, proveniência, versão do contrato, saída quantitativa e estado de persistência. Storage indisponível não pode ser apresentado como sucesso durável. O estado de uma tarefa existente é o do `ProtoBrain`; a integração não cria estados paralelos.

## Fronteira de execução

O job é pesquisa em `SIMULATION`, `SHADOW` ou `HISTORICAL_REPLAY`, conforme catálogo. Sua saída não é ordem, Risk Permit nem autorização de operação. Deve declarar `financial_connectivity=false` e `real_money_execution=false`. Nenhuma dependência de ChatGPT é necessária para calcular ou recuperar uma tarefa.

## Critérios antes do registro

Revisar este contrato com o responsável pela orquestração. Testar persistência atômica, retries, colisões, dados futuros, custos e fontes ausentes, indisponibilidade de storage e recuperação após falhas; verificar ainda os modos permitidos e a ausência de efeitos financeiros. O pipeline atual e `persist_quant_lineage` são pontos de reutilização, mas não persistem o envelope completo de entrada e proveniência exigido aqui.
