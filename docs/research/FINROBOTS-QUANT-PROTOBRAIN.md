# FinRobots: produtor quantitativo no ProtoBrain

Estado: **IMPLEMENTADO COMO HANDLER INTERNO; INGESTÃO PERIÓDICA AINDA NÃO CONECTADA**.

## Fluxo autorizado

1. Um produtor interno **já autorizado** obtém dados reais com origem, horário e custos observados. O produtor nunca sintetiza dados faltantes.
2. Constrói `QuantEvidence` (`quant-evidence/1`) contendo `QuantPipelineInput`, versões de modelo/features, fontes de todos os custos, amostras passadas e resultado da execução do pipeline.
3. Chama `services.quant.job.enqueue_quant_research(brain, envelope)`. A chave `quant-evidence:<SHA256 do JSON canônico>` torna a submissão idempotente.
4. O job `JOB_CATALOG["quant-research"]` é executado pelo **ProtoBrain existente** (fila, lease, retries, DEAD_LETTER). O handler recebe `JobRun.mode` da fila durável, nunca do payload.
5. `services.quant.job.process_quant_research_job` confirma modo e proveniência (SIMULATION/SIMULATED, SHADOW/PAPER, HISTORICAL_REPLAY/REPLAY), recusa observação futura, reexecuta `run_quant_pipeline` via `store_quant_evidence` e armazena o envelope completo somente quando tudo coincide.
6. A fonte de snapshot `quant.evidence` lê o registro com SELECT e verifica a identidade; FinRobots emitem relatórios **consultivos**. Dados ausentes/invalidáveis => `ABSTAIN`.

O job é registrado em `apps/api/app/orchestration_state.py` **somente quando `persistence_engine` existe**, para impedir falso sucesso no plano de controle `LIVE_MONITORING`.

## Bloqueios honestos

- **Não existe alimentador automático aprovado** capaz de preencher com dados reais todos os campos (custos, fonte, horários, amostras). Não criar `PeriodicJob("quant-research")` até esse produtor estar disponível: o supervisor hoje enfileira apenas metadados de agendamento e faltaria o envelope obrigatório.
- O módulo não disponibiliza endpoint público para submissão e não concede aos agentes autoridade de ordem/permit.
- `Strategy Research` e `Model Validator` ainda não dispõem de backtests, holdout e validação persistidos e auditáveis. Mantêm `ABSTAIN`.
- O uso real do conector ChatGPT/OAuth e o deploy Oracle não são validados por estes testes.

## Verificações

`tests/unit/test_finrobot_quant_job.py` testa persistência, deduplicação, isolamento por modo, rejeição de proveniência inconsistente, observação futura, indisponibilidade de banco e resultados adulterados. O CI Python e a suíte cruzada #359 + #360 devem permanecer verdes antes de merge.

Não ativar trading com dinheiro real; `financial_connectivity=false`, `real_money_execution=false`.
