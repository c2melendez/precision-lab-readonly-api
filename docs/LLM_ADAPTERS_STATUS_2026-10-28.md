# Estado de trabajo — adaptadores LLM multi-cliente

**Fecha UTC:** 2026-10-28  
**Repositorio:** \`c2melendez/precision-lab-readonly-api\`  
**Rama:** \`feat/multi-llm-readonly-adapters\`  
**Estado:** IMPLEMENTADO EN RAMA; REVISIÓN Y PRUEBAS PENDIENTES  
**No desplegado:** sí. Render conserva la configuración existente y \`autoDeployTrigger: off\`.

## Objetivo

Eliminar la falsa premisa de que toda consulta de la API debe ejecutarse desde una GPT Action. La API es REST de solo lectura y puede ser consumida por cualquier cliente HTTPS autorizado. Para probar una integración LLM concreta se necesita, además, que el host real del modelo ejecute la herramienta y devuelva su resultado al modelo.

## Cambios introducidos en esta rama

- \`integrations/read_only_client.py\`: cliente HTTPS de solo lectura, esquemas de herramientas comunes y Gemini, ejecutor con allowlist, clave desde el entorno y host fijado por defecto al servicio oficial.
- \`integrations/mcp_server.py\` y \`integrations/__init__.py\`: servidor MCP local stdio con tres herramientas de observación; no es un endpoint MCP remoto ni modifica Render.
- \`integrations/copilot-studio/precision-lab-swagger2.json\`: contrato Swagger 2.0 limitado a GET para importar en Copilot Studio.
- \`tests/test_read_only_client.py\` y \`tests/test_compatibility_contracts.py\`: tests de contrato/validación con transporte HTTP simulado.
- \`scripts/verify_live_api.py\`: verificador multiplataforma de API en vivo; utiliza solo GET, nunca imprime la clave y no necesita GPT Actions.
- \`docs/LLM_CLIENT_COMPATIBILITY.md\`: matriz de perfiles y límites por proveedor.
- \`docs/LLM_INTEGRATION_TEST_BATTERY_V2.md\`: batería ampliada API / MCP / function-calling / Copilot / GPT Action, con estados PASS/FAIL/BLOCKED/NOT VERIFIED/PENDING.
- \`.github/workflows/llm-adapter-tests.yml\`: flujo independiente para tests unitarios sin credenciales ni llamadas a producción.
- README e instrucciones de integración actualizados.

## Evidencia de prueba

Se creó una corrida de GitHub Actions al añadir el workflow:

- Run: [40637329892](https://github.com/c2melendez/precision-lab-readonly-api/actions/runs/40637329892)
- Commit probado: \`02afaa467f0274a817421e36eb2e79c872b1cd16\`
- Conclusión reportada: \`failure\`
- Dos jobs de matriz terminaron en aproximadamente 2–3 segundos, con \`runner_id: 0\` y sin pasos observados. Los logs de los jobs devolvieron BlobNotFound en la consulta disponible.

**Interpretación:** no existe evidencia de que \`pytest\` se haya llegado a ejecutar. Registrar como \`BLOCKED / FAIL ANTES DE EJECUTAR LOS PASOS\`, no como test unitario fallido ni como código aprobado. La causa exacta de la falta de runner no quedó establecida; no asumir que sea un fallo del código ni asegurar que sea una cuota sin evidencia adicional.

No se han lanzado nuevas corridas tras esta observación para evitar reintentos redundantes sin resolver primero el problema de ejecución de Actions.

## Próximos pasos

1. Cuando GitHub Actions pueda asignar un runner, ejecutar el workflow \`LLM read-only adapter tests\`; confirmar que se ejecutan checkout, instalación y pytest en Python 3.11 y 3.12.
2. Corregir cualquier fallo de código que aparezca en logs reales y volver a ejecutar solo después de identificar la causa.
3. Validar \`scripts/verify_live_api.py\` con una clave guardada de forma segura. La prueba autenticada y los perfiles reales de proveedor permanecen pendientes hasta contar con un host/credencial autorizados.
4. Ejecutar individualmente los perfiles solicitados (Gemini API, DeepSeek API, Qwen API, Grok API, Copilot Studio, Copilot MCP, GPT Action y cualquier superficie Meta AI exacta). Una declaración de compatibilidad genérica no sustituye una prueba real por producto/modelo.
5. Revisar y abrir PR cuando pueda revisarse el resultado de pruebas. No integrar en \`main\`, no desplegar a Render y no modificar secretos como parte de este trabajo.

## Límites de seguridad que permanecen

- API REST: solo GET.
- \`SERVICE_API_KEY\`: únicamente en el host seguro del cliente, nunca en prompts, esquemas, mensajes del modelo o evidencia.
- \`GITHUB_READ_TOKEN\`: solo en Render; nunca se copia a adaptadores.
- \`RUN\`: recomendación, no ejecución.
- Éxito de HTTP/Actions: no equivale a PASS de certificación.
- No dispatch, commit, push, cancel, rerun, publicación ni invocación de Compass Bridge.
- Compass Bridge conserva su suspensión y estado no certificado.
