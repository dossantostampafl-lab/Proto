---
name: pragma-audit
description: Auditoria, code review e análise de arquitetura, segurança ou qualidade solicitada explicitamente; encontra riscos verificáveis por prioridade e evita auditorias ilimitadas.
---

# PRAGMA Audit: verificação guiada por evidência
1. Defina dimensão requerida (segurança, arquitetura, funcionalidade, testes, desempenho) e fronteira: diff, módulo, serviço ou repositório.
2. Comece pelos invariantes relevantes, entradas externas, caminhos críticos e alterações recentes. Amplie escopo só se houver evidência de problema sistêmico.
3. Faça buscas pontuais e ferramentas determinísticas/linters disponíveis antes de invocar revisores adicionais.
4. Cada achado precisa de evidência reproduzível ou localização precisa, severidade, impacto e correção indicada. Classifique certeza; não infira vulnerabilidade apenas por padrão textual.
5. Diferencie achados novos de dívida histórica; evite recontar o mesmo problema em arquivos diferentes.
6. Se houver autorização para corrigir, ataque achados de maior impacto, rode os testes correspondentes e informe riscos pendentes.
7. Critério de parada: áreas no escopo cobertas, achados priorizados, correções verificadas quando pedidas; não inicie uma auditoria infinita.
8. Resposta: resumo executivo curto, achados por gravidade, evidências, estado das correções e limites da auditoria.
