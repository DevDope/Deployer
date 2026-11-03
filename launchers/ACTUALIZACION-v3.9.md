# Actualizacion v3.9

Reemplaza solamente:

- `deployer_panel.py` en `D:\Deployer\Deployer\deployer_panel.py`.
- `Start-TPSM-Managed.ps1` en `D:\TSPM\Start-TPSM-Managed.ps1`, junto al BAT externo.

Conserva `services.json`, el BAT externo y los archivos internos del tunel que ya funcionan.

## LanguageTool

Reinicia TPSM desde el panel para usar el supervisor nuevo. Si el puerto 85 pertenece a Java ejecutando LanguageTool, esa instancia se cierra y se inicia una nueva bajo supervision. Si el puerto pertenece a otro programa o no se puede identificar, el arranque se detiene sin cerrar ese proceso. Apache permanece independiente.

## Inicio Automatico

1. Abre el panel v3.9 como administrador.
2. Abre Herramientas > Activar inicio automatico.
3. Introduce la contrasena de Windows de la cuenta mostrada, no su PIN. No la compartas por chat.
4. Espera el mensaje de configuracion completada en Actividad.

Las tareas se configuran para arrancar 30 segundos despues de iniciar Windows, aunque nadie haya iniciado sesion. Cloudflared queda como servicio Automatico. El panel no almacena la contrasena en archivos; Windows administra las credenciales de las tareas. La cuenta debe ser la misma que guardo la clave IA cifrada.

Los botones de inicio conservan esa configuracion. Si cambias la ruta del BAT, su carpeta o la contrasena de Windows, vuelve a activar el inicio automatico.

La configuracion no se activa por actualizar los archivos: debes completar el formulario. Falta verificar un reinicio real del servidor; los tests locales no instalan tareas ni reinician servicios.
