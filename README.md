# Precision Lab — API de certificación de solo lectura

**Estado actualizado (corte 2026-10-26 21:00 UTC):** el planificador v2 está desplegado en Render. El deploy `dep-dbfnu8nlk1mc73fksl6g` figura `live` sobre el commit `b6d5efdcae0802d5b5a24c79d8d7373bfa9e1acd`. Los logs de producción confirman `GET /healthz` → `200` y `GET /openapi.json` → `200`; también confirman que `GET /v1/status` sin credenciales devuelve `401`, comportamiento esperado para una ruta protegida.

**Integración todavía no cerrada:** Render continúa reportando `healthCheckPath` vacío aunque `render.yaml` declara `/healthz`, así que hay deriva entre configuración versionada y configuración efectiva del servicio. No se ha probado una llamada autenticada correcta a `/v1/status?repo=both` ni al planificador. La presencia de `GITHUB_READ_TOKEN` y `SERVICE_API_KEY` no se pudo confirmar mediante metadatos; no asumir que faltan ni revelar valores. `autoDeployTrigger` está desactivado: los cambios del repositorio no despliegan por sí solos.

Servicio HTTP para que un GPT personalizado consulte el estado de Precision Lab Lite/Plus sin recibir credenciales de GitHub ni permisos para ejecutar operaciones. No se conecta a los productos como aplicación matemática y no modifica los repositorios.

## Rutas

| Método | Ruta | Función |
|---|---|---|
| GET | `/healthz` | Liveness público; no consulta GitHub ni muestra configuración. |
| GET | `/v1/status?repo=lite\|plus\|both` | Rama predeterminada/canónica, HEAD, revisión, bloqueos, estado de reconciliación, versión del runner y registro documental más reciente disponible. |
| GET | `/v1/gates/{gate}/plan?repo=lite\|plus&refresh=true` | Ejecuta la lógica de decisión en modo lectura; nunca inicia un gate. |
| GET | `/v1/runs/{run_id}?repo=lite\|plus` | Evidencia bruta de Actions, jobs/pasos y registro del runner relacionado cuando puede encontrarse. |
| GET | `/openapi.json` / `/openapi.yaml` | Especificación importable para GPT Actions. |

La API acepta únicamente los alias `lite` y `plus`, no nombres de repositorio arbitrarios. Responde con `Cache-Control: no-store` y `X-Read-Only: true`. Todas las operaciones documentadas de la API de certificación son GET. `success` de GitHub Actions nunca se transforma automáticamente en `PASS`.

## Seguridad requerida

### GitHub

Configura `GITHUB_READ_TOKEN` como un fine-grained token restringido a `c2melendez/precision-lab-lite` y `c2melendez/precision-lab-plus`, con solo:

- Repository permissions: **Contents: Read**.
- Repository permissions: **Actions: Read**.
- Metadata: Read (permiso de lectura estándar del repositorio).

No uses el PAT de escritura `GH_TOKEN` del Certification Runner. La API no contiene métodos para enviar solicitudes POST/PATCH/PUT/DELETE a GitHub. El cliente `GitHubReadClient` solo implementa GET.

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

Copia `.env.example` a `.env` **solo localmente** y configura `GITHUB_READ_TOKEN` y `SERVICE_API_KEY`. No añadas `.env` al repositorio.

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
