# Zoom Cybersecurity Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This project is executed natively without subagents, Git, commits, CI/CD, or GitHub integrations.

**Goal:** Build a local Windows pipeline that downloads authorized Zoom recordings from `urls.txt`, transcribes pending recordings with `faster-whisper`, persists portable metadata, and safely resumes interrupted work.

**Architecture:** A small Python package owns validation, metadata, download, transcription, and orchestration. PowerShell and Batch wrappers run `python -m scripts.pipeline`; all durable paths are relative to the project root, and each recording is isolated by a 20-character SHA-256 URL ID.

**Tech Stack:** Python 3.10+, standard-library `unittest`, `yt-dlp`, `ffmpeg`, `ffprobe`, `faster-whisper`, CTranslate2, PowerShell, Windows Batch.

**Spec:** `docs/superpowers/specs/2026-09-21-zoom-cybersecurity-pipeline-design.md`

## Global Constraints

- Windows 10/11 is the primary platform; do not embed machine-specific absolute paths.
- Use exactly the first 20 lowercase hexadecimal characters of `SHA-256(trimmed URL)` as the recording ID.
- Process only URLs supplied in `urls.txt`; accept one or more valid unique URLs from `unab-cl.zoom.us` whose path contains `/rec/play/`.
- `expected_url_count` defaults to `null`; enforce it only when configured as a positive integer.
- Use `--cookies-from-browser opera` by default and never copy, inspect, or persist browser cookie databases.
- Preserve video by default, cap video selection at 720p, and never delete a successfully downloaded source file.
- Store all durable file references relative to the project root.
- Keep per-record JSON state plus `metadata/index.json`, `metadata/index.csv`, and `metadata/download-archive.txt`.
- Use `large-v3`, Spanish, CUDA/float16 when it really works, and CPU/int8 after a controlled CUDA initialization or inference failure.
- Do not introduce PyTorch.
- If direct decoding fails, create mono 16 kHz FLAC and delete it only after TXT, SRT, and VTT validate.
- Use the standard model cache outside the project; never copy model weights into this folder.
- During implementation, the only real recording command permitted is `.\run.ps1 -Limit 1`; never run an unlimited batch.
- Do not initialize or use Git and do not add CI/CD or publishing configuration.

## Review Focus

- A URL containing uppercase host text or harmless surrounding whitespace must normalize correctly without changing the URL content used for hashing; Task 1 pins this behavior.
- A title containing Windows reserved names, punctuation, accents, or only invalid characters must still produce a safe, nonempty filename containing the recording ID; Task 1 pins this behavior.
- A stale archive entry with no valid local media must trigger one archive-free recovery attempt for only that URL; Task 3 pins this behavior.
- CUDA may initialize successfully but fail while consuming the segment generator; Task 4 pins the CPU retry after an inference-time failure.
- An interrupted index rebuild must not corrupt the last valid index and must be repairable from per-record JSON; Task 2 pins atomic replacement and regeneration.

---

### Task 1: Core utilities, configuration, URL validation, and preboot

**Files:**
- Create: `config.json`
- Create: `requirements.txt`
- Create: `scripts/__init__.py`
- Create: `scripts/utils.py`
- Create: `scripts/check_dependencies.py`
- Create: `tests/__init__.py`
- Create: `tests/test_utils.py`
- Create: `tests/test_dependencies.py`

**Interfaces:**
- Produces: `recording_id(url: str) -> str`, `safe_component(text: str, fallback: str) -> str`, `to_relative(root: Path, path: Path) -> str`, `resolve_relative(root: Path, value: str) -> Path`, `atomic_write_text(path: Path, text: str) -> None`, and `atomic_write_json(path: Path, value: object) -> None`.
- Produces: `load_config(root: Path) -> dict`, `load_and_validate_urls(root: Path, config: dict) -> list[str]`, `ensure_directories(root: Path) -> None`, and `run_preboot(root: Path, config: dict, urls: list[str], runner=subprocess.run) -> PrebootReport`.
- `PrebootReport` contains `ok: bool`, `errors: list[str]`, `warnings: list[str]`, `url_count: int`, and `cuda_candidate: bool`.

- [ ] **Step 1: Write failing utility tests**

```python
class UtilityTests(unittest.TestCase):
    def test_recording_id_is_first_twenty_sha256_hex_characters(self):
        url = "https://unab-cl.zoom.us/rec/play/example"
        expected = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        self.assertEqual(recording_id(f"  {url}  "), expected)

    def test_uppercase_host_is_valid_but_hash_preserves_original_url_text(self):
        url = "https://UNAB-CL.ZOOM.US/rec/play/example"
        expected = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        self.assertEqual(recording_id(url), expected)

    def test_safe_component_handles_windows_reserved_and_unicode_titles(self):
        value = safe_component('CON: clase de redes / ping áéí', 'clase')
        self.assertTrue(value)
        self.assertNotIn(':', value)
        self.assertNotEqual(value.upper(), 'CON')

    def test_relative_path_rejects_files_outside_project(self):
        with self.assertRaises(ValueError):
            to_relative(Path('D:/project'), Path('C:/outside/file.mp4'))
```

- [ ] **Step 2: Run utility tests and confirm the missing-module failure**

Run: `python -m unittest tests.test_utils -v`

Expected: failure because `scripts.utils` does not exist.

- [ ] **Step 3: Implement the minimal utility surface**

```python
def recording_id(url: str) -> str:
    normalized = url.strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]

def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text, encoding=encoding)
    os.replace(temporary, path)
```

Implement Windows name cleanup, project-bound relative-path conversion, JSON atomic writes, clock formatting, and command redaction in the same focused module.

- [ ] **Step 4: Run utility tests and confirm they pass**

Run: `python -m unittest tests.test_utils -v`

Expected: all utility tests pass.

- [ ] **Step 5: Write failing configuration and URL tests**

```python
class DependencyValidationTests(unittest.TestCase):
    def test_accepts_any_positive_count_when_expected_count_is_null(self):
        self.write_config(expected_url_count=None)
        self.write_urls([self.zoom_url('a'), self.zoom_url('b')])
        self.assertEqual(len(load_and_validate_urls(self.root, load_config(self.root))), 2)

    def test_rejects_duplicate_url_after_trimming(self):
        url = self.zoom_url('same')
        self.write_urls([url, f'  {url}  '])
        with self.assertRaisesRegex(ValueError, 'duplicad'):
            load_and_validate_urls(self.root, load_config(self.root))

    def test_rejects_wrong_host_or_path(self):
        self.write_urls(['https://example.com/rec/play/a'])
        with self.assertRaisesRegex(ValueError, 'unab-cl.zoom.us'):
            load_and_validate_urls(self.root, load_config(self.root))

    def test_accepts_zoom_host_case_insensitively(self):
        url = 'https://UNAB-CL.ZOOM.US/rec/play/a'
        self.write_urls([url])
        self.assertEqual(load_and_validate_urls(self.root, load_config(self.root)), [url])
```

- [ ] **Step 6: Run dependency tests and confirm the missing-interface failure**

Run: `python -m unittest tests.test_dependencies -v`

Expected: failure because `scripts.check_dependencies` is absent.

- [ ] **Step 7: Implement configuration and preboot checks**

Use a `PrebootReport` dataclass and small helpers for executable probes. The preboot must run `yt-dlp --version`, `ffmpeg -version`, `ffprobe -version`, import `faster_whisper` and `ctranslate2`, create required directories, call `shutil.disk_usage`, and perform optional `nvidia-smi`/CTranslate2 diagnostics without loading the Whisper model.

The checked-in configuration is:

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

The dependency file is:

```text
yt-dlp>=2025.1.26
faster-whisper>=1.1.0
```

- [ ] **Step 8: Run Task 1 tests and compile the modules**

Run: `python -m unittest tests.test_utils tests.test_dependencies -v`

Run: `python -m compileall -q scripts tests`

Expected: both commands exit with code 0.

### Task 2: Per-record metadata, state transitions, and global indexes

**Files:**
- Create: `scripts/metadata.py`
- Create: `tests/test_metadata.py`

**Interfaces:**
- Consumes: atomic and relative-path helpers from Task 1.
- Produces: `RecordingStore(root: Path)` with `ensure(urls: list[str]) -> list[dict]`, `load(recording_id: str) -> dict`, `list_records() -> list[dict]`, `save(record: dict) -> dict`, `transition(recording_id: str, state: str, **changes) -> dict`, `recover_interrupted() -> None`, and `rebuild_indexes() -> None`.
- Record schema includes `schema_version`, `id`, `url`, `title`, `date`, `duration`, `state`, `attempts`, `download`, `transcripts`, `whisper`, `error`, and `history`.

- [ ] **Step 1: Write failing store and index tests**

```python
class RecordingStoreTests(unittest.TestCase):
    def test_creates_one_record_and_artifact_directory_per_url(self):
        records = self.store.ensure([self.url])
        rid = recording_id(self.url)
        self.assertEqual(records[0]['id'], rid)
        self.assertTrue((self.root / 'metadata' / 'records' / f'{rid}.json').is_file())
        self.assertTrue((self.root / 'artifacts' / rid).is_dir())

    def test_indexes_contain_only_relative_paths_and_required_columns(self):
        record = self.completed_record()
        self.store.save(record)
        self.store.rebuild_indexes()
        index = json.loads((self.root / 'metadata' / 'index.json').read_text('utf-8'))
        self.assertEqual(index[0]['download_file'], record['download']['file'])
        self.assertFalse(Path(index[0]['download_file']).is_absolute())

    def test_rebuild_keeps_previous_index_if_atomic_replace_fails(self):
        old = '[{"state":"old"}]\n'
        (self.root / 'metadata' / 'index.json').write_text(old, encoding='utf-8')
        with mock.patch('scripts.metadata.os.replace', side_effect=OSError('busy')):
            with self.assertRaises(OSError):
                self.store.rebuild_indexes()
        self.assertEqual((self.root / 'metadata' / 'index.json').read_text('utf-8'), old)
```

- [ ] **Step 2: Run metadata tests and confirm the missing-module failure**

Run: `python -m unittest tests.test_metadata -v`

Expected: failure because `scripts.metadata` does not exist.

- [ ] **Step 3: Implement atomic per-record persistence and derived indexes**

Use one JSON object per exact `metadata/records/<20-hex-id>.json`, ignoring `.source.json` files when listing canonical records. Append timestamped state changes to a bounded history, and store errors as `{phase, type, message, at}` without traceback secrets. Write `index.json` and `index.csv` to temporary siblings before replacing their destinations; encode CSV as UTF-8 with BOM (`utf-8-sig`) for Windows compatibility.

CSV columns must be:

```python
INDEX_COLUMNS = [
    'url', 'id', 'title', 'date', 'duration', 'download_file',
    'txt', 'srt', 'vtt', 'state', 'error', 'whisper_model',
    'whisper_language', 'whisper_device', 'whisper_compute_type'
]
```

Convert `downloading` and `transcribing` records found at startup to `interrupted`; retain their download and temporary paths.

- [ ] **Step 4: Run metadata tests and the complete current suite**

Run: `python -m unittest tests.test_metadata -v`

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass.

### Task 3: Download command, Opera authentication, archive recovery, and media validation

**Files:**
- Create: `scripts/download.py`
- Create: `tests/test_download.py`

**Interfaces:**
- Consumes: `RecordingStore`, safe names, path conversion, config, and an injected command runner.
- Produces: `DownloadResult(ok: bool, relative_file: str | None, info_file: str | None, title: str | None, date: str | None, duration: float | None, error_kind: str | None, error_message: str | None)`.
- Produces: `DownloadError(kind: str, message: str)`, `classify_download_error(output: str) -> DownloadError`, `build_yt_dlp_command(root: Path, record: dict, config: dict, use_archive: bool = True) -> list[str]`, `probe_media(path: Path, runner=subprocess.run) -> dict`, and `download_recording(root: Path, store: RecordingStore, record: dict, config: dict, logger, runner=subprocess.run) -> DownloadResult`.

- [ ] **Step 1: Write failing command and validation tests**

```python
class DownloadTests(unittest.TestCase):
    def test_command_uses_opera_archive_resume_windows_names_and_height_cap(self):
        command = build_yt_dlp_command(self.root, self.record, self.config)
        self.assertIn('--cookies-from-browser', command)
        self.assertIn('opera', command)
        self.assertIn('--download-archive', command)
        self.assertIn('--continue', command)
        self.assertIn('--windows-filenames', command)
        self.assertTrue(any('height<=720' in item for item in command))

    def test_probe_rejects_empty_or_unreadable_media(self):
        media = self.root / 'empty.mp4'
        media.write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'vacío'):
            probe_media(media, runner=self.runner)

    def test_locked_opera_cookie_database_gets_specific_error(self):
        result = classify_download_error('Could not copy Chrome cookie database. The process cannot access the file')
        self.assertEqual(result.kind, 'opera_cookies_locked')
        self.assertIn('cerrar Opera', result.message)
```

- [ ] **Step 2: Run the tests and confirm the missing-module failure**

Run: `python -m unittest tests.test_download -v`

Expected: failure because `scripts.download` does not exist.

- [ ] **Step 3: Implement command construction and `ffprobe` validation**

The format selector must be equivalent to:

```python
height = int(config['max_video_height'])
selector = (
    f"bestvideo[height<={height}]+bestaudio/"
    f"best[height<={height}]/best"
)
```

Use `--merge-output-format mp4`, `--write-info-json`, `--print after_move:filepath`, `--newline`, `--no-progress`, and an output template beneath `temp/<id>/` containing `%(title).120B`, `%(id)s`, and the stable record ID. Log a redacted display command while passing the real URL only to the subprocess.

`probe_media` invokes `ffprobe -v error -show_entries format=duration -of json <file>` and requires a positive file size plus parseable JSON.

- [ ] **Step 4: Write the failing stale-archive recovery test**

```python
def test_missing_local_file_retries_once_without_archive(self):
    runner = ScriptedRunner([
        completed(stdout='[download] archive: already recorded\n'),
        completed(stdout=str(self.downloaded_temp_file) + '\n'),
        ffprobe_success(duration=3600.0),
    ])
    result = download_recording(self.root, self.store, self.record, self.config, self.logger, runner)
    self.assertTrue(result.ok)
    self.assertEqual(runner.ytdlp_calls, 2)
    self.assertIn('--download-archive', runner.commands[0])
    self.assertNotIn('--download-archive', runner.commands[1])
```

- [ ] **Step 5: Run the recovery test and confirm it fails for missing behavior**

Run: `python -m unittest tests.test_download.DownloadTests.test_missing_local_file_retries_once_without_archive -v`

Expected: assertion failure because only the normal archive attempt exists.

- [ ] **Step 6: Implement download lifecycle and one controlled archive-free recovery**

Before downloading, transition to `downloading` and increment the download attempt count. After successful probing, build `YYYY-MM-DD_CIBERSEGURIDAD_<safe-title>_<id>.<ext>`, move without overwrite, move the generated info JSON to `metadata/records/<id>.source.json`, persist both relative paths and normalized metadata, transition to `downloaded`, and rebuild indexes. On failure, transition to `download_failed` and return a failed result so the pipeline can continue.

- [ ] **Step 7: Run download tests and the complete suite**

Run: `python -m unittest tests.test_download -v`

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass.

### Task 4: Whisper transcription, subtitle formats, FLAC fallback, and CUDA fallback

**Files:**
- Create: `scripts/transcribe.py`
- Create: `tests/test_transcribe.py`

**Interfaces:**
- Consumes: valid downloaded media, `RecordingStore`, config, logger, and injectable `model_factory`/runner.
- Produces: `TranscriptResult(ok: bool, txt: str | None, srt: str | None, vtt: str | None, effective_model: str | None, language: str | None, device: str | None, compute_type: str | None, used_flac: bool, error_message: str | None)`.
- Produces: `Segment(start: float, end: float, text: str)`, `format_timestamp(seconds: float, separator: str) -> str`, `render_txt(segments: list[Segment]) -> str`, `render_srt(segments: list[Segment]) -> str`, `render_vtt(segments: list[Segment]) -> str`, `validate_transcript_set(txt: Path, srt: Path, vtt: Path) -> None`, `build_flac_command(source: Path, target: Path) -> list[str]`, and `transcribe_recording(...) -> TranscriptResult`.

- [ ] **Step 1: Write failing rendering and validation tests**

```python
class TranscriptFormatTests(unittest.TestCase):
    def test_renders_unicode_segment_in_all_three_formats(self):
        segments = [Segment(0.0, 8.125, 'Ejecute ping 10.0.0.1 — señor.')]
        self.assertIn('[00:00:00 - 00:00:08]', render_txt(segments))
        self.assertIn('00:00:00,000 --> 00:00:08,125', render_srt(segments))
        self.assertTrue(render_vtt(segments).startswith('WEBVTT\n\n'))

    def test_empty_or_header_only_files_are_incomplete(self):
        self.write_outputs('', '', 'WEBVTT\n')
        with self.assertRaises(ValueError):
            validate_transcript_set(self.txt, self.srt, self.vtt)
```

- [ ] **Step 2: Run format tests and confirm the missing-module failure**

Run: `python -m unittest tests.test_transcribe.TranscriptFormatTests -v`

Expected: failure because `scripts.transcribe` does not exist.

- [ ] **Step 3: Implement renderers, validators, and atomic publication**

Normalize segment text with `.strip()`, skip empty segments, keep Whisper segment boundaries, and require at least one timestamped segment. Generate all three temporary outputs before replacing any final output; if publication fails, leave the record incomplete so the next run repeats transcription.

- [ ] **Step 4: Write failing FLAC and CUDA fallback tests**

```python
class TranscriptionFallbackTests(unittest.TestCase):
    def test_flac_command_is_mono_16khz_lossless(self):
        command = build_flac_command(Path('class.mp4'), Path('class.flac'))
        self.assertEqual(command[command.index('-ac') + 1], '1')
        self.assertEqual(command[command.index('-ar') + 1], '16000')
        self.assertTrue(str(command[-1]).endswith('.flac'))

    def test_cuda_inference_failure_retries_complete_transcription_on_cpu(self):
        factory = FakeModelFactory(cuda_segments_raise=RuntimeError('CUDA driver error'))
        result = transcribe_recording(
            self.root, self.store, self.record, self.config, self.logger,
            model_factory=factory, runner=self.runner,
        )
        self.assertTrue(result.ok)
        self.assertEqual((result.device, result.compute_type), ('cpu', 'int8'))
        self.assertEqual(factory.created, [('large-v3', 'cuda', 'float16'), ('large-v3', 'cpu', 'int8')])
```

- [ ] **Step 5: Run fallback tests and confirm failures for absent behavior**

Run: `python -m unittest tests.test_transcribe.TranscriptionFallbackTests -v`

Expected: failures because FLAC extraction and model fallback are not implemented.

- [ ] **Step 6: Implement direct decode, FLAC retry, and full-device retry**

Attempt CUDA first only when `prefer_gpu` is true. Materialize `list(segments)` inside the CUDA try block so delayed generator failures trigger fallback. On any CUDA initialization or inference exception, log it and restart the entire transcription on CPU/int8.

If direct input decoding fails independently of CUDA, run:

```python
[
    'ffmpeg', '-y', '-v', 'error', '-i', str(source),
    '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'flac', str(flac_path)
]
```

Persist detected language, probability, duration, segment count, requested model/language, actual device/compute type, and fallback reason. Delete FLAC only after validation succeeds and `delete_temporary_audio` is true.

- [ ] **Step 7: Run transcription tests and the complete suite**

Run: `python -m unittest tests.test_transcribe -v`

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass.

### Task 5: Orchestration, progress, summaries, logs, and Windows launchers

**Files:**
- Create: `scripts/pipeline.py`
- Create: `tests/test_pipeline.py`
- Create: `run.ps1`
- Create: `run.bat`

**Interfaces:**
- Consumes: preboot, `RecordingStore`, download and transcription functions.
- Produces: `PipelineDependencies(download, transcribe, probe_media, validate_transcripts)`, `default_dependencies() -> PipelineDependencies`, `build_parser() -> argparse.ArgumentParser`, `select_records(records: list[dict], mode: str, limit: int | None, root: Path) -> list[dict]`, `validate_completed_record(root: Path, record: dict, dependencies: PipelineDependencies) -> None`, `run_pipeline(root: Path, args, dependencies: PipelineDependencies | None = None) -> int`, and `main(argv: list[str] | None = None) -> int`.
- Exit codes: `0` success/no pending work, `1` one or more selected recordings failed, `2` argument/preboot failure, and `130` interruption.

- [ ] **Step 1: Write failing selection and isolation tests**

```python
class PipelineTests(unittest.TestCase):
    def test_limit_one_never_advances_after_first_selected_failure(self):
        dependencies = FakeDependencies(download_results=[failed_download()])
        code = run_pipeline(self.root, args(limit=1), dependencies)
        self.assertEqual(code, 1)
        self.assertEqual(dependencies.downloaded_ids, [self.records[0]['id']])

    def test_download_only_never_calls_transcriber(self):
        dependencies = FakeDependencies(download_results=[successful_download()])
        run_pipeline(self.root, args(download_only=True), dependencies)
        self.assertEqual(dependencies.transcribed_ids, [])

    def test_transcribe_only_selects_only_valid_local_downloads(self):
        selected = select_records(self.records, 'transcribe', None, self.root)
        self.assertEqual([record['id'] for record in selected], [self.downloaded_record['id']])
```

- [ ] **Step 2: Run pipeline tests and confirm the missing-module failure**

Run: `python -m unittest tests.test_pipeline -v`

Expected: failure because `scripts.pipeline` does not exist.

- [ ] **Step 3: Implement parser, selection, processing, logging, and summary**

Accept both Windows-style and conventional aliases:

```python
parser.add_argument('-Limit', '--limit', type=positive_int)
mode = parser.add_mutually_exclusive_group()
mode.add_argument('-DownloadOnly', '--download-only', action='store_true')
mode.add_argument('-TranscribeOnly', '--transcribe-only', action='store_true')
```

Initialize `logs/pipeline_YYYYMMDD_HHMMSS.log`, recover interrupted states, ensure records for all URLs, rebuild indexes, select pending work in URL order, and process each selected record inside its own exception boundary. Print concise `[n/total]` phases and a final Spanish summary with elapsed time and failed IDs/reasons.

- [ ] **Step 4: Write failing launcher contract tests**

```python
def test_batch_forwards_all_arguments_and_exit_code(self):
    text = (self.root / 'run.bat').read_text(encoding='utf-8')
    self.assertIn('%*', text)
    self.assertRegex(text.lower(), r'exit /b')

def test_powershell_forwards_limit_and_modes(self):
    text = (self.root / 'run.ps1').read_text(encoding='utf-8')
    self.assertIn('$args', text)
    self.assertIn('$LASTEXITCODE', text)
```

- [ ] **Step 5: Run launcher tests and confirm they fail while files are absent**

Run: `python -m unittest tests.test_pipeline.PipelineLauncherTests -v`

Expected: failure because `run.ps1` and `run.bat` do not exist.

- [ ] **Step 6: Implement launchers with project-root working directory and exit propagation**

PowerShell intentionally uses its automatic `$args` array without a parameter block so every argument, including future options, is forwarded unchanged. It runs `python -m scripts.pipeline @args`, captures `$LASTEXITCODE`, restores the previous directory, and exits with the captured code.

Batch follows this structure:

```bat
@echo off
setlocal
pushd "%~dp0"
python -m scripts.pipeline %*
set "PIPELINE_EXIT=%ERRORLEVEL%"
popd
exit /b %PIPELINE_EXIT%
```

- [ ] **Step 7: Run pipeline tests and the complete suite**

Run: `python -m unittest tests.test_pipeline -v`

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass.

### Task 6: User documentation and safe local verification

**Files:**
- Create: `README.md`
- Modify: implementation files only if verification exposes a defect, always after first adding a failing regression test.

**Interfaces:**
- Documents installation, configuration, model cache behavior, all launcher modes, output layout, restart behavior, Opera troubleshooting, CUDA fallback, disk requirements, and the one-record validation boundary.

- [ ] **Step 1: Write the README with exact runnable commands**

Include these commands verbatim:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m scripts.check_dependencies
.\run.ps1 -Limit 1
.\run.ps1 -DownloadOnly
.\run.ps1 -TranscribeOnly
```

Also include Batch equivalents, Windows `ffmpeg`/`ffprobe` installation guidance, the possibility that `large-v3` downloads on first use to the standard cache, and an explicit warning not to run the unlimited command until the one-record result has been inspected.

- [ ] **Step 2: Run the complete automated suite**

Run: `python -m unittest discover -s tests -v`

Expected: zero failures and zero errors.

- [ ] **Step 3: Compile all Python sources**

Run: `python -m compileall -q scripts tests`

Expected: exit code 0 and no syntax diagnostics.

- [ ] **Step 4: Run the real preboot only**

Run: `python -m scripts.check_dependencies`

Expected: prints the URL count, dependency versions, disk status, and CUDA diagnostic without accessing Zoom. If it exits nonzero, record the exact missing prerequisite and do not run the recording test until resolved.

- [ ] **Step 5: Check launcher help without selecting recordings**

Run: `.\run.ps1 --help`

Run: `cmd /c run.bat --help`

Expected: both show Python argument help and return the same exit code as the pipeline.

- [ ] **Step 6: Re-read the specification as a completion checklist**

Verify each specification section against an implementation file or a passing test. Record any gap as a failing regression test before changing code.

### Task 7: One-record end-to-end validation and enforced stop

**Files:**
- Modify: implementation and tests only when the real run exposes a reproducible defect.
- Generate at runtime: `downloads/`, `transcripts/`, `metadata/`, `artifacts/`, `logs/`, and `temp/` contents for only the first pending URL.

**Interfaces:**
- Consumes the completed local pipeline and the user's authorized Opera session.
- Produces real evidence for exactly one pending recording; it never invokes the unlimited pipeline.

- [ ] **Step 1: Confirm no unlimited pipeline process is running**

Run: `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'scripts[\\/.]pipeline' } | Select-Object ProcessId,CommandLine`

Expected: no unrelated pipeline process. If one exists, report it rather than starting another.

- [ ] **Step 2: Execute exactly one real pending recording**

Run: `.\run.ps1 -Limit 1`

Expected: at most one stable recording ID is selected. The command may download `large-v3` to its standard cache on first use and may take substantial time.

- [ ] **Step 3: If the run fails, diagnose without advancing to another URL**

Read only the generated log and the single record JSON. Reproduce code defects with a failing unit test before applying a fix, rerun the full safe suite, and retry only `.\run.ps1 -Limit 1`. For locked Opera cookies, stop and report that Opera must be closed; do not manipulate its profile.

- [ ] **Step 4: If the run succeeds, validate the real artifacts independently**

Run: `python -c "from pathlib import Path; from scripts.metadata import RecordingStore; from scripts.pipeline import default_dependencies, validate_completed_record; root=Path.cwd(); store=RecordingStore(root); record=next(r for r in store.list_records() if r['state']=='completed'); validate_completed_record(root, record, default_dependencies()); print(record['id'])"`

Expected: the selected record has a nonempty `ffprobe`-valid download, valid TXT/SRT/VTT, relative paths, completed status, effective Whisper configuration, and synchronized JSON/CSV indexes.

- [ ] **Step 5: Stop after reporting the one-record result**

Do not run `.\run.ps1`, do not increase the limit, and do not process another URL. Report the exact completed or failed ID, output paths, effective Whisper device, log path, elapsed time, and any action required from the user.
