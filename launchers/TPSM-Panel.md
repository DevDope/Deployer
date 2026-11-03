# TPSM En Deployer

## Archivos En El Servidor

Conserva `services.json`, los entornos virtuales y los settings privados.

| Archivo del paquete | Destino |
| --- | --- |
| `deployer_panel.py` | `D:\Deployer\Deployer\deployer_panel.py` |
| `externo\LevantarPROD-TPSM.bat` | `D:\TSPM\LevantarPROD-TPSM.bat` |
| `externo\Start-TPSM-Managed.ps1` | `D:\TSPM\Start-TPSM-Managed.ps1` |
| `tunnel\levantar_local.bat` | `D:\TSPM\Sistema\SUIT_tunnel\SUIT_tunnel\levantar_local.bat` |
| `tunnel\proyecto_suit\tunnel_settings.py` | `D:\TSPM\Sistema\SUIT_tunnel\SUIT_tunnel\proyecto_suit\tunnel_settings.py` |

Si tus rutas son distintas, edita los ajustes al principio del BAT externo.
Los puertos del tunel interno y del VirtualHost deben seguir coincidiendo.

## Primera Ejecucion

1. Cierra las instancias anteriores de PROD y del tunel. No cierres Apache ni los otros sistemas.
2. Abre el panel v3.9 como administrador.
3. Agrega TPSM seleccionando `D:\TSPM\LevantarPROD-TPSM.bat`.
4. Confirma la URL `https://tpsm.grupo-asicce.com` y los puertos `7003,8015,8105,8081`.
5. En Logs selecciona `D:\TSPM\logs\TPSM`. No necesita clave de inferencia: usa WORKSTATION_URL.
6. Inicia TPSM. La preparacion conserva check, migrate y collectstatic de PROD, y el BAT interno conserva sus validaciones y tests.

## Comportamiento

- PROD y el tunel son procesos propios: si uno termina, el supervisor cierra los otros procesos que haya creado.
- Detener y reiniciar desde el panel solicitan el cierre al supervisor. Durante una preparacion larga puede tardar; el panel no inicia una segunda instancia si no confirma el cierre.
- Apache se reutiliza o se inicia independientemente mediante WMI. Detener TPSM no lo detiene ni modifica su configuracion.
- Si Apache no responde en 8105, el arranque falla con un mensaje para revisar su configuracion. El lanzador no recarga Apache automaticamente.
- LanguageTool usa 8081; Apache conserva 85. Si 8081 ya esta ocupado por Java ejecutando el servidor de LanguageTool, se cierra esa instancia y se inicia una nueva bajo supervision. Si pertenece a otro programa, TPSM aborta sin detenerlo. PROD y tunel deben leer LANGUAGETOOL_URL del entorno.
- La workstation se comprueba durante cuatro segundos. Su ausencia se informa como advertencia, sin impedir el arranque.
- Los logs de cada proceso estan en `logs\TPSM` junto al BAT externo. La salida del panel muestra los pasos y errores del supervisor.

Las pruebas entregadas usan procesos simulados y no ejecutan migraciones ni servicios reales. La verificacion final debe hacerse en el servidor.

## Arranque Automatico Sin Iniciar Sesion

En el panel, abre Herramientas > Activar inicio automatico e introduce la contrasena de Windows de la cuenta que ejecuta el panel, no su PIN. No compartas esa contrasena por chat.

Windows guarda las credenciales en el Programador de tareas; el panel no las guarda en services.json ni en logs. Las tareas arrancan 30 segundos despues del inicio de Windows, con permisos elevados. Cloudflared queda como servicio Automatico.

Activar el arranque no sustituye una prueba de reinicio real. Los procesos pueden tardar varios minutos si recompilan o preparan la base de datos. La clave IA cifrada debe haberse guardado usando la misma cuenta de Windows.

Los botones de inicio y reinstalacion conservan las tareas con arranque automatico. Si cambias la ruta del BAT o su carpeta, vuelve a activar el inicio automatico con la contrasena para actualizar la tarea. Si cambia la contrasena de Windows, repite esa configuracion.
