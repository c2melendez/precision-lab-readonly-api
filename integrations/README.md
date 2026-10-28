# Integraciones de Precision Lab con clientes LLM

Esta carpeta contiene adaptadores que no modifican la API desplegada y que no requieren privilegios de escritura en GitHub.

## 1. Requisitos

- Python 3.11+ (igual que el repositorio).
- Acceso HTTPS desde el proceso que ejecutará la herramienta.
- La clave de servicio `SERVICE_API_KEY`, proporcionada a ese proceso mediante un almacén de secretos o variable de entorno.
- Para el adaptador MCP: `python -m pip install -r requirements-dev.txt`.

No uses ni copies `GITHUB_READ_TOKEN` a la herramienta cliente. Esa credencial pertenece exclusivamente al servicio alojado en Render.

## 2. Adaptador MCP local (stdio)

El archivo `integrations/mcp_server.py` se lanza como un proceso local por un cliente MCP. El proceso consulta la REST API alojada; no necesita su propio token de GitHub ni abre un puerto público.

Antes de lanzar el cliente MCP, configura en el entorno del proceso:
- `SERVICE_API_KEY`: clave válida de la API de solo lectura.
- `PRECISION_LAB_API_BASE_URL`: opcional; por defecto apunta a `https://precision-lab-readonly-api.onrender.com`.

Configura la clave en el almacén de secretos o en el entorno del proceso que lanzará el cliente. No uses una lectura cifrada de PowerShell como valor de `SERVICE_API_KEY`: la salida cifrada no es la clave original que espera la API. No imprimas la variable ni la almacenes en un archivo versionado. El mecanismo exacto depende del sistema operativo y del cliente MCP.

Prueba de arranque:
```bash
python -m integrations.mcp_server
```

El proceso espera el protocolo MCP por stdin/stdout. No lo ejecutes en una terminal interactiva como si fuera un chat: la salida normal debe ser tráfico de protocolo. Para probar manualmente, utiliza MCP Inspector u otro cliente MCP compatible.

### Ejemplo para VS Code

En `.vscode/mcp.json` del usuario/workspace, puedes usar la entrada de credencial de tipo contraseña del propio VS Code (la sintaxis exacta puede variar con la versión instalada):

```json
{
  "inputs": [
    {
      "type": "promptString",
      "id": "precisionLabApiKey",
      "description": "Precision Lab read-only API key",
      "password": true
    }
  ],
  "servers": {
    "precision-lab-readonly": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "integrations.mcp_server"],
      "env": {
        "SERVICE_API_KEY": "\${input:precisionLabApiKey}",
        "PRECISION_LAB_API_BASE_URL": "https://precision-lab-readonly-api.onrender.com"
      }
    }
  }
}
```

Ejecuta el host con el directorio de trabajo en la raíz local del repositorio, o configura su entorno para que Python pueda importar el paquete `integrations`. No añadas la clave literal a un archivo versionado. Esta configuración es un ejemplo para VS Code, no una configuración universal para todos los clientes MCP.

### Claude Desktop y Gemini CLI

Ambos necesitan que se configure un servidor MCP local en el formato admitido por la versión instalada. Utiliza el comando `python` (o la ruta absoluta al intérprete del entorno virtual), como argumento el archivo `integrations/mcp_server.py`, y proporciona las dos variables de entorno a través del mecanismo seguro que ofrezca ese cliente/OS. No copies el ejemplo JSON de VS Code sin adaptarlo: las claves `servers`, `mcpServers` y el soporte de variables de entrada no son idénticos en todos los hosts.

Si el cliente no puede conservar la clave de forma segura, no lo conectes; usa otro perfil de ejecución autorizado.

## 3. Function calling en API de modelos

El módulo `integrations/read_only_client.py` expone:
- `openai_compatible_tool_schemas()`: esquemas para APIs con formato OpenAI-compatible, cuando el endpoint/modelo documenta esa compatibilidad.
- `gemini_function_declarations()`: declaraciones para el formato de function calling de Gemini.
- `execute_readonly_tool(name, arguments)`: ejecutor allowlisted para que la aplicación que integra el modelo realice el GET y devuelva el JSON al modelo.

La aplicación anfitriona debe implementar el ciclo propio del proveedor:
1. Enviar al modelo el esquema de herramientas.
2. Recibir la llamada de función solicitada por el modelo.
3. Ejecutar `execute_readonly_tool` en el servidor de confianza.
4. Devolver el resultado de la herramienta al modelo y pedir su respuesta final.

El modelo no ejecuta el código Python por sí solo. No declares una prueba end-to-end PASS hasta capturar el tool call real del modelo, el resultado real del ejecutor y la respuesta final basada en ese resultado.

## 4. REST, GPT Actions y Copilot Studio

- **REST genérico:** consulta `/openapi.yaml`; cualquier cliente HTTP autorizado puede usar `X-API-Key`.
- **ChatGPT GPT Action:** utiliza el OpenAPI existente en la configuración de Actions y define `X-API-Key` como clave de cabecera personalizada.
- **Copilot Studio:** importa el archivo Swagger 2.0 `integrations/copilot-studio/precision-lab-swagger2.json` y configura la clave `X-API-Key` como credencial segura. Enciende exclusivamente las operaciones GET.

No se ha desplegado un servidor MCP remoto en Render. El adaptador incorporado es local (stdio), y no debe confundirse con un endpoint MCP público para ChatGPT o una integración MCP remota de Anthropic.

## 5. Permisos y límite funcional

Los únicos nombres de herramientas expuestos por el adaptador son:
- `precision_lab_status`
- `precision_lab_gate_plan`
- `precision_lab_run_evidence`

No se aceptan URLs arbitrarias ni métodos HTTP elegidos por el modelo. La API key nunca debe formar parte de los argumentos, esquemas, prompts o resultados. Los tres métodos subyacentes son GET. `RUN` es un consejo de planificación, no un dispatch; una ejecución de Actions exitosa no equivale a certificación.
