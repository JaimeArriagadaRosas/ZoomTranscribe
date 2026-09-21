# Pipeline local de grabaciones Zoom de CIBERSEGURIDAD

## Propósito

El proyecto proporcionará un único flujo local para Windows 10/11 capaz de leer las grabaciones indicadas en `urls.txt`, descargar únicamente las pendientes mediante la sesión autorizada de Opera, transcribirlas con `faster-whisper` y producir TXT, SRT y VTT. El flujo será reanudable, idempotente y tolerante a fallos por grabación.

La validación inicial se limitará a una grabación mediante `run.ps1 -Limit 1`. Durante la implementación no se ejecutará el lote completo ni se procesarán automáticamente las otras diez URLs iniciales.

El pipeline solo utilizará mecanismos normales de acceso disponibles para la sesión autorizada del usuario. No copiará bases de cookies, no almacenará credenciales y no intentará evadir DRM ni restricciones de Zoom.

El proyecto será exclusivamente local y utilitario. No se inicializará Git ni se crearán repositorios, commits, ramas, automatizaciones de CI/CD o integraciones con GitHub. La implementación priorizará un pipeline directo y funcional; no añadirá capas de infraestructura que no contribuyan a la descarga, transcripción, reanudación o diagnóstico.

## Alcance inicial y evolución de `urls.txt`

El archivo inicial contiene once URLs únicas de `unab-cl.zoom.us` con una ruta que incluye `/rec/play/`. El preboot comprobará que exista al menos una URL válida, mostrará el total encontrado y rechazará duplicados, hosts ajenos o rutas incompatibles.

La cantidad once no será una restricción dura. `config.json` podrá incluir `expected_url_count`, cuyo valor predeterminado será `null`. Cuando tenga un entero positivo, una diferencia se tratará como un error de validación configurable; con `null`, se admitirá cualquier cantidad positiva de URLs válidas para permitir agregar clases posteriormente.

No se buscarán ni incorporarán URLs adicionales.

## Arquitectura

`run.ps1` y `run.bat` serán lanzadores mínimos. Ambos reenviarán los argumentos al orquestador `scripts/pipeline.py` y devolverán su código de salida. Entre las interfaces admitidas estarán:

```powershell
.\run.ps1 -Limit 1
.\run.ps1 -DownloadOnly
.\run.ps1 -TranscribeOnly
```

```bat
run.bat -Limit 1
run.bat -DownloadOnly
run.bat -TranscribeOnly
```

PowerShell traducirá los parámetros nombrados a la interfaz de Python. Batch reenviará todos sus argumentos mediante `%*` y conservará el código devuelto por Python.

El orquestador Python coordinará módulos independientes:

- `check_dependencies.py`: configuración, URLs, programas, importaciones, directorios, espacio y diagnóstico preliminar de GPU.
- `download.py`: ejecución controlada de `yt-dlp`, clasificación de errores y validación multimedia.
- `transcribe.py`: inicialización de Whisper, fallback de dispositivo, generación y validación de salidas.
- `metadata.py`: registros individuales, estados e índices derivados.
- `utils.py`: rutas, nombres seguros, IDs, tiempos, subprocess y escrituras atómicas.
- `pipeline.py`: selección de trabajo, modos de ejecución, aislamiento de fallos, progreso y resumen.

Las rutas persistidas serán siempre relativas a la raíz del proyecto. Las rutas absolutas solo existirán en memoria mientras se ejecuta el programa.

## Estructura

```text
config.json
requirements.txt
README.md
run.ps1
run.bat
scripts/
  check_dependencies.py
  download.py
  metadata.py
  pipeline.py
  transcribe.py
  utils.py
tests/
  test_dependencies.py
  test_download.py
  test_metadata.py
  test_pipeline.py
  test_transcribe.py
  test_utils.py
downloads/
transcripts/
  txt/
  srt/
  vtt/
metadata/
  records/
  download-archive.txt
  index.json
  index.csv
artifacts/
  <recording-id>/
logs/
temp/
  <recording-id>/
```

`artifacts/<recording-id>/` quedará disponible para extensiones futuras como `chat.txt` o `links.txt`. La primera implementación no extraerá esos artefactos.

## Configuración

`config.json` incluirá, como mínimo:

```json
{
  "whisper_model": "large-v3",
  "language": "es",
  "browser": "opera",
  "prefer_gpu": true,
  "keep_video": true,
  "delete_temporary_audio": true,
  "expected_url_count": null,
  "minimum_free_space_gb": 10,
  "minimum_yt_dlp_version": "2025.01.26",
  "max_video_height": 720
}
```

La implementación validará tipos y rangos antes de iniciar trabajo. El umbral de espacio contemplará descargas parciales, el video final, el FLAC temporal y la posible descarga inicial del modelo. Se podrá aumentar desde la configuración.

## Identidad, nombres y rutas

Cada URL se normalizará eliminando únicamente espacios exteriores. El ID estable será exactamente:

```text
SHA-256(URL normalizada)[0:20]
```

Es decir, los primeros veinte caracteres hexadecimales en minúsculas. Dos URLs distintas nunca dependerán del título de Zoom para compartir identidad.

Los archivos finales seguirán un patrón equivalente a:

```text
YYYY-MM-DD_CIBERSEGURIDAD_<titulo-seguro>_<recording-id>.<ext>
```

Si no existe fecha o título, se usarán valores neutros. El ID será obligatorio en todos los nombres descargados y transcritos, evitando sobrescrituras incluso cuando varias clases tengan exactamente el mismo título.

Las rutas en registros e índices usarán separadores relativos y portables. Al abrir un archivo, se resolverán contra la raíz detectada del proyecto, por lo que la carpeta completa podrá moverse o sincronizarse sin invalidar referencias.

## Estado y metadata por grabación

Cada URL tendrá un registro canónico en `metadata/records/<recording-id>.json`. Incluirá al menos:

- URL original y recording ID.
- título, fecha y duración;
- estado actual, marcas de tiempo e intentos;
- ruta relativa del archivo descargado y de la metadata de `yt-dlp`;
- rutas relativas de TXT, SRT y VTT;
- idioma detectado, probabilidad disponible y cantidad de segmentos;
- configuración efectiva de Whisper: modelo, idioma solicitado, `device` y `compute_type`;
- indicador y motivo del fallback CUDA a CPU, cuando ocurra;
- último error clasificado y un historial acotado de intentos.

Los estados serán:

```text
pending -> downloading -> downloaded -> transcribing -> completed
```

También existirán `download_failed`, `transcription_failed` e `interrupted`. Los estados transitorios encontrados al reiniciar se convertirán en trabajo recuperable.

Los archivos se escribirán primero junto a su destino con un nombre temporal y después se reemplazarán atómicamente. Un archivo incompleto no podrá convertirse accidentalmente en estado terminado.

## Índices globales

`metadata/index.json` y `metadata/index.csv` se reconstruirán atómicamente desde los registros individuales después de cada transición persistida. No serán la fuente canónica y podrán regenerarse después de una interrupción.

Cada fila o elemento relacionará:

- URL;
- recording ID;
- título;
- fecha;
- duración;
- archivo descargado;
- TXT, SRT y VTT;
- estado;
- error actual;
- configuración efectiva de Whisper.

El CSV serializará las rutas de transcripción en columnas separadas y usará UTF-8 compatible con herramientas habituales de Windows.

## Descarga

Cada descarga usará un directorio estable `temp/<recording-id>/`. Esto conserva archivos `.part` entre ejecuciones y permite a `yt-dlp` continuar transferencias.

La invocación incluirá:

- `--cookies-from-browser opera` o el navegador configurado;
- continuación de archivos parciales;
- nombres compatibles con Windows;
- `metadata/download-archive.txt`;
- metadata JSON de `yt-dlp`;
- una plantilla temporal con identificadores de Zoom y el recording ID propio;
- salida suficientemente estructurada para conocer el archivo final.

La selección predeterminada conservará video con una altura máxima de 720p y el mejor audio compatible, con fallback a un formato combinado estable. `ffmpeg` combinará video y audio cuando sea necesario. La configuración quedará preparada para un modo de solo audio futuro, pero el valor predeterminado conservará el video.

Tras la descarga, el pipeline leerá la metadata, construirá el nombre final y moverá el archivo a `downloads/` sin sobrescribir otro destino. La descarga será válida únicamente si el archivo existe, tiene contenido y `ffprobe` puede reconocerlo y obtener información multimedia razonable.

`download-archive.txt` será una defensa adicional, no la fuente de verdad. Si indica una descarga previa pero el registro propio no tiene un archivo local válido, el pipeline repetirá de forma controlada esa URL sin aplicar el archivo de historial en ese intento de recuperación.

Si Opera bloquea el acceso a su base de cookies, el error se clasificará, se indicará cerrar Opera y no se copiará ni modificará el perfil.

## Versión de `yt-dlp`

El preboot ejecutará `yt-dlp --version`, validará que la versión pueda interpretarse y la comparará con `minimum_yt_dlp_version`. Si está por debajo del mínimo, detendrá la ejecución con instrucciones para actualizar. No modificará silenciosamente el entorno ni ejecutará una actualización automática.

## Transcripción

El modelo predeterminado será `large-v3`, idioma solicitado `es` y segmentación natural de Whisper. El modelo será configurable sin cambios de código.

`faster-whisper` utiliza la caché estándar de Hugging Face/CTranslate2. El proyecto no copiará ni almacenará deliberadamente los pesos dentro de la carpeta del proyecto. Si `large-v3` no está en caché, la primera transcripción podrá descargarlo y requerirá conexión, varios gigabytes libres y tiempo adicional. El README lo advertirá expresamente.

El archivo descargado se entregará primero directamente a `faster-whisper`. Si no puede decodificarlo, `ffmpeg` generará `temp/<recording-id>/<recording-id>.flac` con audio mono a 16 kHz y sin pérdida. El FLAC se conservará ante cualquier fallo y solo se eliminará, si la configuración lo permite, después de validar TXT, SRT y VTT.

Los segmentos se materializarán una vez. De esa misma colección se generarán:

- TXT con `[HH:MM:SS - HH:MM:SS] Texto`;
- SRT con numeración y tiempos `HH:MM:SS,mmm`;
- VTT con cabecera `WEBVTT` y tiempos `HH:MM:SS.mmm`.

No se generarán marcas por palabra. Los tres archivos se escribirán temporalmente y se publicarán de forma atómica.

## CUDA y fallback

PyTorch no será una dependencia. El preboot podrá consultar `nvidia-smi` y las capacidades expuestas por CTranslate2 para informar si CUDA parece disponible, pero esa consulta será solo diagnóstica.

La prueba definitiva será inicializar y utilizar realmente:

```python
WhisperModel(model_name, device="cuda", compute_type="float16")
```

Si falla durante la inicialización o la inferencia, el pipeline registrará el motivo y realizará un único reintento controlado mediante:

```python
WhisperModel(model_name, device="cpu", compute_type="int8")
```

La metadata guardará la configuración que realmente produjo la transcripción. Si el fallback también falla, la grabación quedará en `transcription_failed` sin afectar las demás.

## Idempotencia y recuperación

Una grabación descargada se reutilizará solo cuando su ruta relativa resuelva a un archivo no vacío validado por `ffprobe`.

Una transcripción se considerará completa únicamente cuando:

- TXT, SRT y VTT existan y no estén vacíos;
- TXT contenga al menos un intervalo válido;
- SRT tenga numeración y tiempos válidos;
- VTT comience con `WEBVTT` y contenga tiempos válidos;
- el registro tenga metadata de transcripción coherente.

La existencia de un archivo vacío nunca bastará. Una salida parcial hará que la transcripción se repita y reemplace el conjunto completo.

Al recibir `Ctrl+C`, se intentará persistir `interrupted` para la grabación activa, se conservarán `.part` y FLAC útiles para reanudación, y se devolverá un código de salida no exitoso.

## Modos y selección

Sin opciones, el pipeline descargará y transcribirá todas las grabaciones pendientes seleccionadas.

`-DownloadOnly` ejecutará únicamente descargas pendientes. `-TranscribeOnly` transcribirá únicamente grabaciones que ya tengan un archivo local válido. Las opciones serán mutuamente excluyentes.

`-Limit N` limitará el número de grabaciones pendientes seleccionadas en esa invocación según el modo activo. No contará elementos ya completos ni permitirá que una ejecución iniciada con `-Limit 1` avance a una segunda URL después de un fallo.

La selección respetará el orden de `urls.txt`.

## Preboot

Antes de seleccionar trabajo se verificará:

1. versión compatible de Python;
2. existencia y esquema válido de `config.json`;
3. al menos una URL válida en `urls.txt`;
4. ausencia de duplicados y pertenencia de todas las URLs a `unab-cl.zoom.us/rec/play/`;
5. `expected_url_count` solamente cuando no sea `null`;
6. ejecución y versión mínima de `yt-dlp`;
7. ejecución de `ffmpeg`;
8. ejecución independiente de `ffprobe`;
9. importación de `faster_whisper` y CTranslate2;
10. creación y escritura de carpetas requeridas;
11. espacio libre superior a `minimum_free_space_gb`;
12. diagnóstico preliminar de NVIDIA/CUDA si se prefiere GPU;
13. coherencia o regeneración de índices desde los registros.

Un fallo indispensable detendrá el pipeline antes de acceder a Zoom. El diagnóstico preliminar negativo de CUDA no será fatal porque CPU/int8 es un camino válido.

## Errores, logs y consola

Cada ejecución creará `logs/pipeline_YYYYMMDD_HHMMSS.log`. Incluirá fecha, recording ID, fase, comandos sanitizados, estados, fallos de programas, cookies, `ffmpeg`, `ffprobe`, Whisper y fallback de CUDA.

Los comandos se registrarán sin valores de cookies ni contenido interno del perfil. La URL completa se conservará en el registro individual y los índices porque forma parte explícita de la relación solicitada; los mensajes de consola y comandos del log podrán sustituirla por `<URL:recording-id>` para reducir exposición innecesaria.

La consola mostrará progreso por grabación y un resumen final con URLs encontradas, ya completadas, descargadas ahora, transcritas ahora, fallidas, pendientes y tiempo total. Los detalles extensos permanecerán en el log.

Un error de una grabación se capturará en su registro y permitirá continuar con la siguiente, salvo que la ejecución esté limitada a esa única grabación. Errores globales de configuración o dependencias terminarán antes del procesamiento.

## Pruebas automatizadas

El código Python se desarrollará mediante ciclos de prueba fallida e implementación mínima. Las pruebas unitarias se limitarán a las garantías importantes y no accederán a Zoom, Opera ni la red. Usarán directorios temporales y dependencias inyectables sin incorporar frameworks o servicios externos innecesarios.

Se cubrirán como mínimo:

- normalización, validación e ID SHA-256 de veinte caracteres;
- cantidad opcional de URLs y rechazo de duplicados;
- nombres Windows seguros y no colisionables;
- persistencia exclusiva de rutas relativas;
- escrituras atómicas y regeneración de JSON/CSV;
- transiciones, reanudación e interrupción;
- recuperación cuando archivo e historial discrepan;
- construcción de comandos `yt-dlp` y clasificación del bloqueo de Opera;
- validación con `ffprobe`;
- formato y validación de TXT, SRT y VTT;
- fallback de archivo directo a FLAC mono/16 kHz;
- fallback CUDA/float16 a CPU/int8 durante inicialización e inferencia;
- propagación de opciones y códigos de salida en PowerShell y Batch;
- semántica de `Limit`, `DownloadOnly` y `TranscribeOnly`.

La revisión segura incluirá la suite de `unittest`, `compileall`, el preboot real y comprobaciones no destructivas de los lanzadores.

## Validación real limitada

Después de completar y revisar las pruebas seguras, se ejecutará exclusivamente:

```powershell
.\run.ps1 -Limit 1
```

La prueba seleccionará la primera URL pendiente. Podrá usar la sesión de Opera, descargar el archivo, descargar `large-v3` a su caché estándar si es necesario, intentar CUDA y aplicar los fallbacks definidos.

Si falla, se conservarán evidencias reales, se diagnosticará y solo se podrá reintentar esa misma grabación. No se crearán archivos falsos ni mocks para declarar éxito.

La prueba será exitosa únicamente después de validar la cadena completa Zoom → Opera → `yt-dlp` → archivo local → `faster-whisper` → TXT/SRT/VTT. Incluso entonces, la implementación se detendrá e informará el resultado; no ejecutará las diez grabaciones restantes ni invocará el pipeline sin `-Limit`.

## Documentación para el usuario

El README explicará propósito, requisitos, instalación de Python y `ffmpeg`/`ffprobe`, entorno virtual, configuración, caché y primera descarga del modelo, prueba de una clase, ejecución completa futura, reanudación, modos parciales, resultados y solución de problemas de Opera y CUDA.

No se crearán archivos ni configuraciones de Git. El README advertirá que descargas, logs, temporales, posibles cookies exportadas y cachés o pesos de modelos no deben compartirse accidentalmente, aunque la implementación no exportará cookies ni almacenará los pesos dentro del proyecto.
