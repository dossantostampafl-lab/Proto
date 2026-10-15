---
name: pragma-feature
description: Implementar recurso novo, refatoração significativa, migração de interface ou alteração entre múltiplos serviços; define contrato de aceite, plano enxuto e testes proporcionais.
---

# PRAGMA Feature: implementação completa sem expansão de escopo
1. Extraia critérios observáveis: entrada, saída, UX/API, erros, segurança, testes. Identifique dependências e compatibilidade.
2. Inspecione pontos de entrada, contrato existente e testes correlatos. Evite ler documentação irrelevante e clonar soluções paralelas já existentes.
3. Planeje somente o necessário: contratos, ordem de mudanças, checagem e rollback se houver risco. Não crie documentos extensos para tarefas pequenas.
4. Faça incremento vertical mínimo utilizável: fluxo real de ponta a ponta antes de abstrações e polimento não pedido.
5. Trate falhas e casos limite no escopo: validação de entradas, autorização, idempotência onde necessária, timeouts, integrações e estados vazios.
6. Rode formatter/lint/testes existentes, testes direcionados e smoke test de UI/API quando aplicável. Rejeite mock que mascara integração quebrada.
7. Revise diff, dependências, migrações e efeitos de compatibilidade. Não reescreva grandes áreas como efeito colateral.
8. Conclua somente com critérios de aceite demonstrados, ou apresente com precisão o que falta.

Para mudanças destrutivas em banco ou produção, exija salvaguardas e as permissões aplicáveis.
