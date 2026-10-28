# Procedimiento de diagnóstico — GitHub Actions y verificación autenticada

**Fecha:** 2026-10-28  
**Repositorio:** `c2melendez/precision-lab-readonly-api`  
**Rama de adaptadores:** `feat/multi-llm-readonly-adapters`  
**Alcance:** diagnóstico y pruebas GET/pytest; no desplegar, no despachar gates, no modificar Compass Bridge.

## Estado de partida confirmado

Las corridas [40637329892](https://github.com/c2melendez/precision-lab-readonly-api/actions/runs/40637329892) y [40637713482](https://github.com/c2melendez/precision-lab-readonly-api/actions/runs/40637713482) fallaron antes de ejecutar los pasos. La consulta de los cuatro trabajos da cero pasos, runner no asignado y logs no descargables (`BlobNotFound`). El workflow pide `ubuntu-latest`.

Esto es evidencia de fallo previo al trabajo de test, **no** evidencia de que pytest haya fallado, ni confirmación por sí sola de cuota de Actions agotada. No hacer más reintentos ciegos.

## Parte A — Obtener el motivo exacto de Actions

### A1. Inspeccionar el mensaje de error del job

Abre cada corrida, entra en uno de los trabajos fallidos y revisa el banner superior y el apartado **Annotations** (anotaciones), además de cualquier mensaje de facturación o aprovisionamiento.

- [Run 40637329892](https://github.com/c2melendez/precision-lab-readonly-api/actions/runs/40637329892)
- [Run 40637713482](https://github.com/c2melendez/precision-lab-readonly-api/actions/runs/40637713482)

Anota literalmente el mensaje de diagnóstico y el nombre del job. Si hay texto sobre tokens, secretos o datos personales, elimínalo antes de compartirlo. Si no aparecen anotaciones ni mensaje detallado, registra explícitamente “UI sin anotación legible”; no infieras una causa.

### A2. Comprobar uso y límites de facturación

En GitHub, abre tu perfil → **Settings** → **Billing & licensing** (la etiqueta puede variar) → **Usage** o **Plans and usage** → sección **Actions**.

Comprueba:
- minutos de GitHub-hosted runners usados y cuota incluida;
- fecha de reinicio del ciclo de facturación;
- si existe límite de gasto que bloquee uso adicional;
- estado de pago/facturación indicado por GitHub, sin compartir datos de pago.

La documentación oficial confirma que los repositorios privados tienen una cuota de minutos incluida según el plan y que el uso que excede esa cuota puede facturarse; no significa que esta sea necesariamente la causa de estas corridas: https://docs.github.com/en/actions/concepts/billing-and-usage

No cambies a un plan de pago, no aumentes el límite de gasto ni publiques el repositorio como solución automática. Primero identifica el mensaje exacto y decide conscientemente.

### A3. Comprobar la política de Actions del repositorio

En el repositorio, abre **Settings → Actions → General** y verifica:
- que GitHub Actions esté habilitado;
- que la política permita las acciones usadas por el workflow (`actions/checkout@v4` y `actions/setup-python@v5`);
- que no exista una restricción de runners/políticas que contradiga `runs-on: ubuntu-latest`.

No cambies permisos globales ni amplíes la política sin una razón confirmada.

### A4. Elegir la remediación en función del mensaje

- **Límite o bloqueo de facturación confirmado:** compara la cuota disponible y la fecha de reinicio; decide entre esperar el reinicio o autorizar gasto adicional. No activar gasto sin aprobación.
- **Política de Actions/runners confirmada:** corregir solo la política señalada.
- **Fallo de aprovisionamiento/infraestructura sin explicación:** no volver a lanzar repetidamente. Conserva las dos URL, los IDs de job y el texto de la UI y abre una solicitud a GitHub Support.
- **Todo lo anterior parece correcto:** compartir la anotación exacta antes de cambiar el YAML. Los dos intentos fallidos no justifican, por sí solos, modificar el workflow para usar otro runner.

No se recomienda configurar ahora un runner autohospedado sin revisar primero seguridad, disponibilidad y quién puede disparar workflows: ejecutaría código del repositorio en una máquina propia.

## Parte B — Ejecutar tests sin depender de GitHub-hosted runners

Esto puede hacerse en una máquina local autorizada con Python. No requiere credenciales de producción ni genera tráfico a la API.

### B1. Usar la rama correcta

Asegúrate de estar en `feat/multi-llm-readonly-adapters`, ya que el verificador y los adaptadores nuevos están en esa rama, no en `main`.

Compruébalo con:

```powershell
git branch --show-current
```

### B2. Instalar y ejecutar los tests offline

Desde la raíz del repositorio, en PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Si Python 3.12 no está instalado, usa 3.11. No definas `SERVICE_API_KEY` para esta fase. Conserva la salida completa de pytest y el código de salida. Un PASS local valida esos tests en ese equipo, pero no sustituye el CI reproducible en GitHub.

## Parte C — Verificación autenticada de solo lectura

### C1. Preparar el secreto sin pegarlo en el chat

La clave `SERVICE_API_KEY` debe proceder del almacén de secretos autorizado donde se conserva el valor original. El panel de Render normalmente oculta el valor configurado; no hay que intentar extraerlo de logs.

Si ya no existe una copia segura del valor original, detente y solicita autorización para rotar la clave en el servicio y actualizar los clientes secretos que la usan. No se rota como parte de este diagnóstico sin aprobación explícita.

### C2. Ejecutar el verificador

Con el repositorio local en la rama de adaptadores, instala dependencias antes de cargar la clave. En PowerShell, la siguiente secuencia solicita la clave como entrada protegida y la elimina de las variables de entorno al terminar:

```powershell
$secure = Read-Host "SERVICE_API_KEY" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
  $env:SERVICE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
  python scripts/verify_live_api.py
}
finally {
  Remove-Item Env:SERVICE_API_KEY -ErrorAction SilentlyContinue
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
  $secure.Dispose()
}
```

El verificador está pensado para realizar solo GET, redacción de secretos y host de API restringido. No uses argumentos de gate/run hasta obtener IDs reales de una respuesta autenticada o fuente documental fiable. No compartas el valor de la clave ni capturas donde aparezca.

### C3. Interpretar los resultados

1. Primero comprueba liveness/OpenAPI y la autenticación.
2. Solo si `GET /v1/status?repo=both` devuelve un JSON autenticado válido, utiliza los identificadores exactos documentados en esa respuesta para consultar planes de gate.
3. Solo consulta evidencia de run usando un `run_id` numérico real ya conocido.
4. Un 401 sin clave demuestra rechazo anónimo, no un fallo del servicio.
5. No consideres un status HTTP 200 como certificación; conserva códigos, cuerpos sanitizados, hora y salida del verificador.

## Entregables que permiten decidir el siguiente paso

1. Mensaje exacto de la sección Annotations de Actions, o confirmación de que la UI no muestra ninguno.
2. Resultado de la comprobación de Actions usage/quota/limit sin datos de facturación sensibles.
3. Salida íntegra de `python -m pytest -q` ejecutado en la rama de adaptadores.
4. Salida sanitizada de `python scripts/verify_live_api.py` cuando esté disponible una clave autorizada en el entorno.

Hasta obtener estas evidencias: no relanzar Actions, no fusionar la rama, no desplegar Render y mantener Compass Bridge suspendido y no certificado.
