# Batería específica para Qwen Coder — v1

**Fecha:** 2026-10-28  
**Repositorio evaluado:** `c2melendez/precision-lab-readonly-api`  
**Rama de adaptadores:** `feat/multi-llm-readonly-adapters`  
**Estado:** plan preparado; todavía no ejecutado en el entorno real de Qwen Coder.

## Objetivo

Probar de manera reproducible el entorno denominado **Qwen Coder** sin confundir:
1. el nombre de la interfaz/producto que ejecuta el agente;
2. el modelo de lenguaje que esa interfaz declara usar;
3. el host/sandbox que ejecuta terminal, Python, red y herramientas;
4. el adaptador de Precision Lab;
5. la integración real de Qwen Code/MCP o function calling, si existe en ese entorno.

El archivo `tests/Qwen Coder.json` anterior es evidencia de una sesión con terminal y red; sin embargo, declara “Claude (Anthropic)” como modelo subyacente. Esto no demuestra que el producto interfaz no fuera Qwen Coder, pero sí que la procedencia de la etiqueta de modelo debe aclararse. No editar el informe original para ocultar la discrepancia: registrar una ejecución nueva y separada.

## Matriz de pruebas

Usa prefijo `QC-` para no mezclar los resultados con los identificadores `Txx` de la batería general o `LV-Txx` del verificador REST.

| ID | Prueba | Criterio de PASS | Si falta capacidad/evidencia |
|---|---|---|---|
| QC-01 | Identidad del entorno y procedencia | Registra nombre exacto del producto/interfaz, versión si está disponible, modelo declarado y fuente del dato. Separa modelo de interfaz/host. | NOT VERIFIED si el modelo exacto no se muestra; no adivinarlo. |
| QC-02 | Workspace y revisión del repositorio | Muestra raíz de Git, rama, SHA HEAD y estado Git; los valores coinciden con la rama de adaptación prevista. | BLOCKED si no está disponible el repo/rama correctos; no hacer checkout ni push por cuenta propia. |
| QC-03 | Capacidades del entorno | Prueba la disponibilidad real de terminal, Python, HTTP GET, lectura de archivos y MCP; distingue herramienta disponible de herramienta realmente ejecutada. | Marcar por capacidad; no inferir que la UI equivale a Qwen Code CLI. |
| QC-04 | Presencia de archivos de integración | Verifica por ruta explícita `integrations/read_only_client.py`, `integrations/mcp_server.py`, `scripts/verify_live_api.py`, `requirements-dev.txt` y tests nuevos. | Si falta alguno, comprobar primero rama/workspace. No declarar un defecto del producto por examinar una copia de `main`. |
| QC-05 | Tests offline del repositorio | Ejecuta `python -m pytest -q` en la rama de adaptación y conserva la salida y código de retorno. Sin `SERVICE_API_KEY` ni llamadas live innecesarias. | BLOCKED si faltan dependencias o el entorno no puede instalar/ejecutar; FAIL solo si pytest ejecuta y falla. |
| QC-06 | Liveness pública | GET `/healthz` devuelve el resultado esperado de salud. Registrar hora UTC, HTTP y respuesta sanitizada. | BLOCKED si la red/política no permite el acceso; un bloqueo de navegador no prueba que el servicio falle. |
| QC-07 | Contrato público OpenAPI | GET `/openapi.json` devuelve la especificación y contiene las rutas de solo lectura conocidas. | BLOCKED si no hay red; registrar versión/esquema observado si está disponible. |
| QC-08 | Control de acceso sin clave | GET `/v1/status?repo=both` sin `X-API-Key` devuelve HTTP 401 esperado. | FAIL únicamente si la petición se ejecuta y contradice el contrato. HTTP 401 demuestra rechazo anónimo, no avería. |
| QC-09 | Estado autenticado | Si `SERVICE_API_KEY` ya está disponible en entorno/secret store autorizado, ejecuta `scripts/verify_live_api.py` y registra estado real de Lite/Plus sin mostrar la clave. | BLOCKED si no está disponible; nunca pedir al usuario que la pegue en el prompt. |
| QC-10 | Plan de gate | Solo después de un JSON autenticado con ID de gate exacto, consulta el plan de ese gate. | PENDING si no hay gate exacto; nunca adivinar. |
| QC-11 | Evidencia de run | Solo con un `run_id` numérico real, conocido y proporcionado por una respuesta autorizada, consulta la evidencia del run. | PENDING si no hay ID real. |
| QC-12 | Redacción de secretos | Verifica que logs/resultados no incluyan valores de `SERVICE_API_KEY` ni `GITHUB_READ_TOKEN`. Solo comprobar presencia/ausencia de la variable; nunca imprimir su contenido. | FAIL si un secreto se muestra; detener y tratar el registro como sensible. |
| QC-13 | Descubrimiento MCP (solo si aplica) | En Qwen Code CLI genuino, inspecciona herramientas/servidores configurados con `/tools` y `/mcp`. Registra transportes disponibles. | N/A para una interfaz distinta o BLOCKED si no se expone MCP. No asumir que MCP está configurado. |
| QC-14 | MCP end-to-end (solo preconfigurado/autorizado) | Si el servidor ya está configurado y autorizado, llama únicamente a las herramientas de lectura `precision_lab_status`, `precision_lab_gate_plan`, `precision_lab_run_evidence` y registra respuestas reales. | BLOCKED si falta cliente/servidor/configuración. No instalar ni cambiar configuración global de Qwen sin autorización. |
| QC-15 | Function calling real (opcional) | Solo si hay API del modelo y un host de herramientas autorizado: modelo emite llamada, host ejecuta la función allowlist y devuelve resultado real. | NOT VERIFIED/BLOCKED si solo hay terminal o chat, sin ciclo real modelo-host. |
| QC-16 | Cumplimiento de solo lectura | En toda prueba contra el servicio, las solicitudes HTTP son GET. No hay POST/PUT/PATCH/DELETE, escritura, dispatch, cancelación, rerun, publicación ni llamada a Compass Bridge. | La ejecución previa que incluyó POST se registra como desviación de protocolo; no repetir esa sonda. |

### Distinción importante

Un sandbox con `curl` puede verificar REST, pero eso no certifica por sí solo una integración de Qwen Coder ni de un modelo Qwen. A su vez, si el entorno es la herramienta oficial **Qwen Code CLI**, la documentación del proveedor describe un agente de terminal y soporte MCP; aun así, la configuración efectiva de ese workspace debe inspeccionarse en la sesión real antes de declarar cualquier capacidad.

Referencias oficiales (consultadas para diseñar esta batería):
- [Qwen Code — overview y ejecución CLI](https://qwenlm.github.io/qwen-code-docs/en/users/overview/)
- [Qwen Code — commands, tools y MCP commands](https://github.com/QwenLM/qwen-code/blob/main/docs/users/features/commands.md)
- [Qwen Code — conexión MCP](https://qwenlm.github.io/qwen-code-docs/en/users/features/mcp/)
- [Qwen Code — configuración del sandbox y políticas de red](https://qwenlm.github.io/qwen-code-docs/en/users/configuration/settings/)

## Prompt para ejecutar en Qwen Coder

Copia y ejecuta este bloque dentro del entorno real de Qwen Coder. No incluyas secretos en el texto.

---

Eres el evaluador técnico dedicado al entorno que el usuario identifica como **Qwen Coder** y a la integración de solo lectura de Precision Lab.

Objetivo: ejecutar la batería QC-01 a QC-16 que corresponda a las capacidades reales de esta sesión y crear un informe nuevo en JSON. No reutilices ni sobrescribas `tests/Qwen Coder.json`.

### Reglas obligatorias

1. Primero identifica el producto/interfaz, el modelo que declara la interfaz, el entorno de ejecución y las fuentes de cada dato. No escribas “Claude” solo porque lo diga un archivo anterior; verifica la sesión actual. Si el modelo no está expuesto, indica “no expuesto”.
2. No asumas que el workspace tiene el repositorio correcto. Inspecciona raíz, rama y SHA. Los archivos nuevos esperados están en `feat/multi-llm-readonly-adapters`, no necesariamente en `main`. No cambies rama, publiques cambios ni hagas push/commit.
3. Informa qué herramientas están realmente disponibles. Si es el CLI de Qwen Code y sus comandos están soportados, puedes inspeccionar `/tools` y `/mcp`; si la interfaz no usa esos comandos, no los simules.
4. No ejecutes operaciones de escritura en la API. Contra `https://precision-lab-readonly-api.onrender.com` realiza exclusivamente GET. No POST, PUT, PATCH, DELETE, dispatch, cancel, rerun, publicación ni llamadas a Compass Bridge. No pruebes “si POST está bloqueado”; ya existe una desviación registrada por esa sonda y no debe repetirse.
5. Ejecuta los tests offline solo si están presentes la rama y los archivos esperados. Usa `python -m pytest -q`; no declares PASS si no se llegó a ejecutar pytest. Puede instalarse `requirements-dev.txt` si el usuario y la política del entorno autorizan la instalación de dependencias, pero no instales configuraciones MCP globales ni cambies las restricciones de red.
6. Para pruebas públicas, usa solo GET a `/healthz`, `/openapi.json` y, cuando sea apropiado, `/v1/status?repo=both` sin credenciales para verificar el rechazo. Registra código HTTP y respuesta sanitizada. Si la red está bloqueada, marca BLOCKED y no infieras estado de la API.
7. Para autenticación, solo usa `SERVICE_API_KEY` si ya está disponible a través del almacén de secretos o entorno seguro del ejecutor autorizado. No la solicites al usuario ni la imprimas, la leas al log o la incluyas en archivos. Si falta, marca QC-09/QC-10/QC-11 BLOCKED/PENDING según corresponda.
8. No adivines gates, run IDs, versiones ni identidad del modelo. No consideres HTTP 200 como certificación.
9. Cada resultado requiere un ID QC-, estado PASS/FAIL/BLOCKED/NOT VERIFIED/PENDING/N/A, qué se ejecutó realmente, fecha/hora UTC si es fiable, código HTTP cuando exista, referencia de evidencia, limitación/dependencia y siguiente paso.
10. Al terminar, revisa la salida en busca de secretos y de errores de atribución. Guarda el resultado como archivo nuevo `tests/Qwen Coder run YYYY-MM-DD.json` únicamente si crear archivos de evidencia locales está autorizado; en caso contrario, muestra el JSON sanitizado para que el usuario lo guarde.

### Informe final

Entrega:
- identidad del producto/interfaz y modelo con fuente;
- rama/SHA exactos y presencia de los archivos;
- herramientas disponibles y realmente usadas;
- resultados QC-01...QC-16;
- salida completa de pytest si se ejecutó;
- códigos HTTP y JSON real sanitizado si se ejecutaron GET;
- estado interno de Lite/Plus solo si se obtuvo una respuesta autenticada válida;
- bloqueos, discrepancias, riesgos y siguiente paso exacto;
- declaración expresa de cero métodos distintos de GET, cero escrituras, cero dispatch y cero llamadas a Compass Bridge.

No conviertas análisis teórico en PASS. No repitas trabajo que ya tiene evidencia vigente. Cuando no haya capacidad o credencial, marca BLOCKED, no inventes resultados.

---
