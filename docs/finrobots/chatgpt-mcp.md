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
- **Token próprio.** `MCP_API_TOKEN` vai em `Authorization: Bearer …`.
  - Tem no mínimo 32 caracteres em produção.
  - Precisa ser diferente dos tokens de operador, executor e watchdog; o settings recusa se for igual.
  - O middleware de operador não cobre `/mcp`, porque o ChatGPT não envia `X-Proto-Operator-Token`.
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
2. Na API, defina `MCP_ENABLED=true` e `MCP_API_TOKEN=<segredo>`. Para ter ciclos contínuos, defina
   também `FINROBOTS_ENABLED=true`. Depois reinicie.
3. Exponha `/mcp` por HTTPS no proxy público (hoje ele não está roteado). Garanta que o proxy
   repasse o header `Authorization`.
4. No ChatGPT: Configurações → Apps e conectores → Modo desenvolvedor → Criar conector. Use a URL
   `https://<host>/mcp` e configure a autenticação com o bearer token.

## Não verificado (PARTIAL)

- **Autenticação do ChatGPT.** Não testei se o formulário de conector da conta do operador aceita
  bearer token estático. A documentação pública cita OAuth e “sem autenticação”, e as opções mudam
  por plano.
  - Se só OAuth for aceito, o próximo passo é um fluxo OAuth 2.1 com PKCE e registro dinâmico,
    conforme a especificação de autorização do MCP. Esse fluxo ainda não está implementado.
  - Não abra `/mcp` sem autenticação.
- **Conexão real.** Nenhuma chamada real do ChatGPT foi feita. Os testes usam o `TestClient` do
  FastAPI com o protocolo MCP implementado à mão (`tests/unit/test_mcp_surface.py`).
- **Proxy e deploy.** A rota no proxy e o deploy na Oracle não foram aplicados.
