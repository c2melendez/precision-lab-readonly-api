# Contrato de API de solo lectura para GPT Actions

**Estado:** implementación local preparada; pendiente de publicar en un repositorio dedicado y desplegar en Render. El host de `openapi.yaml` permanece como marcador hasta conocer la URL real.

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

La API key se envía por `X-API-Key`. El token de GitHub solo vive en `GITHUB_READ_TOKEN` en el runtime, con permiso Contents: Read y Actions: Read en los dos repositorios. No se utiliza el `GH_TOKEN` con permisos de escritura del runner.

## Límites operativos conocidos

- No hay caché: las rutas `/v1/*` consultan GitHub en cada solicitud. El endpoint devuelve `Cache-Control: no-store` y la observación tiene edad cero al momento de responder.
- La relación entre un run hijo y su registro padre se busca en la cola más reciente de 25 archivos de runner. La ausencia de relación no prueba que nunca exista: el endpoint conserva la evidencia bruta del run y `record_relation=none` cuando no puede reconciliar el enlace en ese conjunto acotado.
- El runner v3 tiene en sus comentarios la regla de no usar `[skip ci]`, pero los registros documentales r294/r295/r307/r308 incluyen esa marca en el subject. El servicio lo reporta como advertencia; no modifica workflows ni historial.
- No se exponen endpoints de dispatch, publicación, push, cancelación, reintento, actualización de refs o cambios de configuración.

## Endurecimiento adicional de la implementación

- La URL de la API de GitHub está fijada en `https://api.github.com`; no puede cambiarse mediante una variable de entorno. Así se evita redirigir accidentalmente el token de lectura a otro host.
- Los enlaces de runs se reconstruyen con el alias de repositorio permitido y el ID numérico. No se devuelven URLs arbitrarias provenientes de JSON documental.
- Si la huella técnica de HEAD no coincide con `technical_baseline_sha`, el endpoint de planificación devuelve `REFRESH`/`RECONCILE` antes de consultar históricos de workflows. No se gasta cuota tratando de elegir un gate cuando el estado de continuidad necesita conciliación.
- Los endpoints `/v1/*` requieren `X-API-Key`; `/healthz` es solo liveness, no acredita conectividad con GitHub.
