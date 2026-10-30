# ChatGPT → PROTO pelo MCP (somente leitura, sem API paga)

## O que é

`POST /mcp` é um servidor MCP (Model Context Protocol) no transporte Streamable HTTP
(JSON-RPC 2.0, respostas `application/json`). Um conector personalizado do ChatGPT
(modo desenvolvedor / apps) chama esse endpoint. O modelo roda no plano ChatGPT do operador,
do lado da OpenAI. O PROTO não chama nenhuma API de modelo da OpenAI nesse caminho, então não há
chave de API paga.

Isso é diferente do “Sign in with ChatGPT” (#335/#355). Nele, o PROTO usa o OAuth do operador para
pedir explicações. Os dois caminhos convivem. O MCP também não depende de sessões privadas, cookies
ou APIs não documentadas.

## Ferramentas (todas `readOnlyHint: true`)

| Ferramenta | Devolve |
|---|---|
| `proto_status` | modo de runtime, rótulo de dados (PAPER/TESTNET), estado dos FinRobots, controles do Risk Engine |
| `finrobots_list` | os 8 especialistas, mandato, `BOUND`/`UNBOUND`, último ciclo |
| `finrobot_council_latest` | veredito do council por instrumento, votos, razões e evidências |
| `finrobot_cycles` (`limit` 1–10) | ciclos recentes com todos os relatórios |
| `agent_decisions_recent` (`limit` 1–50, `instrument_id`) | decisões recentes dos agentes híbridos |
| `risk_status` | limites constitucionais, política e controles |

Nenhuma ferramenta envia sinal, intent ou ordem. Nenhuma mexe em kill switch ou limite, nem inicia
ciclo. A supervisão ativa continua no dashboard, com o token de operador.

## Segurança

- **Fechado por padrão.** Com `MCP_ENABLED=false`, a rota responde 404.
- **Autenticação.** Há dois caminhos, e os dois chegam às mesmas ferramentas só de leitura.
  - **OAuth 2.1** (`apps/api/app/mcp_oauth.py`): é o que o conector do ChatGPT usa.
    - O `/mcp` responde 401 com `WWW-Authenticate: Bearer resource_metadata=…`, que aponta para
      `/.well-known/oauth-protected-resource`.
    - O ChatGPT se registra sozinho (registro dinâmico, RFC 7591). Só são aceitos redirects HTTPS em
      `chatgpt.com` ou `chat.openai.com`.
    - Em seguida, abre `/oauth/authorize`: uma página onde **o operador digita o `MCP_API_TOKEN`**
      para aprovar. São 5 tentativas por minuto, e cada recusa grava `MCP_OAUTH_DENIED`.
    - O código vale 60 s, é de uso único e fica preso ao PKCE S256, ao redirect e ao recurso
      `PUBLIC_ORIGIN/mcp`.
    - O access token vale 1 h. O refresh token vale 30 dias e é trocado a cada uso.
    - O único escopo é `proto.read`.
    - Os tokens só existem como hash SHA-256, na memória e no armazenamento do plano de controle
      (`control_state`, chave `mcp_oauth`), sem tabela nova.
      - Reiniciar a API (um deploy) mantém o ChatGPT conectado.
      - Trocar o `MCP_API_TOKEN` desconecta todos.
      - Se o armazenamento falhar, só se perde a durabilidade: o login continua funcionando e o
        caminho de risco não é afetado.
    - `POST /v1/mcp/oauth/revoke`, com o token de operador, desconecta todos na hora.
      `GET /v1/mcp/oauth/status` mostra o estado.
  - **Bearer fixo:** `MCP_API_TOKEN` em `Authorization: Bearer …`, para clientes que aceitam.
- **Token próprio.** O `MCP_API_TOKEN` (bearer fixo e senha de aprovação do OAuth):
  - Tem no mínimo 32 caracteres em produção.
  - Precisa ser diferente dos tokens de operador, executor e watchdog; o settings recusa se for igual.
  - O middleware de operador não cobre `/mcp` nem `/oauth/*`, porque o ChatGPT não envia
    `X-Proto-Operator-Token`. `/v1/mcp/oauth/revoke` continua exigindo o token de operador.
  - O OAuth só existe com `MCP_ENABLED`, `MCP_API_TOKEN` e um `PUBLIC_ORIGIN` HTTPS; sem eles,
    as rotas respondem 404.
- **Origem.** Uma requisição sem `Origin` (chamada de servidor), de `https://chatgpt.com`,
  `https://chat.openai.com` ou de `PUBLIC_ORIGIN` passa. Qualquer outra origem de navegador recebe
  403 (proteção contra DNS rebinding).
- **Limites.** 120 chamadas/min por cliente, corpo de até 64 KiB e lotes JSON-RPC recusados.
  - `MCP-Protocol-Version` desconhecida → 400.
- **Prompt injection.**
  - Os argumentos são validados com `extra="forbid"` e padrões estritos, e nunca são interpretados
    como instrução.
  - Todo texto devolvido passa por limpeza de caracteres de controle e bidi.
  - O texto vem embrulhado num `notice` que diz ao modelo que aquilo é dado, não instrução.
  - Mesmo que o modelo seja manipulado, não existe ferramenta que aja.
- **Auditoria.** Cada `tools/call` grava `MCP_TOOL_CALLED` (ferramenta, ok, cliente) no journal.
- **Credenciais.** `mcp_surface` fica em `apps/api`. Não importa venues nem o executor, e não lê
  credencial de corretora.

## Como ligar (operador)

1. Gere um segredo: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
2. Na API, defina `MCP_ENABLED=true`, `MCP_API_TOKEN=<segredo>` e
   `PUBLIC_ORIGIN=https://<domínio>`. O `PUBLIC_ORIGIN` é o issuer do OAuth. Para ter ciclos
   contínuos, defina também `FINROBOTS_ENABLED=true`. Depois reinicie.
3. Nada a mudar no proxy. O Caddy de produção (`infra/oci/deploy/proto-deploy.sh`,
   `caddy reverse-proxy --to 127.0.0.1:8000`) repassa todos os caminhos e o header
   `Authorization` para a API. Com `MCP_ENABLED=true`, `https://<domínio>/mcp` já responde. Sem
   domínio configurado, não há HTTPS público e o ChatGPT não consegue se conectar.
4. No ChatGPT: Configurações → Apps e conectores → Modo desenvolvedor → Criar conector.
   - URL: `https://<domínio>/mcp`.
   - Autenticação: **OAuth**.
   - O ChatGPT abre a página de aprovação do PROTO. Digite o `MCP_API_TOKEN` e toque em Autorizar.

## Não verificado (PARTIAL)

- **Autenticação do ChatGPT.** O OAuth segue a especificação de autorização do MCP e está testado
  ponta a ponta com um cliente simulado (`tests/unit/test_mcp_oauth.py`). Ainda não foi
  exercitado com o conector real.
  - Não sei se o conector manda o parâmetro `resource`; ele é opcional aqui.
  - O endereço exato de callback do ChatGPT pode variar. Qualquer caminho HTTPS em `chatgpt.com` ou
    `chat.openai.com` é aceito.
  - Não abra `/mcp` sem autenticação.
- **Conexão real.** Nenhuma chamada real do ChatGPT foi feita. Os testes usam o `TestClient` do
  FastAPI com o protocolo MCP implementado à mão (`tests/unit/test_mcp_surface.py`).
- **Deploy.** Não liguei `MCP_ENABLED`, `MCP_API_TOKEN` nem `FINROBOTS_ENABLED` na Oracle. Pelo script, o proxy não precisa de mudança, mas não verifiquei isso no servidor.
