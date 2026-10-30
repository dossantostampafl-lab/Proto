# FinRobots: baseline de integração

Base auditada: `main@8dce9e34b8b61a9200f25c63177dca5a6d8699dc`. Este documento registra a inspeção inicial, não certifica o sistema nem define um contrato compartilhado já acordado com outro implementador.

## Componentes e limites existentes

- `services/orchestration/registry.py` define contratos de jobs quantitativos, calibração, risco, replay, portfólio e observabilidade. Em `apps/api/app/orchestration_state.py`, o ProtoBrain registra os handlers `market-data-health`, `opportunity-scan` e `shadow-decision`; presença no catálogo não comprova execução dos demais jobs. O supervisor agenda a verificação de mercado.
- `services/orchestration/runtime.py` restringe modos, proíbe efeitos financeiros reais nos jobs e bloqueia jobs que exigem gate quando este não está disponível. Contratos de resultado e persistência de cada especialização precisam ser verificados antes de registrar novos handlers.
- `services/quant`, `services/research`, `services/validation`, `services/agents` e os serviços de mercado são candidatos a reutilização. `services/risk` contém o motor e os permits; `services/oms` contém OMS, ledger e reconciliação. A existência dos módulos não demonstra certificação de execução real.
- `apps/research_worker` é um adaptador FinRobot experimental, isolado e opt-in. Conforme `docs/research/FINROBOT_WORKER.md`, não está conectado à API, ao OMS, ao risco ou ao dashboard; o isolamento de rede não é firewall e requer proteção adicional antes de produção. Os testes offline usam um substituto do SDK e não demonstram inferência externa.
- As PRs #335 e #355 foram integradas à base. A superfície conversacional existente é explicativa; `services/chatgpt/chatgpt_provider.py` aponta para o endpoint Responses da OpenAI. OAuth, elegibilidade e inferência reais não foram comprovados por esta auditoria, e OAuth não estabelece acesso gratuito à inferência. Não foi identificado servidor MCP nos diretórios `apps` e `services` inspecionados.

## Dependências para integração

1. Acordar e versionar com o implementador da orquestração esquemas de tarefa, evidência, proveniência, estado, versão e falha; fixtures devem cobrir resultados indisponíveis e dados obsoletos. Nenhuma interface nova é declarada acordada por este documento.
2. Ligar cada capacidade ao serviço canônico correspondente somente após verificar entradas, persistência, idempotência, autorização e testes de recuperação. Não atribuir capacidade executável a um nome presente apenas no catálogo.
3. Manter pesquisas sem credenciais de execução e sem acesso direto a venues. Qualquer caminho financeiro deve respeitar intents tipados, permit independente, OMS, ledger, reconciliação, kill switch e gates constitucionais de `AGENTS.md`.
4. Validar separadamente o conector MCP oficialmente suportado, autenticação e permissões, disponibilidade de dados e funcionamento sem sessão ChatGPT. A implementação atual de conversação não é evidência desses requisitos.
5. Comprovar isolamento, dependências e custos do worker antes de conectá-lo ou implantá-lo. A pesquisa não autoriza ordens nem substitui os controles de risco.

## Limites da inspeção

Não havia artefatos Graphify no checkout. A revisão foi localizada e não substitui revisão cruzada independente, testes de integração, CI, certificação financeira ou validação de deploy. A licença e as condições de redistribuição dos arquivos em `third_party/the_creation_os` devem receber análise específica antes de uma distribuição que dependa deles.
