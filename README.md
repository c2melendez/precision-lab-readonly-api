# Precision Lab — API de certificación de solo lectura

**Estado:** implementación preparada y probada localmente; todavía no desplegada en Render.

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
6. Verifica el despliegue y `GET /healthz`.
7. Cuando Render asigne la URL real, configura `PUBLIC_BASE_URL=https://<host-real>.onrender.com` y aplica el cambio. La especificación OpenAPI seguirá usando un marcador hasta conocer la URL real.
8. En Windows PowerShell 5.1, ejecuta `./scripts/verify-live-api.ps1 -BaseUrl https://<host-real>.onrender.com`. El verificador pide `SERVICE_API_KEY` con entrada oculta y comprueba HTTPS, URL de OpenAPI, métodos GET, estado bilateral y que el planificador indique `action_taken=false`. Solo realiza peticiones GET; no despacha gates.
9. Descarga `https://<host-real>.onrender.com/openapi.yaml`, verifica que `servers[0].url` corresponda exactamente al host y luego impórtalo como Action en el GPT. Configura `X-API-Key` en la autenticación de la Action.
10. Revisa que las advertencias de `/v1/status` se clasifiquen correctamente antes de interpretar el estado.

El Blueprint utiliza el plan `free`, que puede suspender servicios tras inactividad y añadir latencia de arranque. Cambiar de plan requiere una decisión de coste separada. `autoDeployTrigger: off` evita que cada commit futuro se despliegue automáticamente; los despliegues deben iniciarse de forma deliberada.

## Planificación y frescura

- Se resuelven los punteros de rama canónica en `qa/certification/README.md` de ambos repositorios, en sus ramas predeterminadas. Si no coinciden, la API devuelve `409` y se abstiene de planificar.
- Se lee el `continuity.json` desde la rama canónica actual y se verifica que sus campos `repository` y `canonical_branch` coincidan.
- El endpoint de planificación utiliza la lógica y huella de `scripts/certification/continuity.py`. Se inspeccionan como máximo 15 runs recientes del workflow solicitado y los árboles de commits referenciados. Si la historia observada es incompleta y no aparece evidencia equivalente, devuelve `REFRESH`, no una autorización para ejecutar.
- No se marca `logs_reviewed` o `evidence_executed` automáticamente. Un run SUCCESS sin revisión comprobada se devuelve como `REVIEW`, no como `REUSE`.
- `technical_baseline_sha` se contrasta con el HEAD vivo. Si el árbol técnico difiere o no se puede obtener el objeto baseline, se suprime `next_action` y el resultado es `RECONCILE` o `REFRESH`.
- El servicio no es un oráculo de certificación. `REUSE` significa evidencia reutilizable según el planificador, no una ejecución nueva ni cierre global.

## Hallazgo de continuidad: `[skip ci]`

La versión v3 del runner en `main` declara que los commits documentales no deben usar `[skip ci]`, pero los registros documentales r294/r295 y r307/r308 incluyen esa marca en el asunto de commit. El endpoint `/v1/status` emite una advertencia cuando la detecta. No se cambia el historial ni se altera el runner desde este servicio. Antes de concluir que los filtros `paths-ignore` por sí solos evitaron gates, inspecciona los triggers de los workflows y documenta/corrige el contrato en la rama correspondiente.

## Verificaciones locales realizadas

- Pruebas automatizadas: ejecutar `python -m pytest -q` en el paquete; el conteo puede cambiar al añadir regresiones.
- YAML de Render: sintaxis local comprobable; el CLI de Render no estaba instalado, por lo que no afirmo que se haya ejecutado `render blueprints validate` ni una validación de Blueprint con la API de Render.
- No se han ejecutado gates de certificación ni se ha modificado GitHub/Render desde este paquete.
