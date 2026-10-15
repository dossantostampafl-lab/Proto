---
name: pragma-debug
description: Investigar bugs, falhas de testes, incidentes, latência e regressões com evidências, diagnóstico de causa raiz e correção sem loops improdutivos. Use quando algo não funciona ou testes/deploy falham.
---

# PRAGMA Debug: incidentes e correções
1. Delimite sintoma, ambiente e resultado esperado. Defina o menor teste reprodutível e severidade; em produção, prefira observação não destrutiva.
2. Preserve contexto real: status do git, branch, diff, versão, flags, integração; **não** exponha tokens de acesso ou `.env`.
3. Inspecione primeiro o ponto de falha: código do caminho de execução, traceback recortado, status/exit code e logs filtrados por requisição. Evite `tail` infinito e análise de repositório inteiro.
4. Separe sintomas de hipóteses. Priorize uma hipótese falsificável por vez; selecione comando/teste que possa confirmá-la ou refutá-la.
5. Corrija a causa raiz com mudança mínima, preserve comportamento e inclua regressão para o defeito quando adequado.
6. Valide: teste que falhava → suíte afetada → integração/e2e quando pertinente. Observe erros silenciosos, timeouts e falsas respostas de sucesso.
7. Se a mesma hipótese falhar em duas iterações sem evidência nova, pare de repetir, registre fato e reformule. Se bloqueado por ambiente/acesso, classifique BLOCKED, não invente solução.
8. Ao finalizar, informe **causa confirmada ou provável**, mudança, comandos com resultados e risco residual. Sem diagnósticos garantidos por intuição.

Para falhas de deploy, combine com `pragma-delivery`, mas apenas se realmente houver operações de entrega.
