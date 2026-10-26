# Operación y límites

## Estados que devuelve el planificador

- `RUN`: no se observó evidencia equivalente verificada en el snapshot disponible. Es una recomendación, no una orden para despachar. Si la historia está truncada, se degrada a `REFRESH`.
- `WAIT`: ya existe una ejecución equivalente en cola o curso.
- `REUSE`: solo debe aparecer si el registro de observaciones acredita revisión de logs, ejecución real de los pasos, aceptación vigente y jobs correctos. Esta implementación no asigna esas banderas de revisión por inferencia desde GitHub; un SUCCESS normal termina en `REVIEW`.
- `DIAGNOSE`: existe una ejecución equivalente fallida y se requiere clasificar causa antes de reintentar.
- `REVIEW`: el resultado necesita revisión humana o evidencia adicional.
- `REFRESH`: el snapshot o el histórico observado no permite una decisión fiable.

## Errores

- `400`: parámetro/gate no permitido o combinación bloqueada por política (incluye `security` en Plus mientras S19 continúe sin decisión contractual).
- `401`: falta o no coincide `X-API-Key`.
- `404`: run inexistente o recurso solicitado no encontrado.
- `409`: punteros de rama canónica o identidad de continuidad no coinciden.
- `502`: objeto Git, árbol, JSON o respuesta de GitHub incompleta/invalidada.
- `503`: falta la credencial, GitHub deniega lectura o alcanza el límite de API.

No se proporciona un endpoint de dispatch, publicación documental, cambio de rama, cancelación o reintento. La ruta `healthz` solo indica que el proceso responde; no acredita disponibilidad de GitHub.
