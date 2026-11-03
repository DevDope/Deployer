# Corregir El Conflicto De Puerto De LanguageTool

El PID identificado en el puerto 85 es Apache. No se detiene ni modifica.
LanguageTool pasa a 8081 y la revision ortografica recibe esa direccion por entorno.

1. En Deployer detiene TPSM. Los otros sistemas y Apache deben continuar activos.
2. Reemplaza los dos archivos externos del paquete en `D:\TSPM`: `LevantarPROD-TPSM.bat` y `Start-TPSM-Managed.ps1`.
3. Aplica el parche de una linea en ambas copias del repo, usando estas ordenes de PowerShell una por una:

```powershell
git -C "D:\TSPM\Sistema\SUIT_prod\SUIT_prod" apply --check "D:\TSPM\languagetool-url.patch"
git -C "D:\TSPM\Sistema\SUIT_prod\SUIT_prod" apply "D:\TSPM\languagetool-url.patch"
git -C "D:\TSPM\Sistema\SUIT_tunnel\SUIT_tunnel" apply --check "D:\TSPM\languagetool-url.patch"
git -C "D:\TSPM\Sistema\SUIT_tunnel\SUIT_tunnel" apply "D:\TSPM\languagetool-url.patch"
```

El archivo `languagetool-url.patch` debe estar en `D:\TSPM` para esas ordenes.
Si `--check` falla, no fuerces el parche: revisa si esa copia ya tiene el cambio.

El cambio equivalente, si prefieres editar manualmente, es reemplazar:

```python
LANGUAGETOOL_URL = "http://127.0.0.1:85/v2/check"
```

por:

```python
LANGUAGETOOL_URL = os.environ.get("LANGUAGETOOL_URL", "http://127.0.0.1:85/v2/check")
```

`os` ya esta importado en el archivo revisado. No reemplaces el archivo completo.

4. Conserva el panel v3.9 y services.json. En TPSM deja la deteccion automatica de puertos activada: debe mostrar 7003, 8015, 8081 y 8105.
5. Inicia TPSM desde el panel. Si 8081 esta ocupado por otro programa, el lanzador abortara sin cerrarlo. Si es LanguageTool, lo reiniciara.
6. Para probar el endpoint desde PowerShell:

```powershell
curl.exe --max-time 10 http://127.0.0.1:8081/v2/languages
```

La respuesta debe ser una lista JSON de idiomas. Despues prueba la revision ortografica desde TPSM.

El arranque automatico del panel se conserva: no cambian la ruta del BAT ni su carpeta.
