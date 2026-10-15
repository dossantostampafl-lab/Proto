---
name: pragma-delivery
description: Concluir PR, CI, merge, deploy ou release solicitado, com gates independentes, logs de falha, smoke test e possibilidade de reversão. Use em tarefas de entrega real.
---

# PRAGMA Delivery: nenhuma entrega fictícia
1. Confirme a branch/remoto/ambiente efetivos e o escopo autorizado antes de `push`, PR, merge ou deploy. Preserve alterações não relacionadas e decisões de branch protection.
2. Valide localmente o escopo alterado, registre testes efetivos e divergências conhecidas.
3. Ao criar/atualizar PR, verifique diff, alvo, checks e status do PR a partir de ferramenta real; não invente links/IDs.
4. Para CI, verifique o *run* e a conclusão real; status verde antigo ou em outra revisão não conta.
5. Merge exige autorização pertinente e checks obrigatórios. Não confunda PR aprovado com merge realizado.
6. Deploy exige acesso, autorização, confirmação da versão/commit, logs de implantação e saúde observável. CI verde e deployment criado não provam aplicação funcionando.
7. Faça smoke test representativo após deploy, conforme rota e capacidade disponíveis. Em falha, classifique o estágio, diagnostique o log pertinente; não dispare tentativas repetidas sem mudança ou hipótese nova.
8. Reversão e migrações: estabeleça ponto de retorno e plano de contingência antes de mudança irreversível; jamais apague dados em nome da rapidez.
9. Reporte matriz factual: código/testes locais/CI/PR/merge/deploy/smoke, cada um VERIFIED, FAILED ou NOT CHECKED.

Se não houver permissão ou infraestrutura, reporte BLOCKED/PARTIAL. Instruções não concedem acesso à produção.
