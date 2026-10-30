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

## Como ligar na Oracle (operador)

A imagem de produção (`Dockerfile`) já liga `FINROBOTS_ENABLED` e `MCP_ENABLED`, e o
`PUBLIC_ORIGIN` vem do domínio do stack: o próprio ou `<ip-com-traços>.sslip.io`. O único item
manual é o token do conector, `proto-mcp-api-token` no Vault, que você escolhe e guarda.
Sem ele, a API sobe normalmente e só o `/mcp` fica fechado.

1. **Merge na `main` com CI verde.** A imagem nova é publicada e o servidor a baixa em até
   5 minutos.
   - Os FinRobots passam a rodar sozinhos: aba **Agents → FinRobots: RUNNING · 8/8**.
   - O conector aparece **ENABLED**, ainda sem OAuth, porque falta o token.
2. **Cloud Shell, uma vez:** rode o setup, que aplica o IAM do segredo novo e reinstala o
   `proto-deploy` nas instâncias.
   ```bash
   bash <(curl -fsSL https://raw.githubusercontent.com/dossantostampafl-lab/Proto/main/infra/oci/deploy/setup.sh)
   ```
3. **Crie o token** (32 caracteres ou mais; guarde no seu gerenciador de senhas):
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   bash ~/Proto/infra/oci/scripts/create-secrets.sh
   ```
   - O script pergunta só o que falta. Cole o token em `proto-mcp-api-token` (não aparece na tela).
   - Se for menor que 32 caracteres, ele recusa.
   - Também pode ser criado no console do Vault, como texto simples, com esse nome.
4. **Recarregue os segredos da API:**
   ```bash
   bash ~/Proto/infra/oci/deploy/bastion-run.sh ssh api
   sudo proto-deploy --force
   ```
   - Um token curto ou igual a outro token é ignorado com uma linha `mcp:` no log, e a API não cai.
5. **Confira** em `https://<domínio>` → aba **Agents** → **ChatGPT connector (MCP)**:
   **ENABLED** e **OAuth AVAILABLE**.
6. **No ChatGPT:** Configurações → Apps e conectores → Avançado → ative o **Modo desenvolvedor**
   → Criar conector.
   - URL: `https://<domínio>/mcp`.
   - Autenticação: **OAuth**.
   - Na página do PROTO que abrir, digite o token e toque em Autorizar.
   - Para desconectar: botão **Sign out all** no painel. Para invalidar tudo: troque o token
     (passos 3 e 4).

Fora da Oracle, as mesmas variáveis valem em qualquer ambiente: `MCP_ENABLED=true`,
`MCP_API_TOKEN=<token>`, `PUBLIC_ORIGIN=https://<domínio>` e `FINROBOTS_ENABLED=true`. O Caddy
de produção repassa todos os caminhos e o header `Authorization`, então não precisa de mudança no
proxy.

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
- **Deploy.** Os passos acima não foram executados na Oracle por mim: não tenho acesso à conta. Pelo script, o proxy não precisa de mudança, mas não verifiquei isso no servidor.
