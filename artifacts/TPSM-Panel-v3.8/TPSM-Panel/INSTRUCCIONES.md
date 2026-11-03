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
2. Abre el panel v3.8 como administrador.
3. Agrega TPSM seleccionando `D:\TSPM\LevantarPROD-TPSM.bat`.
4. Confirma la URL `https://tpsm.grupo-asicce.com` y los puertos `7003,8015,8105,85`.
5. En Logs selecciona `D:\TSPM\logs\TPSM`. No necesita clave de inferencia: usa WORKSTATION_URL.
6. Inicia TPSM. La preparacion conserva check, migrate y collectstatic de PROD, y el BAT interno conserva sus validaciones y tests.

## Comportamiento

- PROD y el tunel son procesos propios: si uno termina, el supervisor cierra los otros procesos que haya creado.
- Detener y reiniciar desde el panel solicitan el cierre al supervisor. Durante una preparacion larga puede tardar; el panel no inicia una segunda instancia si no confirma el cierre.
- Apache se reutiliza o se inicia independientemente mediante WMI. Detener TPSM no lo detiene ni modifica su configuracion.
- Si Apache no responde en 8105, el arranque falla con un mensaje para revisar su configuracion. El lanzador no recarga Apache automaticamente.
- LanguageTool se inicia si esta instalado y el puerto esta libre. Si el puerto ya esta ocupado, ese proceso se conserva y no se detiene desde TPSM.
- La workstation se comprueba durante cuatro segundos. Su ausencia se informa como advertencia, sin impedir el arranque.
- Los logs de cada proceso estan en `logs\TPSM` junto al BAT externo. La salida del panel muestra los pasos y errores del supervisor.

Las pruebas entregadas usan procesos simulados y no ejecutan migraciones ni servicios reales. La verificacion final debe hacerse en el servidor.
