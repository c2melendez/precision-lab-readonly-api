# Compatibility guide: Precision Lab read-only API across LLM clients

**Status:** implementation guide; client support varies by product, account, plan, region, and host configuration.  
**Reviewed:** 2026-10-27. Verify the linked vendor documentation before enabling a new client.

## Core design decision

The service remains a vendor-neutral HTTPS REST API. It does **not** require GPT Actions in order to run. A caller needs:

- network access to `https://precision-lab-readonly-api.onrender.com`;
- the service key stored in the caller/agent's secure configuration and sent as `X-API-Key`;
- permission to make outbound HTTPS requests (or a configured tool host that makes the request on the model's behalf).

The service exposes only GET routes for observations. `RUN` is a recommendation, not a dispatch; no client can use this API to commit, push, or start GitHub Actions. Each integration needs its own verification. A model's ability to generate a tool call is not the same as the host executing the call.

## Execution profiles

| Profile | Best fit | What executes the HTTP request | Key handling | Compatibility status |
|---|---|---|---|---|
| Direct REST / Python / curl / Postman | LLM sessions with a real HTTP or terminal tool; operator diagnostics | The HTTP client | `SERVICE_API_KEY` in environment/secret store | Supported by the API; test each caller's network access |
| OpenAI custom GPT Action | ChatGPT GPTs configured with Actions | GPT Action runtime | Configure API key as custom header `X-API-Key` in the Action, never in the prompt | Supported by existing OpenAPI v3 schema, subject to product/model/workspace permissions |
| Local stdio MCP adapter added in this branch | MCP-capable local tools/agents | The user's MCP host launches `integrations/mcp_server.py`; adapter calls the REST API | Pass `SERVICE_API_KEY` in the server process environment | Implemented in code; must pass the new local MCP tests and be configured per client |
| Function calling via an LLM provider API | Gemini, DeepSeek, Qwen, Grok, and other model APIs that support function calling | Your application/tool host executes the returned function call using `execute_readonly_tool()` | API credentials stay in server-side environment variables; never sent to the model as prompt text | Generic schemas and executor provided; each model/API version needs its own integration test |
| Microsoft Copilot Studio REST tool | Custom agents built in Copilot Studio | Copilot Studio connector | Configure header API key in the connector | Import the Swagger/OpenAPI 2.0 compatibility file from `integrations/copilot-studio/`; requires eligible product/license and configuration |
| ChatGPT custom remote MCP app | ChatGPT Pro with read/fetch limits, or Business/Enterprise/Edu workspaces where custom MCP connections are enabled, subject to current workspace rules | ChatGPT MCP host | Configure the authentication supported by that MCP connection | Not directly served by the local stdio adapter; requires a separately reachable HTTPS MCP endpoint or supported secure tunnel |
| Consumer chat website/app without custom tool integration | Plain chat-only sessions | No external HTTP executor available | No safe place to configure a service key | Not compatible by prompt alone; use a supported tool-enabled client, MCP host, custom agent, or a separate application |

## Provider-specific notes

### OpenAI / ChatGPT

- **Custom GPT Actions:** import `https://precision-lab-readonly-api.onrender.com/openapi.yaml` and configure API-key authentication in the custom header named `X-API-Key`. Do not paste the key into GPT instructions.
- An Action test is only valid when the Action itself was invoked and its request/response is captured. A call made by curl or another HTTP client validates the API, not the GPT Action.
- OpenAI's current documentation distinguishes GPT Actions from MCP apps: Actions need an OpenAPI schema and configured authentication, and may be unavailable for some model modes or workspace policies. Custom remote MCP connections have separate plan/workspace availability; the local stdio adapter in this repo is not a remote MCP endpoint. ChatGPT Pro remote MCP access is restricted to read/fetch permissions; full MCP tool access is documented for Business/Enterprise/Edu subject to workspace permissions.
- The current Help Center article also carries a migration/retirement notice for custom GPTs affecting some Enterprise workspaces. Because rollout and replacement availability may vary, verify the notice and the target workspace before making a long-term integration depend solely on GPT Actions; keep the REST/MCP client-neutral routes as alternatives.

Official docs:  
- https://developers.openai.com/api/docs/actions/introduction  
- https://help.openai.com/en/articles/9442513-configuring-actions-in-gpts  
- https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt

### Claude / Anthropic

- Claude clients that can launch local stdio MCP servers can use the included adapter with the API key kept in the local process environment.
- The Anthropic Messages API's remote MCP connector is a different integration: it expects a reachable HTTPS MCP endpoint and uses an authorization token for its remote connection. This repo's adapter is **stdio**, not a deployed remote MCP service.
- Do not mark remote MCP compatibility PASS just because the local adapter starts.

Official docs:  
- https://platform.claude.com/docs/en/agents-and-tools/mcp-connector  
- https://docs.anthropic.com/en/docs/agents-and-tools/mcp-connector

### Google Gemini

- The Gemini API supports function calling, but the application that uses the API must execute the requested function and return its result. Use `gemini_function_declarations()` and `execute_readonly_tool()` in the integration host.
- Gemini CLI documents MCP server support and can run a compatible local MCP process.
- This does not imply that the consumer Gemini website or mobile app can connect to an arbitrary private API automatically.

Official docs:  
- https://ai.google.dev/gemini-api/docs/function-calling  
- https://geminicli.com/docs/tools/mcp-server/

### DeepSeek

- DeepSeek API tool/function calling returns tool-call instructions; the calling application must execute the function and send the result back to the model. The API itself does not magically execute this repo's Python functions.
- The common OpenAI-compatible schema is available through `openai_compatible_tool_schemas()`. Check the selected model's current tool-calling mode and API format before running integration tests.
- This is support for a **custom application using DeepSeek API**, not proof that the consumer DeepSeek chat UI accepts arbitrary API tools.

Official docs:  
- https://api-docs.deepseek.com/guides/tool_calls/  
- https://api-docs.deepseek.com/guides/agent_integrations/

### Qwen / Alibaba Cloud Model Studio

- Qwen API function calling is also a model-to-application handshake: the host application is responsible for executing the tool and returning its output.
- Use the OpenAI-compatible schema when the selected endpoint supports that format; otherwise adapt the schema and tool-call loop to the selected DashScope/API endpoint.
- Do not infer custom API access in the consumer Qwen chat product from support in the developer API.

Official docs:  
- https://docs.qwencloud.com/developer-guides/tool-calling/function-calling  
- https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-dashscope

### Grok / xAI

- The xAI API supports function calling. The application that receives a function-call request is responsible for performing the external operation and returning the result.
- Use `openai_compatible_tool_schemas()` where the selected endpoint follows the documented compatible schema; confirm current constraints with the xAI documentation.
- This covers a custom application using the xAI API, not arbitrary external API tools in every consumer Grok interface.

Official docs:  
- https://docs.x.ai/developers/tools/overview  
- https://docs.x.ai/developers/tools/function-calling

### Microsoft Copilot

There are several distinct products; test the one actually used.

- **Copilot Studio:** supports REST API tools from OpenAPI, with current documentation specifying OpenAPI/Swagger 2.0 input for this feature. Import `integrations/copilot-studio/precision-lab-swagger2.json`, configure `X-API-Key`, and enable only GET operations.
- **GitHub Copilot / VS Code agent with MCP:** can use configured MCP servers in supported environments. Configure the local stdio adapter in the appropriate MCP settings and store the service key in a secret/secure environment setting.
- This does not establish support in every consumer Microsoft Copilot chat surface, account or license.

Official docs:  
- https://learn.microsoft.com/en-us/microsoft-copilot-studio/agent-extend-action-rest-api  
- https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/configure-mcp-servers  
- https://code.visualstudio.com/docs/agents/reference/mcp-configuration

### Meta AI / Llama

- The compatibility checked here does **not** establish a consumer Meta AI chat interface that accepts an arbitrary user-defined REST API or MCP server. Do not treat "Meta AI" and a custom application built with a Llama model as equivalent.
- If using a Meta/Llama developer API or a self-hosted model that exposes tool/function calling, the host application must implement the HTTP executor with the same safety rules. Validate the exact API/product and model version first.
- Until that separate integration exists and is tested, record consumer Meta AI direct access as `NOT VERIFIED / NO CUSTOM TOOL CHANNEL ESTABLISHED`, not as PASS or a permanent claim that no such feature could exist.

## Secret handling

- `SERVICE_API_KEY` protects the Precision Lab read-only API. It is not a GitHub token.
- `GITHUB_READ_TOKEN` is held by the API service and must never be copied into the LLM client.
- Put `SERVICE_API_KEY` in a secret store or process environment for the trusted tool host. Never include it in a prompt, model message, tool schema, repository file, issue, or shared log.
- If the key was exposed, rotate it in Render and in every authorized client configuration.
- A 401 from a request without the key means that particular unauthenticated request was rejected. It does not prove that the API is broken.
- Do not permit arbitrary URLs, HTTP methods, repo names, gate IDs discovered by guessing, or user-supplied run links in a generic tool executor. Use the fixed host and allowlisted read operations implemented in this repository.

## Verification vocabulary

Use these statuses consistently:

- `PASS`: the specific operation actually ran in the named host and its observable result met expectations.
- `FAIL`: the operation ran and returned an observed result that violates the expected contract.
- `BLOCKED`: the required client, credentials, network, permissions, or prerequisite is unavailable.
- `NOT VERIFIED`: no reliable evidence establishes the claim.
- `PENDING`: a prerequisite has not yet been fulfilled; do not execute the dependent test.
- `N/A`: the profile does not claim that capability (for example, local stdio MCP is not the same as a remote MCP server).

Never promote reasoning-only PASS into an integration PASS.
