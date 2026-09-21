# Pipeline local de clases de CIBERSEGURIDAD

Este proyecto descarga grabaciones autorizadas de Zoom indicadas en `urls.txt` y las transcribe localmente con `faster-whisper`. Conserva estado individual por grabación, evita duplicados, reanuda descargas parciales y produce TXT, SRT y VTT.

El proyecto no contiene cookies, contraseñas ni mecanismos para evadir restricciones de Zoom. La autenticación se obtiene directamente de la sesión local de Opera mediante `yt-dlp --cookies-from-browser opera`.

## Requisitos

- Windows 10 u 11.
- Python 3.10 o posterior.
- Chrome (preferido) u otro navegador compatible con una sesión que tenga acceso a las grabaciones.
- `ffmpeg` y `ffprobe` disponibles en `PATH`.
- Al menos 10 GiB libres por defecto. Este umbral puede cambiarse en `config.json`.
- Conexión a Internet para las descargas y, si no está en caché, para obtener el modelo de Whisper.

## Instalación

Abra PowerShell en esta carpeta y cree un entorno virtual:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Si PowerShell bloquea la activación del entorno, puede habilitarla solamente para esa ventana:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

Los lanzadores usan automáticamente `.venv\Scripts\python.exe` cuando existe. También pueden funcionar con el `python` global si las dependencias están instaladas allí.

### Instalar FFmpeg y FFprobe

Instale una compilación de FFmpeg para Windows mediante su gestor de paquetes habitual o desde un proveedor enlazado por el sitio oficial de FFmpeg. Con `winget`, una opción habitual es:

```powershell
winget install --id Gyan.FFmpeg -e
```

Cierre y vuelva a abrir PowerShell después de instalarlo. Compruebe ambos ejecutables por separado:

```powershell
ffmpeg -version
ffprobe -version
```

## Configuración

`config.json` incluye las opciones principales:

```json
{
  "whisper_model": "medium",
  "language": "es",
  "browser": "chrome",
  "prefer_gpu": true,
  "keep_video": true,
  "delete_temporary_audio": true,
  "expected_url_count": null,
  "minimum_free_space_gb": 10,
  "minimum_yt_dlp_version": "2025.01.26",
  "max_video_height": 720
}
```

`expected_url_count: null` permite añadir nuevas clases. La configuración actual usa `medium`, Chrome como primera sesión de autenticación y un batch conservador para GPU de 4 GB. Si se configura un entero positivo, el preboot exige exactamente esa cantidad.

`urls.txt` debe contener una URL por línea. El pipeline acepta únicamente URLs de `unab-cl.zoom.us` cuya ruta contenga `/rec/play/`, y rechaza duplicados.

## Modelo de Whisper y caché

El modelo predeterminado es `large-v3`. Si aún no está disponible, `faster-whisper` puede descargarlo durante la primera transcripción. Esa primera ejecución requiere conexión, varios gigabytes adicionales y puede tardar bastante más.

Se utiliza la caché estándar de Hugging Face/CTranslate2 del usuario. Los pesos no se copian dentro de este proyecto. Cambie `whisper_model` si posteriormente desea usar `medium`, `distil-large-v3` u otro modelo compatible.

## Comprobación previa

Antes de acceder a Zoom, ejecute:

```powershell
python -m scripts.check_dependencies
```

El preboot valida configuración, URLs, Python, `yt-dlp`, `ffmpeg`, `ffprobe`, `faster-whisper`, CTranslate2, carpetas, espacio y disponibilidad preliminar de CUDA.

## Primera prueba: solamente una clase

No ejecute todavía el lote completo. Valide primero una grabación:

```powershell
.\run.ps1 -Limit 1
```

Desde Command Prompt:

```bat
run.bat -Limit 1
```

`Limit` cuenta grabaciones pendientes seleccionadas, no elementos ya completos. Una ejecución limitada a uno nunca avanza a una segunda URL después de un fallo.

Revise el TXT, SRT y VTT generados antes de ejecutar más clases.

## Ejecución futura del lote

Después de validar manualmente la primera clase:

```powershell
.\run.ps1
```

o:

```bat
run.bat
```

## Modos parciales

Descargar grabaciones pendientes sin transcribir:

```powershell
.\run.ps1 -DownloadOnly
```

```bat
run.bat -DownloadOnly
```

Transcribir únicamente archivos locales pendientes:

```powershell
.\run.ps1 -TranscribeOnly
```

```bat
run.bat -TranscribeOnly
```

Los modos también aceptan límite, por ejemplo:

```powershell
.\run.ps1 -DownloadOnly -Limit 2
```

## Reanudación e idempotencia

Puede volver a ejecutar el mismo comando después de un cierre o fallo:

- los archivos `.part` permanecen bajo `temp/<id>/` para que `yt-dlp` continúe;
- una descarga solo se reutiliza si existe y `ffprobe` la valida;
- una transcripción solo se reutiliza si TXT, SRT y VTT contienen segmentos válidos;
- los estados `downloading` y `transcribing` encontrados al iniciar se recuperan como `interrupted`;
- una clase fallida no impide procesar las demás seleccionadas;
- el video descargado nunca se elimina automáticamente.

El flujo automático conserva el video, genera además un MP3 permanente en `audio/` y usa un FLAC temporal mono a 16 kHz para Whisper. El FLAC solo se elimina después de validar correctamente las tres transcripciones.

## CUDA

El pipeline no utiliza PyTorch. La comprobación preliminar usa NVIDIA/CTranslate2, pero la verificación definitiva consiste en inicializar y ejecutar realmente `WhisperModel` con CUDA/float16.

Si la inicialización o la inferencia falla, el log conserva el motivo y toda la transcripción se repite con CPU/int8. La metadata registra el dispositivo y tipo de cálculo que produjeron el resultado final.

CPU con `large-v3` puede ser considerablemente más lenta. Puede cambiar el modelo en `config.json` sin modificar scripts.

## Opera y cookies

Si aparece un error indicando que la base de cookies está bloqueada, el modo interactivo permite reintentar la misma clase. Antes de pulsar ENTER:

1. cierre completamente Chrome/Opera;
2. compruebe en el Administrador de tareas que no queden procesos del navegador;
3. ejecute nuevamente el mismo comando.

El pipeline no copia ni modifica el perfil de Opera. Si Zoom rechaza el acceso normal de la sesión o la grabación tiene restricciones incompatibles, registra el fallo y se detiene para esa clase.

## Resultados

La opción 3 del menú ejecuta el flujo completo: descarga, MP3, transcripción y publicación de una copia TXT ordenada para Drive.

```text
downloads/                  videos descargados
audio/                      MP3 permanentes
final_transcripts/          TXT finales numerados + 00_INDICE.txt
transcripts/txt/            texto con timestamps
transcripts/srt/            subtítulos SRT
transcripts/vtt/            subtítulos WebVTT
metadata/records/<id>.json  estado canónico por URL
metadata/index.json         índice global JSON
metadata/index.csv          índice global CSV
metadata/download-archive.txt historial de yt-dlp
artifacts/<id>/             espacio reservado para chat, enlaces u otros artefactos
logs/                       logs técnicos persistentes
temp/<id>/                  descargas parciales y FLAC recuperable
```

Cada ID usa exactamente los primeros 20 caracteres hexadecimales de `SHA-256(URL)`. Los nombres finales siempre incluyen ese ID, por lo que títulos repetidos no se sobrescriben.

Las rutas guardadas en metadata son relativas a esta carpeta. El proyecto puede moverse completo sin romperlas.

## Diagnóstico rápido

Mostrar ayuda:

```powershell
.\run.ps1 --help
```

Actualizar `yt-dlp` dentro del entorno virtual si el preboot informa una versión antigua:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade yt-dlp
```

Consulte el archivo más reciente de `logs/` y el registro correspondiente en `metadata/records/`. Los logs muestran IDs y comandos sanitizados; no contienen valores de cookies.
