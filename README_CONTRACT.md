# Contrato de API Observer para GPT Actions

**Estado:** Observer es el único punto de integración para el LLM. Lee estado y planes desde GitHub y solicita dispatch exclusivamente a través del Bridge interno; nunca llama directamente a GitHub Actions dispatch.

## Fuentes de verdad y coherencia

1. Leer metadata de ambos repositorios para resolver `default_branch`; no codificar `main` como supuesto permanente.
2. Leer `qa/certification/README.md` desde las ramas predeterminadas. Si los punteros canónicos de Lite y Plus no coinciden, devolver `409` y no planificar.
3. Leer `qa/certification/continuity.json` en la rama canónica; exigir correspondencia entre `repository`, `canonical_branch` y el repositorio real.
4. Obtener HEAD desde la referencia de rama, no desde un SHA copiado del chat o del ledger.
5. Obtener árbol recursivo del HEAD y de `technical_baseline_sha`. El árbol debe declarar `truncated: false`; si es incompleto no se calcula la huella.
6. Si la huella técnica no coincide con la línea base, `state_status=RECONCILE` y `next_action=null`. Si no puede leerse la línea base, `state_status=REFRESH` y `next_action=null`.
7. Para planificar, consultar la historia del workflow de interés. Se inspeccionan como máximo 15 runs recientes; si el endpoint no cubre todo el historial y no aparece un run equivalente, el resultado se degrada a `REFRESH`.
8. Las respuestas `success` de Actions no son aceptación del requisito. El servicio no atribuye automáticamente `logs_reviewed=true`, `evidence_executed=true` ni `acceptance_revision` a un run.

## Endpoints

- `GET /v1/status?repo=lite|plus|both`
- `GET /v1/gates/{gate}/plan?repo=lite|plus&refresh=true`
- `GET /v1/runs/{run_id}?repo=lite|plus`

La API key entrante se envía por `X-API-Key`. El token de GitHub solo vive en `GITHUB_READ_TOKEN` en el runtime, con permiso Contents: Read y Actions: Read en los dos repositorios. Observer no recibe ni utiliza el token GitHub de dispatch.

## Límites operativos conocidos

- No hay caché: las rutas `/v1/*` consultan GitHub en cada solicitud. El endpoint devuelve `Cache-Control: no-store` y la observación tiene edad cero al momento de responder.
- La relación entre un run hijo y su registro padre se busca en la cola más reciente de 25 archivos de runner. La ausencia de relación no prueba que nunca exista: el endpoint conserva la evidencia bruta del run y `record_relation=none` cuando no puede reconciliar el enlace en ese conjunto acotado.
- El runner v3 tiene en sus comentarios la regla de no usar `[skip ci]`, pero los registros documentales r294/r295/r307/r308 incluyen esa marca en el subject. El servicio lo reporta como advertencia; no modifica workflows ni historial.
- El único endpoint de mutación expuesto por Observer es `POST /v1/bridge/dispatch`; está autenticado por `X-API-Key`, valida primero el plan live y delega el dispatch en Bridge. No existen rutas de publicación, push, cancelación, reintento manual, actualización de refs o cambios de configuración.

## Endurecimiento adicional de la implementación

- La URL de la API de GitHub está fijada en `https://api.github.com`; no puede cambiarse mediante una variable de entorno. Así se evita redirigir accidentalmente el token de lectura a otro host.
- Los enlaces de runs se reconstruyen con el alias de repositorio permitido y el ID numérico. No se devuelven URLs arbitrarias provenientes de JSON documental.
- Si la huella técnica de HEAD no coincide con `technical_baseline_sha`, el endpoint de planificación devuelve `REFRESH`/`RECONCILE` antes de consultar históricos de workflows. No se gasta cuota tratando de elegir un gate cuando el estado de continuidad necesita conciliación.
- Los endpoints `/v1/*` requieren `X-API-Key`; `/healthz` es solo liveness, no acredita conectividad con GitHub.

## Integración Observer → Bridge

### Dispatch protegido
- `POST /v1/bridge/dispatch` es la única ruta de ejecución de Observer.
- Antes de llamar a Bridge, Observer recalcula el plan con GitHub live y exige `state_status=CURRENT`, la rama canónica exacta, el SHA HEAD observado y `decision=RUN`. `WAIT`, `REUSE`, `REFRESH`, `RECONCILE`, `DIAGNOSE` o `REVIEW` no generan dispatch.
- La solicitud exige `target_repo`, `gate`, `dry_run`, `idempotency_key`, `validation_id`, `correlation_id`, `branch` y `requested_sha`. No acepta campos adicionales.
- Observer llama a `POST /api/runs` solo server-to-server con `BRIDGE_DISPATCH_KEY`, que debe tener scope `dispatch`. El secreto no se devuelve ni se registra.
- El dispatch es idempotente en Bridge. Si hay timeout o resultado incierto, Observer devuelve `409` con `reconcile_required=true`; no reintenta a ciegas.

### Reconciliación protegida
- `GET /v1/bridge/requests/{idempotency_key}` consulta `GET /api/runs/lookup` mediante la clave `llm`, que debe tener únicamente scope `read`.
- La lectura se limita al propietario de la clave Bridge y puede reconciliar el estado observado sin crear ni reintentar workflows.
- `GET /v1/bridge/runs/{run_id}` conserva la consulta por UUID de recibo.

## Integración de estado Bridge — GET protegido

### Ruta
- `GET /v1/bridge/runs/{run_id}`
- `run_id` es el UUID interno de una ejecución Bridge (por ejemplo, el UUID de la tabla/recibo Bridge). **No** es el número entero de una ejecución de GitHub Actions.
- La ruta exige `X-API-Key` con la clave entrante `SERVICE_API_KEY` de Observador.
- Observador consulta el host configurado por `BRIDGE_BASE_URL` usando el secreto saliente `llm` únicamente como `X-Bridge-Key`. Esta clave tiene scope `read`; no puede ejecutar POST ni hereda permisos de dispatch.

### Respuestas y errores
- `200`: recibo encontrado; respuesta marcada `read_only: true`, con el UUID y el estado observado.
- `401`: falta `X-API-Key` o es inválida.
- `404`: UUID no encontrado en Bridge, o el recibo no pertenece a la clave/propietario autorizado por Bridge. No revela recibos de otros propietarios.
- `422`: formato de UUID/parámetros inválido.
- `502`: respuesta upstream inválida o error de comunicación con Bridge.
- `503`: configuración o servicio Bridge no disponible.

### Semántica de solo lectura
La operación upstream es exclusivamente HTTP GET. Puede actualizar metadatos operativos de lectura —por ejemplo, last-used/audit— y reconciliar el estado del recibo en Bridge; por ello “GET” no significa necesariamente ausencia absoluta de escrituras internas de auditoría. No crea recibos, no encola trabajos, no ejecuta ni reintenta workflows, no cancela ejecuciones y no cambia configuración.

**No hay aprobación humana por evento.** Observer decide con las políticas y pruebas existentes; las solicitudes rutinarias dentro del alcance se despachan automáticamente a través de Bridge. El LLM nunca conoce la URL interna ni la credencial Bridge. El scope `dispatch` se mantiene separado del scope `read`.

### Prueba de staging registrada — 2026-10-30
- Con `X-API-Key`: `200 OK`, `read_only: true`, recibo `94c102a5-5242-48ba-bdb7-4754a57513db`, repositorio `lite`, gate `cumulative`; la respuesta enlazó la ejecución preexistente de Actions `41005884405` con conclusión `success`.
- Sin `X-API-Key`: `401 Unauthorized`.
- La consulta de las últimas ejecuciones de Lite y Plus no mostró nuevos IDs tras esta prueba. La lectura no despachó workflows.
- El resultado acredita la ruta de lectura autenticada en staging; no certifica dispatch, producción, cierre de la certificación matemática ni aceptación global de los motores.
