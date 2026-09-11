import json
import mimetypes
import os
import shutil
import tempfile
import time
import zipfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import Form
from fastapi.responses import JSONResponse

import app_v6_optimized_runtime as opt

core = opt.core
cli = opt.cli
stable = opt.stable
playlist = opt.playlist
app = opt.app

VERSION = "6.7-worker"
WORKER_URL = os.getenv("PANDA_YT_WORKER_URL", "").strip().rstrip("/")
WORKER_TOKEN = os.getenv("PANDA_YT_WORKER_TOKEN", "").strip()
WORKER_POLL = max(0.5, min(float(os.getenv("PANDA_YT_WORKER_POLL", "1")), 5.0))


def _worker_enabled():
    return bool(WORKER_URL and WORKER_TOKEN)


def _worker_json(method, path, payload=None, timeout=30):
    if not _worker_enabled():
        raise RuntimeError("Worker YouTube non configuré")
    body = None
    headers = {"Authorization": f"Bearer {WORKER_TOKEN}"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(f"{WORKER_URL}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail")
        except Exception:
            detail = str(exc)
        raise RuntimeError(f"Worker YouTube: {detail or exc}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Worker YouTube inaccessible: {exc}") from exc


def _worker_download(path, destination, timeout=3600):
    req = Request(
        f"{WORKER_URL}{path}",
        headers={"Authorization": f"Bearer {WORKER_TOKEN}"},
        method="GET",
    )
    try:
        with urlopen(req, timeout=timeout) as response, open(destination, "wb") as out:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail")
        except Exception:
            detail = str(exc)
        raise RuntimeError(f"Worker YouTube: {detail or exc}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Worker YouTube inaccessible: {exc}") from exc


def _safe_extract(bundle, destination):
    root = os.path.abspath(destination)
    with zipfile.ZipFile(bundle) as archive:
        for member in archive.infolist():
            target = os.path.abspath(os.path.join(root, member.filename))
            if target != root and not target.startswith(root + os.sep):
                raise RuntimeError("Bundle worker invalide")
        archive.extractall(root)


def _json_error(response):
    if not isinstance(response, JSONResponse):
        return ""
    try:
        return str(json.loads(response.body.decode("utf-8")).get("error") or "")
    except Exception:
        return ""


def _single_info_from_raw(clean, raw):
    qualities = sorted(
        {
            int(fmt["height"])
            for fmt in raw.get("formats", [])
            if fmt.get("height") and fmt.get("vcodec") != "none"
        },
        reverse=True,
    )
    return {
        "platform": "youtube",
        "title": raw.get("title"),
        "uploader": raw.get("uploader") or raw.get("channel") or raw.get("artist"),
        "thumbnail": raw.get("thumbnail"),
        "duration": raw.get("duration"),
        "qualities": qualities,
        "quality_sizes": core.quality_sizes(raw),
        "audio_only": not bool(qualities),
        "blocked": False,
        "chapters": core.extract_chapters(raw),
        "worker_authenticated": True,
        "source_url": clean,
    }


def _playlist_info_from_raw(clean, raw):
    entries = []
    for idx, item in enumerate(raw.get("entries") or []):
        if not item:
            continue
        video_id = item.get("id") or ""
        webpage = item.get("webpage_url") or item.get("url") or ""
        if video_id and not str(webpage).startswith("http"):
            webpage = f"https://www.youtube.com/watch?v={video_id}"
        entries.append(
            {
                "index": idx,
                "playlist_index": idx + 1,
                "id": video_id,
                "title": item.get("title") or f"Vidéo {idx + 1}",
                "duration": item.get("duration"),
                "thumbnail": item.get("thumbnail") or "",
                "url": webpage,
                "uploader": item.get("uploader") or item.get("channel") or "",
            }
        )
    if not entries:
        raise RuntimeError("Le worker n’a trouvé aucune vidéo dans cette playlist")
    pid = playlist._playlist_id(clean) or ""
    return {
        "platform": "youtube-playlist",
        "title": raw.get("title") or ("YouTube Mix" if pid.startswith("RD") else "Playlist YouTube"),
        "uploader": raw.get("uploader") or raw.get("channel") or "YouTube",
        "thumbnail": entries[0].get("thumbnail") or "",
        "duration": sum(float(x.get("duration") or 0) for x in entries) or None,
        "qualities": [2160, 1440, 1080, 720, 480, 360, 240, 144],
        "quality_sizes": {},
        "audio_only": False,
        "blocked": False,
        "chapters": [],
        "is_playlist": True,
        "playlist_id": pid,
        "playlist_radio": pid.startswith("RD"),
        "playlist_limited": len(entries) >= playlist.PLAYLIST_LIMIT,
        "playlist_entries": entries,
        "worker_authenticated": True,
    }


_original_info = opt.cached_media_info
stable._remove_route("/info", "POST")


@app.post("/info")
def worker_media_info(url: str = Form(...)):
    try:
        clean = core.validate_public_url(url)
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": core.friendly_error(exc)})

    result = _original_info(url)
    if isinstance(result, dict):
        return result

    error = _json_error(result)
    if core.detect_platform(clean) != "youtube" or not stable._authish(error) or not _worker_enabled():
        return result

    try:
        is_playlist = playlist._is_playlist_url(clean)
        raw = _worker_json(
            "POST",
            "/info",
            {"url": clean, "playlist_mode": is_playlist},
            timeout=180,
        )
        data = _playlist_info_from_raw(clean, raw) if is_playlist else _single_info_from_raw(clean, raw)
        opt._cache_put((url or "").strip(), data)
        return data
    except Exception as exc:
        message = str(exc)
        if stable._authish(message):
            message = "La session YouTube du worker gbeerus doit être reconnectée dans Chromium."
        return JSONResponse(status_code=400, content={"error": message})


def _remote_bundle(job_id, payload, workdir):
    remote = _worker_json(
        "POST",
        "/jobs",
        {
            "url": payload.get("url"),
            "mode": payload.get("mode"),
            "quality": payload.get("quality"),
            "video_format": payload.get("video_format"),
            "playlist_mode": bool(payload.get("playlist_mode")),
            "selected_playlist": payload.get("selected_playlist") or [],
        },
        timeout=30,
    )
    remote_id = remote.get("job_id")
    if not remote_id:
        raise RuntimeError("Le worker n’a pas créé le job")

    while True:
        if core.is_cancelled(job_id):
            try:
                _worker_json("POST", f"/jobs/{remote_id}/cancel", {}, timeout=10)
            except Exception:
                pass
            raise InterruptedError("Job annulé")

        state = _worker_json("GET", f"/jobs/{remote_id}", timeout=20)
        remote_progress = int(state.get("progress") or 0)
        local_progress = 5 + int(max(0, min(100, remote_progress)) * 0.70)
        core.update_job(
            job_id,
            status="running",
            stage="youtube_worker",
            progress=min(local_progress, 75),
            message=f"Worker gbeerus · {state.get('message') or state.get('stage') or 'YouTube'}",
        )
        if state.get("status") == "ready":
            break
        if state.get("status") == "error":
            raise RuntimeError(state.get("error") or state.get("message") or "Le worker YouTube a échoué")
        if state.get("status") == "cancelled":
            raise InterruptedError("Job worker annulé")
        time.sleep(WORKER_POLL)

    bundle = os.path.join(workdir, "worker-bundle.zip")
    core.update_job(job_id, stage="worker_transfer", progress=76, message="Transfert depuis le worker gbeerus")
    _worker_download(f"/jobs/{remote_id}/download", bundle)
    _safe_extract(bundle, workdir)
    try:
        os.remove(bundle)
    except OSError:
        pass


def _finish_single(job_id, payload, workdir):
    info = cli._load_info(workdir)
    files = core.media_files(workdir)
    if not files:
        raise RuntimeError("Le worker n’a transféré aucun média")
    source = max(files, key=os.path.getsize)
    title = info.get("title") or info.get("id") or os.path.splitext(os.path.basename(source))[0] or "download"
    mode = "audio" if payload.get("mode") == "audio" else "video"
    audio_quality = payload.get("audio_quality") if payload.get("audio_quality") in core.AUDIO_BITRATES else "best"
    video_format = payload.get("video_format") if payload.get("video_format") in core.VIDEO_FORMATS else "mp4"
    audio_format = payload.get("audio_format") if payload.get("audio_format") in core.AUDIO_FORMATS else "original"
    split_tracks = bool(payload.get("split_tracks")) and mode == "audio"

    if mode == "video":
        if os.path.splitext(source)[1].lower().lstrip(".") == video_format:
            final_file = source
            core.update_job(job_id, stage="processing", progress=94, message="Vidéo prête · aucune conversion")
        else:
            core.update_job(job_id, stage="converting", progress=80, message=f"Conversion {video_format.upper()}")
            final_file = core.convert_video(source, video_format, workdir)
        filename = core.pretty_name(title, os.path.splitext(final_file)[1])
    elif split_tracks:
        if not info:
            raise RuntimeError("La tracklist n’a pas été transférée par le worker")
        core.update_job(job_id, stage="splitting", progress=80, message="Découpage des tracks")
        final_file = core.build_tracks_zip(
            job_id,
            source,
            info,
            payload.get("selected_tracks") or [],
            payload.get("track_titles") or {},
            audio_format,
            audio_quality,
            workdir,
        )
        filename = core.pretty_name(f"{title} - tracks", ".zip")
    else:
        if audio_format == "original":
            final_file = source
            core.update_job(job_id, stage="processing", progress=94, message="Audio source prêt")
        else:
            core.update_job(job_id, stage="converting", progress=82, message=f"Conversion {audio_format.upper()}")
            final_file = core.convert_audio(source, audio_format, audio_quality, workdir)
        filename = core.pretty_name(title, os.path.splitext(final_file)[1])

    core.update_job(
        job_id,
        status="ready",
        stage="ready",
        progress=100,
        message="Prêt à télécharger · worker gbeerus",
        result_path=final_file,
        filename=filename,
        media_type=mimetypes.guess_type(final_file)[0] or "application/octet-stream",
        result_size=os.path.getsize(final_file),
        workdir=workdir,
        error=None,
    )


def _finish_playlist(job_id, payload, workdir):
    files = playlist._playlist_files(workdir)
    if not files:
        raise RuntimeError("Le worker n’a transféré aucune vidéo de la playlist")
    wanted = payload.get("video_format") if payload.get("video_format") in core.VIDEO_FORMATS else "mp4"
    final_files = []
    total = len(files)
    for pos, source in enumerate(files, 1):
        if core.is_cancelled(job_id):
            raise InterruptedError("Job annulé")
        current = os.path.splitext(source)[1].lower().lstrip(".")
        if current == wanted:
            final = source
        else:
            core.update_job(job_id, stage="converting", progress=80 + int((pos / total) * 10), message=f"Conversion {pos}/{total} · {wanted.upper()}")
            final = opt.convert_playlist_video(source, wanted, workdir, pos)
        final_files.append(final)

    keep = {os.path.abspath(x) for x in final_files}
    for source in files:
        if os.path.abspath(source) not in keep:
            try:
                os.remove(source)
            except OSError:
                pass

    core.update_job(job_id, stage="packaging", progress=92, message=f"Création du ZIP · {len(final_files)} vidéos")
    zip_path = os.path.join(workdir, "playlist-videos.zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for pos, path in enumerate(final_files, 1):
            archive.write(path, arcname=os.path.basename(path))
            try:
                os.remove(path)
            except OSError:
                pass
            core.update_job(job_id, stage="packaging", progress=92 + int((pos / max(1, len(final_files))) * 7), message=f"ZIP {pos}/{len(final_files)}")

    core.update_job(
        job_id,
        status="ready",
        stage="ready",
        progress=100,
        message=f"Playlist prête · {len(final_files)} vidéos · worker gbeerus",
        result_path=zip_path,
        filename="playlist-videos.zip",
        media_type="application/zip",
        result_size=os.path.getsize(zip_path),
        workdir=workdir,
        error=None,
    )


def _worker_fallback(job_id, payload):
    workdir = tempfile.mkdtemp(prefix=f"panda_worker_fallback_{job_id[:8]}_")
    core.update_job(job_id, status="running", stage="youtube_worker", progress=3, message="Cloud Run refusé par YouTube · bascule vers gbeerus", workdir=workdir, error=None)
    try:
        _remote_bundle(job_id, payload, workdir)
        if payload.get("playlist_mode"):
            _finish_playlist(job_id, payload, workdir)
        else:
            _finish_single(job_id, payload, workdir)
    except InterruptedError:
        shutil.rmtree(workdir, ignore_errors=True)
        core.update_job(job_id, status="cancelled", stage="cancelled", progress=0, message="Job annulé", result_path=None, workdir=None)
    except Exception as exc:
        message = str(exc)
        if stable._authish(message):
            message = "La session YouTube du worker gbeerus doit être reconnectée dans Chromium."
        shutil.rmtree(workdir, ignore_errors=True)
        core.update_job(job_id, status="error", stage="error", progress=0, message=message, error=message, result_path=None, workdir=None)


_original_worker = opt.optimized_worker


def worker(job_id, payload):
    _original_worker(job_id, payload)
    job = core.get_job(job_id) or {}
    if job.get("status") != "error":
        return
    clean = payload.get("url") or ""
    if core.detect_platform(clean) != "youtube" or not _worker_enabled():
        return
    text = str(job.get("error") or job.get("message") or "")
    if not stable._authish(text):
        return
    return _worker_fallback(job_id, payload)


opt.optimized_worker = worker
playlist.worker = worker
core.worker = worker

core.HTML = core.HTML.replace("V6.6 · FAST / STABLE", "V6.7 · WORKER AUTH")

stable._remove_route("/health", "GET")


@app.get("/health")
def health():
    core.cleanup_jobs()
    with core.JOB_LOCK:
        jobs = list(core.JOBS.values())
    active = sum(1 for j in jobs if j.get("status") in {"queued", "running"})
    ready = sum(1 for j in jobs if j.get("status") == "ready")
    try:
        disk = shutil.disk_usage("/tmp")
        tmp_free_mb = int(disk.free / 1024 / 1024)
    except Exception:
        tmp_free_mb = None
    with opt._INFO_CACHE_LOCK:
        cache_entries = len(opt._INFO_CACHE)
    return {
        "status": "ok",
        "service": "panda-download",
        "ui": "plugin-v6",
        "engine": "yt-dlp-cli+worker",
        "version": VERSION,
        "track_editor": True,
        "playlist_editor": True,
        "playlist_limit": playlist.PLAYLIST_LIMIT,
        "youtube_worker_configured": _worker_enabled(),
        "fragment_concurrency": opt.FRAGMENTS,
        "youtube_fragment_concurrency": opt.YOUTUBE_FRAGMENTS,
        "analysis_cache_ttl": opt.INFO_CACHE_TTL,
        "analysis_cache_entries": cache_entries,
        "job_ttl": core.JOB_TTL,
        "job_workers": opt.JOB_WORKERS,
        "active_jobs": active,
        "ready_jobs": ready,
        "youtube_cookie_file": bool(core.cookie_path()),
        "deno": shutil.which("deno") is not None,
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "tmp_free_mb": tmp_free_mb,
    }
