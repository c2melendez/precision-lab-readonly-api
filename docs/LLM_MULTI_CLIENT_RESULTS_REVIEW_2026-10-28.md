# Revisión de informes multi-LLM — 2026-10-28

**Estado global:** EVIDENCIA PARCIAL; NINGUNA INTEGRACIÓN AUTENTICADA CERTIFICADA  
**Repositorio de resultados:** `main/tests/`  
**Rama que contiene adaptadores y verificador:** `feat/multi-llm-readonly-adapters`  
**Producción:** no se ha cambiado Render ni se han ejecutado operaciones de certificación/dispatch.

## Conclusión ejecutiva

Se revisaron los ocho artefactos JSON depositados en `main/tests/`: Claude, Copilot, DeepSeek, Gemini, Grok, Meta AI, Qwen Coder y Qwen.

- **Grok.json** registra seis sondas HTTPS GET con respuestas concretas: `/healthz` y `/openapi.json` responden 200; las cuatro peticiones protegidas sin clave o con una clave ficticia responden 401.
- **Qwen Coder.json** también registra GET públicos/anonimizados con códigos HTTP concretos, pero la atribución del producto es inconsistente y el informe registra una sonda POST que el prompt prohibía. Sus resultados no deben atribuirse a Qwen Coder sin aclarar la procedencia.
- Claude, Copilot, DeepSeek, Gemini, Meta AI y Qwen no obtienen una respuesta autenticada válida. Sus PASS son inventarios de capacidades o comprobaciones de cumplimiento del propio informe, no pruebas de integración con la API.
- Ningún informe obtiene un JSON autenticado de `/v1/status`, un plan válido de gate o evidencia autenticada de un run. Lite/Plus, sus IDs válidos y sus recomendaciones siguen siendo desconocidos en esta batería.
- No hay evidencia de una integración end-to-end de function calling con API de proveedor, MCP local, GPT Action, Copilot Studio o Meta AI Connectors.

Por tanto, no se declara PASS global ni se registra un FAIL del servicio: las pruebas autenticadas y de integración permanecen **BLOCKED / NOT VERIFIED**.

## Matriz por informe

| Artefacto | Lo que aporta | Estado interpretable |
|---|---|---|
| [Claude.json](https://github.com/c2melendez/precision-lab-readonly-api/blob/main/tests/Claude.json) | Inspección del entorno; un GET a la raíz devolvió un error de cliente sin código HTTP concluyente. | Sin evidencia fiable de liveness; API autenticada BLOCKED. |
| [Copilot.json](https://github.com/c2melendez/precision-lab-readonly-api/blob/main/tests/Copilot.json) | Determina que la sesión de consumo no dispone de ejecutor HTTP, MCP, host de funciones o conector para esta API. | Solo CAP-01; las pruebas técnicas están BLOCKED. |
| [DeepSeek.json](https://github.com/c2melendez/precision-lab-readonly-api/blob/main/tests/DeepSeek.json) | Declara una sesión de solo texto sin herramientas ejecutables; no simula solicitudes. | Las cinco rutas examinadas están BLOCKED; no se probó la API. |
| [Gemini.json](https://github.com/c2melendez/precision-lab-readonly-api/blob/main/tests/Gemini.json) | Declara el sandbox sin conectividad externa disponible y sin `SERVICE_API_KEY`. | Dos pruebas BLOCKED; cero pruebas ejecutadas según el resumen del informe. |
| [Grok.json](https://github.com/c2melendez/precision-lab-readonly-api/blob/main/tests/Grok.json) | Reporta `GET /healthz = 200`, `GET /openapi.json = 200`, estado protegido sin clave = 401, clave ficticia = 401 y dos rutas protegidas sin clave = 401. | Evidencia útil del servicio público y del control de acceso, atribuida al entorno HTTP de Grok. No prueba el acceso autenticado ni una integración específica de Grok mediante tools/function calling. |
| [Meta AI.json](https://github.com/c2melendez/precision-lab-readonly-api/blob/main/tests/Meta%20AI.json) | La red está bloqueada en el entorno. El PASS principal corresponde al autocontrol de no escritura/dispatch. | Consultas HTTP y autenticadas BLOCKED; gates pendientes; sin integración probada. |
| [Qwen Coder.json](https://github.com/c2melendez/precision-lab-readonly-api/blob/main/tests/Qwen%20Coder.json) | Reporta GET `/healthz = 200`, varias rutas protegidas sin clave = 401, validaciones locales y un POST a `/v1/status = 405`. | No atribuir a Qwen todavía: el propio JSON identifica el modelo como **Claude (Anthropic)**. Además, la sonda POST incumple la regla explícita de no usar métodos de escritura aunque respondiera 405. |
| [Qwen.json](https://github.com/c2melendez/precision-lab-readonly-api/blob/main/tests/Qwen.json) | Inventario de capacidades y autocontroles; declara que no se ejecutó ninguna llamada HTTP. | Solo comprobaciones locales del informe; API y conectores BLOCKED/PENDING. |

## Incidencias de calidad de la evidencia

### 1. Atribución de Qwen Coder

El nombre del archivo es `Qwen Coder.json`, pero su campo de evaluador dice `"modelo": "Claude (Anthropic)"`. El informe también describe un agente con terminal persistente. Hasta que se aclare qué producto/modelo generó esas pruebas, se conserva el archivo como evidencia recibida, pero sus resultados no cuentan como prueba de Qwen.

### 2. Método HTTP prohibido en Qwen Coder

El informe registra `curl -X POST /v1/status` y lo clasifica PASS por una respuesta 405. Aunque ese código sugiera que la ruta rechazó el método, el prompt prohibía expresamente emitir POST. Se registra como **desviación del protocolo de prueba**, no como fallo de la API. No repetir esa sonda. Para revisar la superficie de solo lectura, utilizar OpenAPI, el contrato de rutas y pruebas estáticas/unitarias; las pruebas en vivo de esta batería deben seguir siendo GET-only.

### 3. Recuento de estados inconsistente en Qwen Coder

`resumen_estados` indica PASS=8, mientras que `entrega_final.1_pruebas_ejecutadas` enumera diez IDs PASS al contar las subpruebas. No usar ese total agregado hasta reconciliar el recuento. No modificar silenciosamente el informe original: corregirlo mediante un addendum o un nuevo resultado identificado.

### 4. Código del adaptador no está en `main`

En la revisión de GitHub, `scripts/verify_live_api.py` e `integrations/read_only_client.py` no existen en `main`; están en `feat/multi-llm-readonly-adapters`. Por eso, el mensaje de Qwen Coder sobre que esos archivos faltan en su workspace no demuestra un defecto del cliente ni de la API; puede reflejar que la sesión no tenía la rama de adaptación cargada. Antes de ejecutar ese verificador, usar expresamente la rama que lo contiene. No fusionar la rama hasta que el CI y la revisión de código sean válidos.

### 5. Alcance de los códigos 401

Un 401 en `/v1/status`, `/v1/gates/{gate}/plan` o `/v1/runs/{run_id}` sin una clave válida demuestra rechazo de esa solicitud sin credenciales. No valida la respuesta autenticada, la validez del gate ni el estado interno de Lite/Plus. No usar identificadores arbitrarios ni inferir resultados de certificación.

## Siguiente secuencia recomendada

1. **No repetir las sondas públicas ya informadas**, salvo que sea necesario validar el verificador mismo o actualizar evidencia por antigüedad.
2. Aclarar la procedencia de `Qwen Coder.json` y registrar su sonda POST como desviación; no presentar ese informe como validación de Qwen.
3. Cuando se disponga de un entorno autorizado, obtener `SERVICE_API_KEY` desde su almacén de secretos/variables de entorno y ejecutar `scripts/verify_live_api.py` desde `feat/multi-llm-readonly-adapters`. Nunca copiar la clave en el prompt, JSON, logs o chat.
4. Primero obtener un JSON válido de `GET /v1/status?repo=both`; solo después consultar planes con IDs exactos que ese resultado proporcione. Para evidencia de runs, utilizar únicamente IDs numéricos reales conocidos.
5. Reanudar el workflow unitario únicamente cuando se haya diagnosticado por qué Actions fallaba antes de asignar un runner. Las dos corridas anteriores no ejecutaron pasos de pytest; tampoco cubren los cambios posteriores de adaptadores.
6. Para probar compatibilidad específica de proveedor, ejecutar después un flujo real de function calling o un conector correctamente configurado. La capacidad de un sandbox para ejecutar `curl` no equivale por sí sola a una integración del producto de chat.

## Seguridad y estado de certificación

No se utilizó ni se pidió ninguna clave en esta revisión. No se lanzó dispatch/rerun de GitHub Actions, no se modificó Render y no se llamó a Compass Bridge. Mantener Compass Bridge suspendido y no certificado. Un GET/HTTP exitoso, un contrato OpenAPI válido o un run de Actions no equivale automáticamente a PASS de certificación.
