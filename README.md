# Precision Lab — Observer API de certificación

**Estado de implementación (2026-10-30):** Observer es el único punto de integración del LLM. Su rama de trabajo incorpora planificación live, dispatch protegido mediante Bridge y reconciliación por clave idempotente. El CI de Observer se está validando en el PR; no se afirma despliegue ni E2E hasta verificar el flujo staging completo.

Servicio HTTP para que un GPT personalizado consulte el estado de Precision Lab Lite/Plus y solicite ejecuciones permitidas. Observer valida el plan y delega el dispatch en Bridge; nunca recibe credenciales de escritura de GitHub ni llama directamente a GitHub Actions dispatch. No se conecta a los productos como aplicación matemática.

## Rutas

| Método | Ruta | Función |
|---|---|---|
| GET | `/healthz` | Liveness público; no consulta GitHub ni muestra configuración. |
| GET | `/v1/status?repo=lite\|plus\|both` | Rama predeterminada/canónica, HEAD, revisión, bloqueos, estado de reconciliación, versión del runner y registro documental más reciente disponible. |
| GET | `/v1/gates/{gate}/plan?repo=lite\|plus&refresh=true` | Ejecuta la lógica de decisión en modo lectura; nunca inicia un gate. |
| GET | `/v1/runs/{run_id}?repo=lite\|plus` | Evidencia bruta de Actions, jobs/pasos y registro del runner relacionado cuando puede encontrarse. |
| GET | `/v1/bridge/runs/{run_id}` | Consulta un recibo Bridge por UUID interno. |
| GET | `/v1/bridge/requests/{idempotency_key}` | Consulta y reconcilia el recibo Bridge por clave idempotente. |
| POST | `/v1/bridge/dispatch` | Revalida plan, rama y SHA live; delega en Bridge solo si la decisión es RUN. |
| GET | `/openapi.json` / `/openapi.yaml` | Especificación importable para GPT Actions. |

La API acepta únicamente los alias `lite` y `plus`, no nombres de repositorio arbitrarios. Todas las respuestas usan `Cache-Control: no-store`. Las rutas GET llevan `X-Read-Only: true`; el endpoint POST de dispatch indica `X-Read-Only: false`. La autenticación entrante usa `SERVICE_API_KEY` en `X-API-Key`. Observer usa dos credenciales Bridge distintas: `llm` (scope `read`) y `BRIDGE_DISPATCH_KEY` (scope `dispatch`). `success` de GitHub Actions nunca se transforma automáticamente en `PASS`.

## Seguridad requerida

### GitHub

Configura `GITHUB_READ_TOKEN` como un fine-grained token restringido a `c2melendez/precision-lab-lite` y `c2melendez/precision-lab-plus`, con solo:

- Repository permissions: **Contents: Read**.
- Repository permissions: **Actions: Read**.
- Metadata: Read (permiso de lectura estándar del repositorio).

No uses el PAT de escritura `GH_TOKEN` del Certification Runner. La API no contiene métodos para enviar solicitudes POST/PATCH/PUT/DELETE a GitHub. El cliente `GitHubReadClient` solo implementa GET.

### Bridge de ejecución

Configura `llm` en Render con una clave Bridge de scope `read`, `BRIDGE_DISPATCH_KEY` con una clave independiente de scope `dispatch` y `BRIDGE_BASE_URL` con el origen HTTPS del Bridge. La clave de lectura se usa únicamente para GET de recibos; la clave de dispatch únicamente para POST server-to-server. Nunca compartas, registres ni devuelvas ninguna de las dos credenciales.

### GPT Action

`SERVICE_API_KEY` es una credencial independiente de GitHub. Configúrala en Render y después en la autenticación de la Action, como API Key en el header `X-API-Key`. No la incluyas en el prompt, el esquema OpenAPI ni en archivos versionados.

Genera una clave localmente si la necesitas:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

No compartas la clave por chat. Si se expone, rótala en Render y en la configuración de GPT.

## Ejecutar localmente

Requiere Python 3.11+.

```bash
python -m venv .venv
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

Copia `.env.example` a `.env` **solo localmente**. Configura `GITHUB_READ_TOKEN`, `SERVICE_API_KEY`, `llm`, `BRIDGE_DISPATCH_KEY` y `BRIDGE_BASE_URL` en el entorno seguro. No añadas `.env` al repositorio. `llm` es una credencial Bridge, no un token de GitHub ni una contraseña PostgreSQL.

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

La API key debe enviarse en cada endpoint `/v1/*`:

```bash
curl -H "X-API-Key: $SERVICE_API_KEY" "http://127.0.0.1:8000/v1/status?repo=both"
```

## Desplegar en Render

Este directorio debe publicarse como un repositorio independiente. **No agregues este servicio a los repositorios de producto Lite/Plus**: los cambios de código del servicio son ajenos a los gates matemáticos y de certificación de esos repositorios.

1. Instala Git for Windows y GitHub CLI (`gh`). Ejecuta `gh auth login --hostname github.com --git-protocol https --web`; el usuario autenticado debe tener autorización para crear repositorios privados bajo `c2melendez`.
2. Extrae el paquete, abre PowerShell en la raíz `precision-lab-readonly-api` y ejecuta `./scripts/publish-private-repo.ps1`. El script lista los archivos, rechaza archivos secretos/cachés, pide escribir `PUBLISH`, crea el repositorio como **Private** solo si no existe y nunca fuerza un push. Si el repositorio ya existe y es público, se detiene.
3. Confirma en GitHub que `c2melendez/precision-lab-readonly-api` es privado y que `main` contiene el código publicado. Si el push se rechaza por historial remoto preexistente, no hagas force-push: reconcilia el historial primero.
4. En Render, crea un Blueprint desde ese repositorio y revisa el recurso antes de aplicar. El `render.yaml` está en la raíz y tiene `autoDeployTrigger: off`.
5. Introduce `GITHUB_READ_TOKEN` y `SERVICE_API_KEY` como secretos en Render; nunca los guardes en Git. El token de GitHub debe tener solo Contents: Read y Actions: Read en los dos repositorios de Precision Lab.
6. En Render → **Settings → Health Checks**, configura `/healthz` y confirma que el ajuste del servicio deje de reportar `healthCheckPath` vacío. El archivo `render.yaml` ya declara esa ruta, pero el ajuste observado en el Dashboard todavía no la reflejaba.
7. Confirma que `PUBLIC_BASE_URL=https://precision-lab-readonly-api.onrender.com` corresponda al host público. El `render.yaml` actual ya contiene ese valor. Si cambias configuración, realiza el despliegue manual correspondiente porque `autoDeployTrigger: off`.
8. En Windows PowerShell 5.1, ejecuta `./scripts/verify-live-api.ps1 -BaseUrl "https://precision-lab-readonly-api.onrender.com"`. El verificador pide `SERVICE_API_KEY` con entrada oculta y comprueba HTTPS, URL de OpenAPI, métodos GET, estado bilateral y que el planificador indique `action_taken=false`. Solo realiza peticiones GET; no despacha gates. La prueba aún debe ejecutarse después de confirmar secretos y health check.
9. Descarga `https://precision-lab-readonly-api.onrender.com/openapi.yaml`, verifica que `servers[0].url` corresponda exactamente al host y luego impórtalo como Action en el GPT. Configura `X-API-Key` en la autenticación de la Action.
10. Revisa que las advertencias de `/v1/status` se clasifiquen correctamente antes de interpretar el estado.

El Blueprint utiliza el plan `free`, que puede suspender servicios tras inactividad y añadir latencia de arranque. Cambiar de plan requiere una decisión de coste separada. `autoDeployTrigger: off` evita que cada commit futuro se despliegue automáticamente; los despliegues deben iniciarse de forma deliberada.

## Planificación y frescura

- Se resuelven los punteros de rama canónica en `qa/certification/README.md` de ambos repositorios, en sus ramas predeterminadas. Si no coinciden, la API devuelve `409` y se abstiene de planificar.
- Se lee el `continuity.json` desde la rama canónica actual y se verifica que sus campos `repository` y `canonical_branch` coincidan.
- El endpoint de planificación utiliza la lógica y huella de `scripts/certification/continuity.py`. Se inspeccionan como máximo 15 runs recientes del workflow solicitado y los árboles de commits referenciados. Si la historia observada es incompleta y no aparece evidencia equivalente, devuelve `REFRESH`, no una autorización para ejecutar.
- No se marca `logs_reviewed` o `evidence_executed` automáticamente. Un run SUCCESS sin revisión comprobada se devuelve como `REVIEW`, no como `REUSE`.
- `technical_baseline_sha` se contrasta con el HEAD vivo. Si el árbol técnico difiere o no se puede obtener el objeto baseline, se suprime `next_action` y el resultado es `RECONCILE` o `REFRESH`.
- El servicio no es un oráculo de certificación. `REUSE` significa evidencia reutilizable según el planificador, no una ejecución nueva ni cierre global.

## Huella de continuidad, registros del runner y filtros de Actions

La corrección de huella ya está implementada tanto en la API como en los planificadores canónicos Lite y Plus:

- El planificador utiliza versión de huella `2` y excluye únicamente los archivos de registro generados con el patrón exacto `qa/certification/runner/[ID-numérico].json`. Estos archivos se escriben después de una corrida y no deben invalidar la misma evidencia que documentan.
- La exclusión es deliberadamente estrecha. Los esquemas y archivos no numéricos del directorio `runner/`, los tests, workflows, dependencias, fixtures y contratos de certificación continúan siendo entradas de huella.
- Las pruebas del planificador de la API comprueban tanto que el cambio de un registro numérico no invalida la huella, como que un archivo no numérico o un contrato de certificación sí la cambia.

### Validación bilateral registrada

Los índices canónicos registran la reparación como `VERIFIED_BILATERAL`, con los siguientes resultados exactos por SHA:

| Repositorio | Gate | Run | SHA validado |
|---|---|---|---|
| Lite | Cumulative | [40359884483](https://github.com/c2melendez/precision-lab-lite/actions/runs/40359884483) — SUCCESS | `8c2cd395752959dbdc70cdf4e262d7bf8c597b82` |
| Lite | H1d | [40359884531](https://github.com/c2melendez/precision-lab-lite/actions/runs/40359884531) — SUCCESS | `8c2cd395752959dbdc70cdf4e262d7bf8c597b82` |
| Lite | Runner | [40359884623](https://github.com/c2melendez/precision-lab-lite/actions/runs/40359884623) — SUCCESS | `8c2cd395752959dbdc70cdf4e262d7bf8c597b82` |
| Plus | Cumulative | [40360048532](https://github.com/c2melendez/precision-lab-plus/actions/runs/40360048532) — SUCCESS | `676f36ed2ca9566f5b37d27dd4c4f43065133f82` |
| Plus | H1d | [40360048942](https://github.com/c2melendez/precision-lab-plus/actions/runs/40360048942) — SUCCESS | `676f36ed2ca9566f5b37d27dd4c4f43065133f82` |
| Plus | Runner | [40360048710](https://github.com/c2melendez/precision-lab-plus/actions/runs/40360048710) — SUCCESS | `676f36ed2ca9566f5b37d27dd4c4f43065133f82` |

Estos runs validan la corrección de continuidad; no son evidencia de un cierre global ni justifican repetir los mismos gates.

### Commits documentales y `[skip ci]`

- La versión actual del runner registra resultados sin añadir `[skip ci]` al asunto del commit; se apoya en los filtros de rutas configurados para evitar ciclos de ejecución causados por archivos generados.
- Los commits históricos con `[skip ci]` se conservan sin reescritura. Para cada cambio documental futuro, revisar el `on.push` y los filtros de rutas del workflow afectado; los documentos contractuales que figuran como rutas de activación pueden necesitar validación aunque otros archivos Markdown se ignoren.
- El endpoint `/v1/status` alerta cuando identifica la marca en commits asociados a rutas que son entradas del gate. Una advertencia por sí sola no demuestra si se omitió una corrida; la conclusión debe basarse en los triggers y el historial real de Actions.

### Estado de certificación y siguiente objetivo

En el corte de 2026-10-26, Lite está en revisión documental r301 y Plus en r314. Ambos conservan `global_certification: OPEN`. El siguiente objetivo canónico es `H2-EN-SG-CASE-EVIDENCE-AUDIT-R314`: construir en Plus la matriz de evidencia por caso EN-SG-01..32 y confirmar cobertura existente antes de añadir pruebas. No repetir las corridas verdes de la reparación de continuidad. Los bloqueos S25, Algebra 229, EDCX-B5 y SY-X-04-PLUS-HTTP siguen según los estados del índice; SG28 distribuido permanece `OUT_OF_SCOPE_SINGLE_HOST`, que no equivale a PASS.

## Verificaciones locales realizadas

- Pruebas automatizadas: ejecutar `python -m pytest -q` en el paquete; el conteo puede cambiar al añadir regresiones.
- YAML de Render: sintaxis local comprobable; el CLI de Render no estaba instalado, por lo que no afirmo que se haya ejecutado `render blueprints validate` ni una validación de Blueprint con la API de Render.
- En este seguimiento se verificó el deploy `dep-dbfnu8nlk1mc73fksl6g` (`live`) y los logs de arranque. No se ejecutaron gates nuevos de certificación para este seguimiento.
- Queda pendiente validar en el Dashboard que `healthCheckPath=/healthz`, confirmar la configuración de los secretos sin exponer sus valores y ejecutar `verify-live-api.ps1` para comprobar los endpoints protegidos. Hasta completar esos pasos, la API no debe considerarse plenamente validada de extremo a extremo.

## 2026-10-27 — reconciliación externa de evidencia de Actions

- Plus run [40429424316](https://github.com/c2melendez/precision-lab-plus/actions/runs/40429424316): H1d SUCCESS, 38 pruebas Playwright, SHA canónico `c6549bfd4943938be0db2a43fad5bfa33b169708`.
- Lite run [40403964745](https://github.com/c2melendez/precision-lab-lite/actions/runs/40403964745): frontend SUCCESS sobre merge SHA `cdc9c94841505e3d39696fb4c349c7cc396743ef`, auditoría con 0 vulnerabilidades, typecheck, tests y build. No equivale a validar el SHA canónico de Lite.
- Ambos resultados son evidencia de gates de repositorio, no de la transacción API→Bridge→Runner. La prueba autenticada de `/v1/status?repo=both`, la reconciliación del health check efectivo y el E2E siguen pendientes.

## Bridge y automatización end-to-end — corte 2026-10-27

Actualización 2026-10-30: se añadió en la rama feature/bridge-readonly-run-status un endpoint GET protegido para consultar el estado de un run de Compass mediante llm/X-Bridge-Key. Está pendiente de revisión y despliegue; su inclusión en esta rama no certifica la conexión en producción.

La búsqueda en los repositorios disponibles no identificó un repositorio/endpoint Bridge inequívoco para la certificación. El término “Bridge” encontrado en Lite corresponde a stores de navegación UI/Argand/Graphing, no a una integración GitHub→API→Runner; no se considera evidencia del Bridge de certificación. El API sigue siendo estrictamente GET/read-only: no puede disparar workflows ni publicar resultados. Por tanto, el circuito de automatización requiere un componente externo identificado y sigue `NOT_YET_VERIFIED`. No crear un endpoint ficticio ni ampliar permisos de la API para suplir ese componente.


## Certificación de conexión alternativa — prioridad P0

La API de este repositorio sigue siendo **solo de lectura** y no implementa el Bridge de ejecución ni despacha GitHub Actions. La ruta completa `Push → API → Bridge → Runner → resultado verificable` permanece sin certificar hasta que cada eslabón tenga evidencia y la idempotencia sea durable.

Documentos operativos coordinados con el Prompt maestro (repositorios Lite/Plus, rama canónica activa):
- [HANDOFF_GITHUB_ALTERNATIVE_CONNECTION.md](https://github.com/c2melendez/precision-lab-plus/blob/qa/syntax-audit-in625-a1/qa/certification/HANDOFF_GITHUB_ALTERNATIVE_CONNECTION.md)
- [ROADMAP_GITHUB_ALTERNATIVE_CONNECTION.md](https://github.com/c2melendez/precision-lab-plus/blob/qa/syntax-audit-in625-a1/qa/certification/ROADMAP_GITHUB_ALTERNATIVE_CONNECTION.md)

El Prompt maestro de Lite y Plus prioriza estos documentos antes de reanudar la certificación general de motores matemáticos. El proyecto Supabase `oqlzftercvraprnvjifo` se reportó INACTIVE; no se ha inspeccionado el esquema porque las consultas de tablas/migraciones no pudieron completarse. No se aplicaron cambios de esquema.


## Separación de objetivos: API_GPT, GPT Actions y motor matemático

Este repositorio respalda el proyecto de ampliación de capacidades de API_GPT y la evaluación de GPT Actions en distintos LLMs. La API permite consultar el estado de Precision Lab Lite/Plus, inspeccionar planes de gates en modo lectura y recuperar evidencia de GitHub Actions. Es un instrumento de interoperabilidad/observabilidad; no es el motor matemático, no modifica los repositorios y no ejecuta gates.

La validación de esta API, la importación del esquema OpenAPI y las pruebas de GPT Actions/LLMs constituyen un frente independiente. **No son un prerrequisito ni un bloqueo para desarrollar o certificar el motor matemático.** Del mismo modo, la certificación matemática no certifica automáticamente API_GPT ni Compass/Bridge. Mantener resultados y criterios separados.

Compass/Bridge, si se utiliza, es un ejecutor distinto de esta API GET-only y conserva su propio backlog de seguridad, persistencia e integración. Un bloqueo de Compass no debe detener la continuación de las matrices matemáticas. Esta separación no autoriza ampliar permisos, añadir endpoints de escritura ni relajar controles de seguridad.

## Evidencia multi-LLM almacenada

El repositorio conserva ocho informes de la batería multi-cliente v2 en `tests/`: Claude, Copilot, DeepSeek, Gemini, Grok, Meta AI, Qwen Coder y Qwen. Los informes son evidencia de las capacidades reales de cada sesión; **no deben agregarse como si todos fueran pruebas exitosas de GPT Actions**.

| Cliente / informe | Resultado relevante |
|---|---|
| Grok 4.5 | 6 comprobaciones públicas PASS: `/healthz` y `/openapi.json` 200; endpoints protegidos sin clave y clave incorrecta 401. Autenticación válida, plan y run autenticados bloqueados por falta de `SERVICE_API_KEY`; GPT Action/MCP no configurados. |
| Qwen Coder | 7 PASS, 5 BLOCKED, 1 NOT VERIFIED, 1 N/A. Verificó liveness, rechazo sin clave, OpenAPI/schema, POST rechazado (405) y cold start; no completó endpoints autenticados ni function calling real. |
| Claude Sonnet 5.5 | Inventario de capacidades PASS; GET público no concluyente; autenticación bloqueada; GPT Action no configurada. |
| M365 Copilot | Inventario de capacidades PASS; sin canal HTTP/Action autorizado para la API, por lo que las pruebas de integración quedaron BLOCKED. |
| Qwen | 3 PASS de inventario/protección/no-escritura; 9 BLOCKED y 1 PENDING. La sesión era solo chat, sin HTTP, Action, MCP ni function-calling ejecutable. |
| DeepSeek | 5 pruebas BLOCKED; sesión de solo texto sin canal de ejecución. |
| Gemini | 2 pruebas BLOCKED por falta de red externa y credencial en el sandbox. |
| Meta AI / Muse Spark 1.1 | 1 PASS de no-escritura; 8 BLOCKED, 4 PENDING, 1 NOT VERIFIED y 3 N/A por restricciones del cliente y del entorno. |

**Conclusión de la batería:** se comprobó parcialmente la superficie pública y el comportamiento de rechazo de autenticación, especialmente con Grok y Qwen Coder. No hay en estos informes evidencia de una GPT Action de Precision Lab ejecutada end-to-end con autenticación válida en varios LLMs. El objetivo siguiente del frente API_GPT es completar esa evaluación en clientes que realmente soporten la Action y dispongan de la credencial en un entorno seguro, sin pedirla ni almacenarla en los informes. Esta carencia pertenece al frente de integración multi-LLM y no bloquea el motor matemático.
