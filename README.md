# MediaTranscribe

MediaTranscribe es una aplicación local para **descargar y transcribir contenido al que el usuario ya tiene acceso**. - **Zoom**: grabaciones públicas o institucionales, con reutilización de sesiones locales autorizadas cuando la grabación requiere autenticación.
- **YouTube**: videos públicos y, cuando sea necesario, acceso mediante una sesión local autorizada.

Después de descargar, ambos proveedores usan el mismo pipeline:

```text
URL
 ↓
video
 ↓
MP3 permanente
 ↓
FLAC temporal 16 kHz mono
 ↓
faster-whisper
 ↓
TXT + SRT + VTT
```

## Menú

El programa presenta tres áreas principales:

```text
========================================
            MEDIATRANSCRIBE
========================================
1) Zoom
2) YouTube
3) Limpiar / administrar archivos
4) Salir
```

### Zoom

Pega una URL de grabación:

```text
https://institucion.zoom.us/rec/play/...
```

El programa:

1. valida la URL;
2. comprueba si es accesible sin autenticación;
3. si requiere acceso institucional, busca una sesión válida en los navegadores configurados;
4. prepara una copia local de cookies para no consultar la base del navegador repetidamente;
5. descarga el video;
6. genera MP3;
7. transcribe;
8. guarda el trabajo completo.

MediaTranscribe **no inicia sesión por el usuario ni evade controles de acceso**. Solo reutiliza sesiones locales que ya tengan permiso para acceder a la grabación.

Las cookies exportadas se almacenan únicamente en:

```text
private/zoom.cookies.txt
```

`private/` está excluido de Git.

### YouTube

Pega una URL de YouTube o YouTube Music.

Para videos públicos se intenta trabajar sin cookies. Si el recurso requiere una sesión y el usuario ya dispone de acceso en un navegador compatible, se utiliza el mismo mecanismo local de sesión.

La descarga limita el video a un máximo configurable, por defecto **720p**, porque la prioridad del proyecto es obtener audio y transcripción de forma eficiente.

## Ejecución

### Desde CMD

Desde la raíz del proyecto:

```bat
python -m app.main
```

Si existe `.venv`, también puedes ejecutar:

```bat
.venv\Scripts\python.exe -m app.main
```

### PowerShell

El lanzador PowerShell está ordenado dentro de `tools/`:

```powershell
powershell -ExecutionPolicy Bypass -File tools\run.ps1
```

Ya no existe `run.bat`. El menú pertenece a la aplicación Python, no al lanzador.

## Estructura

```text
app/
├── main.py
├── core/
│   ├── audio.py
│   ├── browser.py
│   ├── cleanup.py
│   ├── jobs.py
│   ├── pipeline.py
│   └── transcribe.py
└── providers/
    ├── base.py
    ├── ytdlp.py
    ├── zoom.py
    └── youtube.py

tools/
└── run.ps1

output/
data/
temp/
logs/
private/
```

Los directorios de salida se crean automáticamente y no se versionan.

## Trabajos numerados

Cada nueva URL obtiene un número consecutivo:

```text
output/
├── 001_zoom_clase-de-redes/
├── 002_youtube_python-networking/
└── 003_zoom_reunion-proyecto/
```

Cada trabajo contiene:

```text
video.mp4
audio.mp3
transcript.txt
transcript.srt
transcript.vtt
metadata.json
```

El archivo global:

```text
data/jobs.json
```

mantiene la numeración y permite detectar URLs ya procesadas o reanudar trabajos incompletos.

Si una URL ya fue completada, MediaTranscribe no vuelve a descargarla automáticamente. Si quedó interrumpida o fallida, la siguiente ejecución reanuda el mismo trabajo.

## Limpieza

El menú de limpieza ofrece:

```text
1) Eliminar solo videos descargados
2) Limpiar todos los trabajos generados
3) Volver
```

**Eliminar solo videos** conserva MP3, transcripciones y metadata.

**Limpiar todos los trabajos** elimina `output/`, `temp/`, `logs/` y el historial de trabajos, y reinicia la numeración en 001. No elimina `config.json`, el entorno virtual ni `private/`.

## Transcripción

Configuración predeterminada:

- modelo: `medium`;
- idioma: detección automática;
- GPU: preferida;
- CUDA: `int8_float16`;
- batch inicial: 4;
- fallback: CPU `int8`.

El programa conserva el MP3 y utiliza FLAC mono 16 kHz únicamente como entrada temporal de Whisper.

## Configuración

`config.json`:

```json
{
  "whisper_model": "medium",
  "language": "auto",
  "browser": "chrome",
  "browser_priority": ["chrome", "edge", "opera", "firefox"],
  "prefer_gpu": true,
  "delete_temporary_audio": true,
  "max_video_height": 720,
  "batch_size": 4
}
```

Para forzar español:

```json
"language": "es"
```

## Requisitos

- Windows 10/11
- Python 3.10+
- `yt-dlp`
- `ffmpeg`
- `ffprobe`
- `faster-whisper`
- CTranslate2
- opcionalmente una GPU NVIDIA compatible

Instala las dependencias Python con:

```bat
python -m pip install -r requirements.txt
```

FFmpeg/ffprobe deben estar disponibles en `PATH`.

## Privacidad

MediaTranscribe no debe almacenar contraseñas.

Los datos sensibles de sesión quedan bajo `private/`, que está ignorado por Git. Los videos, MP3, transcripciones, logs y metadata de trabajos también quedan fuera del repositorio.
