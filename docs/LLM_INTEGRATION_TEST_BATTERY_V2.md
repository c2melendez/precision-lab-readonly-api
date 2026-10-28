# Precision Lab — multi-client integration test battery v2

**Purpose:** verify the read-only API from more than one kind of LLM/tool host without assuming every LLM session has GPT Actions.  
**Target REST API:** \`https://precision-lab-readonly-api.onrender.com\`  
**Adapter in this branch:** local stdio MCP and vendor-neutral function-tool executor.  
**Safety boundary:** observation-only; no GitHub Actions dispatch, commits, pushes, writes, reruns, cancels or Compass Bridge calls.

## Rules before running

1. Identify the **actual client and execution path** before the test: REST client, GPT Action, local MCP, Gemini API, DeepSeek API, Qwen API, xAI API, Copilot Studio, or another named tool host.
2. A model that only replies in text and has no configured HTTP/tool executor cannot run integration tests. It may evaluate reasoning tests but must label them \`BLOCKED\` for integration. Never ask the model to "pretend" it called an API.
3. Store \`SERVICE_API_KEY\` in a trusted process environment/secret store. Never paste it into prompts or put it into evidence. The hosted API holds its separate GitHub read token.
4. Do not guess gate ids. Obtain the exact gate id from current authenticated status or reviewed repository documentation. If no valid gate can be established, mark dependent tests \`PENDING\`.
5. The API is GET-only. \`RUN\` means recommendation, not dispatch. \`HTTP 200\` is not certification PASS. Keep Compass Bridge suspended.
6. Reuse evidence already recorded when host, URL, build/version, test meaning and freshness criteria still match. Do not rerun merely to fill a table.

## Result labels

- \`PASS\`: the named test actually ran in the named client and its observed result met the criteria.
- \`FAIL\`: it actually ran and violated the expected contract.
- \`BLOCKED\`: required client, networking, permission, credential configuration or prerequisite was unavailable.
- \`NOT VERIFIED\`: evidence does not establish the claim.
- \`PENDING\`: a dependency is not complete.
- \`N/A\`: the client profile does not provide that route (for example, consumer chat without external tools).

For every execution record: test id, date/time UTC, product/client, model/version if relevant, path/profile, result, redacted evidence or request ID, HTTP status where available, and sanitized error. Never attach request headers containing secrets.

## A. API-level tests (not specific to any LLM)

| ID | Test | Execution options | PASS criteria | Failure / block handling |
|---|---|---|---|---|
| T01 | Public liveness | Browser, curl, Python, Postman, client HTTP tool | \`GET /healthz\` returns HTTP 200 JSON with \`read_only=true\` | DNS/network failure = BLOCKED if no external request could be made; live non-200 = FAIL pending diagnosis |
| T02 | Authentication guard | Any HTTP client, no key | \`GET /v1/status?repo=both\` without the key returns 401 and no repo status | 200 without auth = security FAIL; no network capability = BLOCKED |
| T03 | Authenticated bilateral status | REST client or any tool host that can issue HTTP | \`GET /v1/status?repo=both\` returns an authenticated JSON payload with Lite and Plus entries or a documented service/upstream error | 401 = auth configuration problem observed; 5xx = observed service/upstream problem; no executor = BLOCKED |
| T04 | Authenticated Lite status | Any authorized REST client/tool host | \`repo=lite\` returns only Lite status | Do not infer state from T03 if the payload does not include the needed fields |
| T05 | Authenticated Plus status | Any authorized REST client/tool host | \`repo=plus\` returns only Plus status | Same rule as T04 |
| T06 | Gate id grounding | Read T03/T04/T05 payloads and trusted repo documentation | Exact gate id exists in the observed authoritative source | If unavailable, NOT VERIFIED and plans remain PENDING; do not guess IDs |
| T07 | Lite gate plan | REST client or function executor; exact gate from T06 | Authenticated \`GET /v1/gates/{gate}/plan?repo=lite&refresh=true\` returns a parseable result; \`read_only=true\`; \`action_taken=false\` | Error codes are evidence, not proof that no future diagnosis is needed |
| T08 | Plus gate plan | Same as T07, with a Plus gate valid for Plus | Same read-only criteria; do not assume Lite's gate ids exist in Plus | If no grounded Plus gate, PENDING |
| T09 | Raw run evidence | REST client or tool host, with a known run id | \`GET /v1/runs/{run_id}?repo=lite|plus\` returns evidence for that run | Unknown id or repository mismatch is not a reason to guess other IDs |
| T10 | No blind gate probing | Review request and execution log | No arbitrary gate ids are sent to discover valid ids | Guessing/probing to identify a gate = protocol FAIL |

**Note:** HTTP status codes and API responses must be interpreted against the current OpenAPI contract. A 401 without a key is expected, not an API outage.

## B. Local stdio MCP adapter (new in this branch)

Prerequisites: install development dependencies with \`python -m pip install -r requirements-dev.txt\`; set \`SERVICE_API_KEY\` in the environment of the trusted MCP host; optionally set \`PRECISION_LAB_API_BASE_URL\`. The default base URL is the deployed HTTPS service. Run \`python integrations/mcp_server.py\` only as an MCP stdio process; it is not a public HTTP server.

| ID | Test | Execution options | PASS criteria | Failure / block handling |
|---|---|---|---|---|
| T11 | MCP initialization/discovery | MCP Inspector or the target MCP client | Server starts and lists exactly the three intended read tools | Missing MCP runtime/config = BLOCKED |
| T12 | MCP bilateral status | Call \`precision_lab_status(repo="both")\` from the MCP client | Result matches the authenticated REST response for the same time window | Do not mark PASS if the tool call is only described in text |
| T13 | MCP gate plan | Call \`precision_lab_gate_plan\` with grounded \`repo\` and \`gate\` | Payload is read-only, contains a decision, and has \`action_taken=false\` | Gate prerequisite unavailable = PENDING |
| T14 | MCP run evidence | Call \`precision_lab_run_evidence\` with a known numeric run id | Payload belongs to the requested repo/run; does not certify from success alone | Missing run id = PENDING |
| T15 | MCP key isolation | Inspect tool schemas, model messages, results, and redacted process logs | No API key appears outside the secure process environment/request header | Any key exposure = security FAIL; rotate the exposed key |

VS Code, Gemini CLI, Claude Desktop and other local MCP hosts have distinct configuration formats. A successful generic MCP Inspector test is not an end-to-end PASS for every named product.

## C. Provider function-calling tests

The adapter supplies \`openai_compatible_tool_schemas()\`, \`gemini_function_declarations()\`, and \`execute_readonly_tool(name, arguments)\`. The **host application** executes the function call and returns its result to the model. These helpers do not themselves sign in to provider APIs or make the model invoke a function.

| ID | Test | Execution options | PASS criteria | Failure / block handling |
|---|---|---|---|---|
| T16 | OpenAI-compatible tool schemas | Automated unit tests; inspect JSON | Contains only status, gate-plan and run-evidence functions; all parameters are schema-bound | Invalid schema = FAIL |
| T17 | Gemini function declarations | Gemini API client using function calling | Declaration is accepted; app executes the selected function via the allowlisted executor and supplies the response back to Gemini | No Google API key/host code = BLOCKED, not FAIL |
| T18 | DeepSeek tool calling | DeepSeek developer API plus a host executor | Model tool call leads to one matching read-only function execution and a final model response grounded in returned JSON | Consumer DeepSeek web chat alone is not evidence of API tool integration |
| T19 | Qwen tool calling | Selected Qwen/DashScope developer API plus host executor | Same round trip, using the selected endpoint's supported schema and tool-call format | Do not assume all Qwen user interfaces accept custom tools |
| T20 | Grok tool calling | xAI developer API plus host executor | Same round trip; API tool-call id and tool output are associated correctly | No xAI API key/host code = BLOCKED |
| T21 | Claude with local MCP | Claude Desktop or another MCP client capable of launching local stdio servers | Server is discovered, authenticated status tool succeeds, and tool results are used correctly | Claude's remote MCP API connector is a different route; it cannot connect directly to this local stdio process |
| T22 | Gemini CLI MCP | Configured Gemini CLI MCP client | Configured local server discovers and executes the status tool | This does not validate the Gemini consumer website/app |
| T23 | GitHub Copilot MCP | GitHub Copilot coding agent or VS Code only when the selected environment supports MCP | The server is configured and a real tool call returns current status | Do not generalize to all Microsoft Copilot surfaces |
| T24 | Copilot Studio REST | Copilot Studio with appropriate license/permissions | Import \`integrations/copilot-studio/precision-lab-swagger2.json\`, configure \`X-API-Key\`, and run a GET status action | Import/configuration not available = BLOCKED |
| T25 | ChatGPT custom GPT Action | Only in a GPT where this Action is configured and enabled | Action invocation log plus authenticated response from T03 is available | Do not require this route for other profiles |
| T26 | ChatGPT custom remote MCP | Only eligible workspace/product, with a separately hosted remote MCP endpoint | Remote connection discovers tools and authenticated status call succeeds | \`N/A\` for the local stdio adapter; this branch does not deploy a public MCP endpoint |
| T27 | Meta AI consumer chat | The specific consumer interface being evaluated | Mark PASS only if that exact interface offers a supported custom tool/API connection and it makes a real call | Otherwise \`NOT VERIFIED\` or \`N/A\`; a custom Llama/model API application is a separate profile |

Provider tests T17–T20 are separate cases. A function schema that works in one provider does not prove it works in another, and tests that lack provider credentials or an executable host remain BLOCKED.

## D. Safety, interpretation and continuity

| ID | Test | PASS criteria |
|---|---|---|
| T28 | Read-only boundary | Adapter exposes only the three allowlisted functions; client code can issue GET only; no POST, PATCH, PUT, DELETE, dispatch, commit, push or Bridge invocation exists in the tool path |
| T29 | Result semantics | \`RUN\` is presented as advice; HTTP 200 is not certification; GitHub Actions success is raw evidence; unknown gate/state remains unresolved |
| T30 | Continuity/handoff | Report preserves evidence references, actual status per profile, exact blocker and next step; doesn't repeat already valid observations or treat a reasoning-only PASS as integration PASS |

## Starter record

Copy this block for each run of the battery; use one row per test and profile.

| Test | Client/product and version | Profile | State | HTTP/status or evidence reference | Notes / next dependency |
|---|---|---|---|---|---|
| T01–T30 | Fill in individually | REST / Action / MCP / provider SDK / Copilot | PENDING | Not run | Do not leave a range scored as one PASS |

## Initial execution order

1. Install and run local unit tests for the adapter and schema (no live API call required).
2. Run T01–T05 using any available authorized REST client, not necessarily a GPT Action. Reuse recent T01/T02 evidence only if the URL/build and meaning still match.
3. Run T06 before T07–T08. Do not invent or probe for gate names.
4. Run T11–T15 through one supported MCP client; then separately configure any other named MCP product.
5. Run provider-specific T17–T20 only in the actual API host for that provider.
6. Run T24/T25 only when the target Copilot Studio/GPT Action is explicitly configured.
7. Complete T28–T30. Compass Bridge remains suspended and uncertified throughout.

## Evidence required for a final integration claim

A claim such as "DeepSeek integration PASS" requires the exact host, model/API version, tool schema submitted, actual tool-call request from that model, executor result from this API, the response supplied back to the model, and an end response derived from returned data. A claim such as "API PASS" needs only the defined API tests. A reasoning-only report cannot prove either network path.
