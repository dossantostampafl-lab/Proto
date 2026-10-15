# PRAGMA-CORE 2.0 — Ultra Efficiency (Codex + Claude Code)

Kit de instruções e skills. **Não é um plugin ativo nem altera automaticamente runtime, API, modelos, custo, permissões, servidor, repositório remoto ou deploy.** O foco é custo total por tarefa concluída corretamente, não instruções infinitas.

## Conteúdo
- `AGENTS.md`: contrato enxuto, sempre ativo nos runtimes compatíveis.
- `CLAUDE.md`: ponte `@AGENTS.md` para integração Claude sem duplicação.
- `.agents/skills/pragma-*/SKILL.md`: 5 skills Codex sob demanda.
- `.claude/skills/pragma-*/SKILL.md`: equivalentes Claude Code sob demanda.
- `PROMPT_TAREFA.md`: briefing reutilizável para pedidos novos.
- `PROMPT_UNICO.md`: alternativa para colar quando não for possível instalar arquivos.
- `CHECKPOINT_MODELO.md`: retomada de tarefa longa, apenas se necessário.
- `BENCHMARK_MODELO.csv`: esquema de coleta; nenhum resultado fictício.
- `REFERENCIA_THE_CREATION_OS.md`: referência histórica opcional, **não autocarregada**.
- `instalar.py`: instalador multiplataforma conservador (`--dry-run` por padrão).

## Instalação sem sobrescrever nada
1. Extraia o ZIP e abra o terminal na pasta que contém `instalar.py`.
2. Simule com `python instalar.py --dest CAMINHO_DO_REPOSITORIO`.
3. Se a listagem estiver correta, execute `python instalar.py --dest CAMINHO_DO_REPOSITORIO --apply`.
4. Se houver conflito com arquivos existentes, o instalador **não os sobrescreve** e não aplica mudanças. Faça mesclagem manual das instruções; confira se não há contradições.
5. Verifique Codex lendo `AGENTS.md`; no Claude Code, use `/context`. Teste uma tarefa pequena antes de integrar a produção.

Caso instale manualmente, copie os dois arquivos de raiz e as pastas `.agents/skills/` e `.claude/skills/` para o repositório. Não copie `REFERENCIA_THE_CREATION_OS.md` como instrução automática. Para equipes com políticas existentes, preserve as políticas mais restritivas.

**Por que duplicar diretórios de skills?** Cada cliente usa convenções próprias para descoberta; os arquivos são fisicamente separados, mas só a skill invocada entra integralmente no contexto, não todas as instruções.

## Operação recomendada
- Tarefa pequena: prompt de tarefa curto; modelo padrão; sem ativar skill desnecessária.
- Bug: skill `pragma-debug`.
- Implementação ampla: `pragma-feature`.
- Auditoria solicitada: `pragma-audit`.
- PR, CI ou deploy: `pragma-delivery`.
- Experimento de custo/latência: `pragma-benchmark`.
- Tarefas longas: checkpoint sucinto antes de retomar; nova sessão para assunto não relacionado.
- Para Claude Code, `/usage` fornece métricas quando disponíveis; `/context` identifica pressão de contexto; há controles de gasto na plataforma e opções de orçamento para execução, conforme conta e versão.

## A verdade sobre economia
A mudança não garante um percentual de redução. Para afirmar resultados, execute benchmarks A/B com critérios equivalentes de qualidade e custo e reporte as métricas reais. Um texto não impõe limites duros de consumo: para isso, configure controles no cliente, API ou gateway.

## Compatibilidade / documentação oficial consultada em outubro de 2026
- [OpenAI: revisão de prompts e skills](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)
- [OpenAI: skills e avaliação](https://developers.openai.com/blog/eval-skills)
- [OpenAI: agentes e configuração](https://developers.openai.com/api/docs/guides/agents)
- [Anthropic: instruções e AGENTS.md](https://code.claude.com/docs/en/memory)
- [Anthropic: custos e uso de tokens](https://code.claude.com/docs/en/costs)
- [Anthropic: skills](https://code.claude.com/docs/en/skills)
