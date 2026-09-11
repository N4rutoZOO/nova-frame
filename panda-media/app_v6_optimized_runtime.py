import copy
import json
import mimetypes
import os
import shutil
import subprocess
import threading
import time
import zipfile
from collections import OrderedDict

from fastapi import Form
from fastapi.responses import JSONResponse

import app_v6_playlist_runtime as playlist

core = playlist.core
cli = playlist.cli
stable = playlist.stable
app = playlist.app

VERSION = "6.6-optimized"
FRAGMENTS = max(1, min(int(os.getenv("PANDA_YTDLP_FRAGMENTS", "4")), 8))
INFO_CACHE_TTL = max(30, min(int(os.getenv("PANDA_INFO_CACHE_TTL", "180")), 1800))
INFO_CACHE_MAX = max(16, min(int(os.getenv("PANDA_INFO_CACHE_MAX", "128")), 512))
core.JOB_TTL = max(600, min(int(os.getenv("PANDA_JOB_TTL", "3600")), 21600))

_INFO_CACHE = OrderedDict()
_INFO_CACHE_LOCK = threading.Lock()


def _cache_get(key):
    now = time.time()
    with _INFO_CACHE_LOCK:
        item = _INFO_CACHE.get(key)
        if not item:
            return None
        created, value = item
        if now - created > INFO_CACHE_TTL:
            _INFO_CACHE.pop(key, None)
            return None
        _INFO_CACHE.move_to_end(key)
        return copy.deepcopy(value)


def _cache_put(key, value):
    with _INFO_CACHE_LOCK:
        _INFO_CACHE[key] = (time.time(), copy.deepcopy(value))
        _INFO_CACHE.move_to_end(key)
        while len(_INFO_CACHE) > INFO_CACHE_MAX:
            _INFO_CACHE.popitem(last=False)


def _video_selector(quality, video_format):
    if quality == "best":
        height_filter = ""
        fallback_filter = ""
    else:
        try:
            height = max(144, min(int(quality), 4320))
        except (TypeError, ValueError):
            height = 1080
        height_filter = f"[height<={height}]"
        fallback_filter = f"[height<={height}]"

    if video_format in {"mp4", "mov", "avi"}:
        # Prefer an MP4/M4A pair first. It usually avoids an extra conversion/remux.
        return (
            f"bv*{height_filter}[ext=mp4]+ba[ext=m4a]/"
            f"bv*{height_filter}+ba/"
            f"b{fallback_filter}"
        )
    if video_format == "webm":
        return (
            f"bv*{height_filter}[ext=webm]+ba[ext=webm]/"
            f"bv*{height_filter}+ba/"
            f"b{fallback_filter}"
        )
    return f"bv*{height_filter}+ba/b{fallback_filter}"


def optimized_yt_args(url, workdir, mode, quality, video_format):
    args = ["-o", os.path.join(workdir, "%(id)s.%(ext)s")]
    if mode == "audio":
        args += ["-f", "bestaudio/best"]
    else:
        args += ["-f", _video_selector(quality, video_format)]
        merge_format = video_format if video_format in {"mp4", "mkv", "webm"} else "mp4"
        args += ["--merge-output-format", merge_format]
    args.append(url)
    return args


def _base_fast_flags(no_playlist=True):
    flags = [
        "--newline",
        "--progress",
        "--retries", "8",
        "--fragment-retries", "8",
        "--extractor-retries", "3",
        "--file-access-retries", "3",
        "--socket-timeout", "20",
        "--concurrent-fragments", str(FRAGMENTS),
        "--continue",
        "--part",
        "--embed-metadata",
        "--write-info-json",
    ]
    if no_playlist:
        flags += ["--no-playlist"]
    return flags


def optimized_run_ytdlp(job_id, args, cwd):
    cmd = [
        *cli.YTDLP,
        *_base_fast_flags(True),
        "--progress-template",
        "download:PANDA|%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
        *args,
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
    )
    tail = []
    last_progress = -1
    last_update = 0.0
    try:
        while True:
            if core.is_cancelled(job_id):
                cli._terminate(proc)
                raise InterruptedError("Job annulé")

            line = proc.stdout.readline() if proc.stdout else ""
            if line:
                line = line.rstrip()
                tail.append(line)
                if len(tail) > 100:
                    tail = tail[-100:]
                if line.startswith("PANDA|"):
                    parts = line.split("|", 3)
                    pct = cli._progress_value(parts[1] if len(parts) > 1 else "")
                    speed = (parts[2] if len(parts) > 2 else "").strip()
                    eta = (parts[3] if len(parts) > 3 else "").strip()
                    now = time.time()
                    rounded = int(pct)
                    # Avoid hammering the shared job lock for tiny progress changes.
                    if rounded != last_progress or now - last_update >= 1.0:
                        msg = f"Téléchargement {pct:.0f}%"
                        if speed and speed not in {"N/A", "Unknown"}:
                            msg += f" · {speed}"
                        if eta and eta not in {"N/A", "Unknown"}:
                            msg += f" · ETA {eta}"
                        core.update_job(
                            job_id,
                            stage="downloading",
                            progress=8 + int(pct * 0.54),
                            message=msg,
                        )
                        last_progress = rounded
                        last_update = now

            if proc.poll() is not None:
                if proc.stdout:
                    rest = proc.stdout.read()
                    if rest:
                        tail.extend(rest.splitlines()[-50:])
                break

            if not line:
                time.sleep(0.04)

        if proc.returncode != 0:
            raise RuntimeError("\n".join(tail[-50:])[-5000:] or "yt-dlp a échoué.")
    finally:
        if proc.poll() is None:
            cli._terminate(proc)


def fast_ffmpeg_track(
    source,
    output,
    start,
    length,
    fmt,
    bitrate,
    title,
    number,
    album,
    artist,
    stream_copy=False,
):
    br = None if bitrate == "best" else f"{bitrate}k"
    args = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-i", source,
        "-t", f"{length:.3f}",
        "-map", "0:a:0", "-vn",
    ]
    if stream_copy:
        args += ["-c:a", "copy"]
    elif fmt == "mp3":
        args += ["-c:a", "libmp3lame", "-b:a", br or "320k"]
    elif fmt == "m4a":
        args += ["-c:a", "aac", "-b:a", br or "256k", "-movflags", "+faststart"]
    elif fmt == "opus":
        args += ["-c:a", "libopus", "-b:a", br or "192k"]
    elif fmt == "ogg":
        args += ["-c:a", "libvorbis", "-b:a", br or "192k"]
    elif fmt == "flac":
        args += ["-c:a", "flac"]
    elif fmt == "wav":
        args += ["-c:a", "pcm_s16le"]
    else:
        args += ["-c:a", "copy"]

    args += [
        "-metadata", f"title={title}",
        "-metadata", f"track={number}",
        "-metadata", f"album={album}",
    ]
    if artist:
        args += ["-metadata", f"artist={artist}"]
    args.append(output)

    proc = subprocess.run(args, capture_output=True, text=True, timeout=3300, check=False)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or f"FFmpeg failed for {title}")[-1800:])


def optimized_build_tracks_zip(
    job_id,
    source,
    info,
    selected,
    custom_titles,
    audio_format,
    audio_quality,
    workdir,
):
    chapters = core.extract_chapters(info)
    if not chapters:
        raise RuntimeError("Aucun chapitre ou timestamp exploitable n’a été détecté.")

    selected = sorted({
        i for i in selected
        if isinstance(i, int) and 0 <= i < len(chapters)
    })
    if not selected:
        raise RuntimeError("Sélectionne au moins une track.")

    source_ext = os.path.splitext(source)[1].lower().lstrip(".") or "m4a"
    original_requested = audio_format == "original"
    fmt = audio_format
    if original_requested:
        fmt = (
            source_ext
            if source_ext in {"m4a", "mp3", "opus", "ogg", "flac", "wav", "webm", "aac", "mka"}
            else "m4a"
        )

    album = core.clean_title(info.get("title") or "Mix")
    raw_artist = info.get("uploader") or info.get("channel") or info.get("artist") or ""
    artist = core.clean_title(raw_artist, 0) if raw_artist else ""
    zip_path = os.path.join(workdir, "tracks.zip")
    tracklist = []
    metadata = []
    cover = core.download_cover(info.get("thumbnail"), workdir)
    total = len(selected)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for pos, idx in enumerate(selected, 1):
            if core.is_cancelled(job_id):
                raise InterruptedError("Job annulé")

            chapter = chapters[idx]
            original_no = int(chapter.get("number") or idx + 1)
            custom = custom_titles.get(str(idx)) or custom_titles.get(str(original_no))
            title = core.clean_title(custom or chapter.get("title"), original_no)
            start = float(chapter["start"])
            length = max(0.05, float(chapter["end"]) - start)
            track_path = os.path.join(workdir, f"track-{original_no:03d}.{fmt}")

            fast_ffmpeg_track(
                source,
                track_path,
                start,
                length,
                fmt,
                audio_quality,
                title,
                original_no,
                album,
                artist,
                stream_copy=original_requested,
            )

            arcname = f"{original_no:02d} - {title}.{fmt}"
            archive.write(track_path, arcname=arcname)
            try:
                os.remove(track_path)
            except OSError:
                pass

            tracklist.append(f"{original_no:02d}. {title}")
            metadata.append({
                "number": original_no,
                "title": title,
                "start": start,
                "duration": length,
            })
            progress = 70 + int((pos / total) * 24)
            mode_text = "sans réencodage" if original_requested else fmt.upper()
            core.update_job(
                job_id,
                stage="splitting",
                progress=progress,
                message=f"Découpage {pos}/{total} · {mode_text}",
                track_current=pos,
                track_total=total,
            )

        archive.writestr("tracklist.txt", "\n".join(tracklist) + "\n")
        archive.writestr(
            "metadata.json",
            json.dumps(
                {"album": album, "artist": artist, "tracks": metadata},
                ensure_ascii=False,
                indent=2,
            ),
        )
        if cover and os.path.isfile(cover):
            archive.write(cover, arcname=os.path.basename(cover))

    return zip_path


def optimized_playlist_json(url, use_cookies=False):
    cmd = [
        *cli.YTDLP,
        "--flat-playlist",
        "--dump-single-json",
        "--no-warnings",
        "--extractor-retries", "3",
        "--socket-timeout", "20",
        "--playlist-end", str(playlist.PLAYLIST_LIMIT),
        url,
    ]
    cookie = None
    if use_cookies:
        cookie = stable._private_cookie_copy()
        if cookie:
            cmd[-1:-1] = ["--cookies", cookie]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                (proc.stderr or proc.stdout or "Analyse playlist impossible")[-4000:]
            )
        return json.loads(proc.stdout)
    finally:
        if cookie:
            try:
                os.remove(cookie)
            except OSError:
                pass


def optimized_playlist_download(job_id, payload, workdir, use_cookies=False):
    selected = payload.get("selected_playlist") or []
    selected_one_based = [x + 1 for x in selected]
    selected_map = {value: pos for pos, value in enumerate(selected_one_based, 1)}
    total_selected = len(selected_one_based)
    quality = payload.get("quality") or "best"
    video_format = (
        payload.get("video_format")
        if payload.get("video_format") in core.VIDEO_FORMATS
        else "mp4"
    )
    merge_format = video_format if video_format in {"mp4", "mkv", "webm"} else "mp4"

    cmd = [
        *cli.YTDLP,
        *_base_fast_flags(False),
        "--yes-playlist",
        "--playlist-items", ",".join(str(x) for x in selected_one_based),
        "--windows-filenames",
        "--trim-filenames", "140",
        "--progress-template",
        "download:PANDAPL|%(info.playlist_index)s|%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
        "-f", _video_selector(quality, video_format),
        "--merge-output-format", merge_format,
        "-o", os.path.join(workdir, "%(playlist_index)03d - %(title)s.%(ext)s"),
    ]

    cookie = None
    if use_cookies:
        cookie = stable._private_cookie_copy()
        if cookie:
            cmd += ["--cookies", cookie]
    cmd.append(payload["url"])

    proc = subprocess.Popen(
        cmd,
        cwd=workdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
    )
    tail = []
    last_key = None
    last_update = 0.0
    try:
        while True:
            if core.is_cancelled(job_id):
                cli._terminate(proc)
                raise InterruptedError("Job annulé")

            line = proc.stdout.readline() if proc.stdout else ""
            if line:
                line = line.rstrip()
                tail.append(line)
                if len(tail) > 120:
                    tail = tail[-120:]
                if line.startswith("PANDAPL|"):
                    parts = line.split("|", 4)
                    try:
                        original_index = int(parts[1])
                    except Exception:
                        original_index = selected_one_based[0]
                    file_pct = cli._progress_value(parts[2] if len(parts) > 2 else "")
                    position = selected_map.get(original_index, 1)
                    overall = ((position - 1) + file_pct / 100.0) / max(1, total_selected)
                    speed = (parts[3] if len(parts) > 3 else "").strip()
                    eta = (parts[4] if len(parts) > 4 else "").strip()
                    key = (position, int(file_pct))
                    now = time.time()
                    if key != last_key or now - last_update >= 1.0:
                        msg = f"Vidéo {position}/{total_selected} · {file_pct:.0f}%"
                        if speed and speed not in {"N/A", "Unknown"}:
                            msg += f" · {speed}"
                        if eta and eta not in {"N/A", "Unknown"}:
                            msg += f" · ETA {eta}"
                        core.update_job(
                            job_id,
                            stage="downloading",
                            progress=7 + int(overall * 72),
                            message=msg,
                            item_current=position,
                            item_total=total_selected,
                        )
                        last_key = key
                        last_update = now

            if proc.poll() is not None:
                if proc.stdout:
                    rest = proc.stdout.read()
                    if rest:
                        tail.extend(rest.splitlines()[-60:])
                break

            if not line:
                time.sleep(0.04)

        if proc.returncode != 0:
            raise RuntimeError(
                "\n".join(tail[-60:])[-6000:] or "yt-dlp playlist a échoué."
            )
    finally:
        if proc.poll() is None:
            cli._terminate(proc)
        if cookie:
            try:
                os.remove(cookie)
            except OSError:
                pass


def convert_playlist_video(source, fmt, workdir, pos):
    current = os.path.splitext(source)[1].lower().lstrip(".")
    if current == fmt:
        return source

    out = os.path.join(workdir, f"playlist-converted-{pos:03d}.{fmt}")

    if fmt in {"mp4", "mkv", "mov"}:
        copy_cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", source,
            "-map", "0:v:0", "-map", "0:a:0?",
            "-c", "copy",
        ]
        if fmt in {"mp4", "mov"}:
            copy_cmd += ["-movflags", "+faststart"]
        copy_cmd.append(out)
        proc = subprocess.run(copy_cmd, capture_output=True, text=True, timeout=3300, check=False)
        if proc.returncode == 0:
            return out

    if fmt == "mp4":
        args = [
            "-i", source, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out,
        ]
    elif fmt == "mov":
        args = [
            "-i", source, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out,
        ]
    elif fmt == "mkv":
        args = [
            "-i", source, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", out,
        ]
    elif fmt == "webm":
        args = [
            "-i", source, "-c:v", "libvpx-vp9", "-deadline", "realtime",
            "-cpu-used", "5", "-crf", "31", "-b:v", "0",
            "-c:a", "libopus", "-b:a", "160k", out,
        ]
    else:
        args = [
            "-i", source, "-c:v", "mpeg4", "-q:v", "3",
            "-c:a", "libmp3lame", "-b:a", "192k", out,
        ]

    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
        timeout=3300,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or f"Conversion {fmt} impossible")[-2000:])
    return out


def optimized_playlist_worker(job_id, payload):
    workdir = core.tempfile.mkdtemp(prefix=f"panda_playlist_{job_id[:8]}_")
    core.update_job(
        job_id,
        workdir=workdir,
        status="running",
        stage="preparing",
        progress=2,
        message="Préparation playlist yt-dlp",
    )
    try:
        if payload.get("mode") != "video":
            raise RuntimeError(
                "Le Playlist Editor est prévu pour les vidéos. Passe en mode VIDÉO."
            )

        core.update_job(
            job_id,
            stage="downloading",
            progress=5,
            message=f"yt-dlp · {FRAGMENTS} fragments simultanés",
        )
        try:
            optimized_playlist_download(job_id, payload, workdir, False)
        except InterruptedError:
            raise
        except Exception as public_exc:
            if not stable._authish(str(public_exc)):
                raise
            core.update_job(
                job_id,
                stage="auth_retry",
                progress=4,
                message="YouTube demande une session · nouvelle tentative",
            )
            optimized_playlist_download(job_id, payload, workdir, True)

        files = playlist._playlist_files(workdir)
        if not files:
            raise RuntimeError("Aucune vidéo de la playlist n’a été générée.")

        wanted_format = (
            payload.get("video_format")
            if payload.get("video_format") in core.VIDEO_FORMATS
            else "mp4"
        )
        final_files = []
        total = len(files)
        for pos, source in enumerate(files, 1):
            if core.is_cancelled(job_id):
                raise InterruptedError("Job annulé")
            current_ext = os.path.splitext(source)[1].lower().lstrip(".")
            if current_ext == wanted_format:
                final = source
            else:
                core.update_job(
                    job_id,
                    stage="converting",
                    progress=80 + int(pos / total * 10),
                    message=f"Conversion {pos}/{total} · {wanted_format.upper()}",
                )
                final = convert_playlist_video(source, wanted_format, workdir, pos)
            final_files.append(final)

        # Remove obsolete source variants after conversion to reduce /tmp pressure.
        keep = {os.path.abspath(path) for path in final_files}
        for path in files:
            if os.path.abspath(path) not in keep:
                try:
                    os.remove(path)
                except OSError:
                    pass

        core.update_job(
            job_id,
            stage="packaging",
            progress=92,
            message=f"Création du ZIP · {len(final_files)} vidéos",
        )
        zip_path = os.path.join(workdir, "playlist-videos.zip")
        with zipfile.ZipFile(
            zip_path,
            "w",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
        ) as archive:
            for pos, path in enumerate(final_files, 1):
                if core.is_cancelled(job_id):
                    raise InterruptedError("Job annulé")
                archive.write(path, arcname=os.path.basename(path))
                # Media is already compressed. Once copied into the stored ZIP, free it.
                try:
                    os.remove(path)
                except OSError:
                    pass
                core.update_job(
                    job_id,
                    stage="packaging",
                    progress=92 + int((pos / max(1, len(final_files))) * 6),
                    message=f"ZIP {pos}/{len(final_files)}",
                )

        size = os.path.getsize(zip_path)
        core.update_job(
            job_id,
            status="ready",
            stage="ready",
            progress=100,
            message=f"Playlist prête · {len(final_files)} vidéos",
            result_path=zip_path,
            filename="playlist-videos.zip",
            media_type="application/zip",
            result_size=size,
        )

    except InterruptedError:
        shutil.rmtree(workdir, ignore_errors=True)
        core.update_job(
            job_id,
            status="cancelled",
            stage="cancelled",
            progress=0,
            message="Job annulé",
            result_path=None,
            workdir=None,
        )
    except Exception as exc:
        msg = core.friendly_error(exc)
        shutil.rmtree(workdir, ignore_errors=True)
        core.update_job(
            job_id,
            status="error",
            stage="error",
            progress=0,
            message=msg,
            error=msg,
            result_path=None,
            workdir=None,
        )


# Apply patches before the first user request.
cli._yt_args = optimized_yt_args
cli._run_ytdlp = optimized_run_ytdlp
core.build_tracks_zip = optimized_build_tracks_zip
playlist._playlist_json = optimized_playlist_json
playlist._run_playlist_download = optimized_playlist_download
playlist.playlist_worker = optimized_playlist_worker


def optimized_worker(job_id, payload):
    if payload.get("playlist_mode"):
        return optimized_playlist_worker(job_id, payload)
    return cli.cli_worker(job_id, payload)


playlist.worker = optimized_worker
core.worker = optimized_worker


# Cache only successful analysis results. Authentication/errors are never cached.
_original_media_info = playlist.media_info
stable._remove_route("/info", "POST")


@app.post("/info")
def cached_media_info(url: str = Form(...)):
    key = (url or "").strip()
    cached = _cache_get(key)
    if cached is not None:
        cached["analysis_cached"] = True
        return cached

    result = _original_media_info(url)
    if isinstance(result, dict) and not result.get("error"):
        _cache_put(key, result)
    return result


core.HTML = core.HTML.replace("V6.5 · PLAYLISTS", "V6.6 · FAST / STABLE")
core.HTML = core.HTML.replace(
    "Analyse en cours…",
    f"Analyse en cours… · moteur optimisé {FRAGMENTS} fragments",
)

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
    with _INFO_CACHE_LOCK:
        cache_entries = len(_INFO_CACHE)

    return {
        "status": "ok",
        "service": "panda-download",
        "ui": "plugin-v6",
        "engine": "yt-dlp-cli",
        "version": VERSION,
        "track_editor": True,
        "playlist_editor": True,
        "playlist_limit": playlist.PLAYLIST_LIMIT,
        "fragment_concurrency": FRAGMENTS,
        "analysis_cache_ttl": INFO_CACHE_TTL,
        "analysis_cache_entries": cache_entries,
        "job_ttl": core.JOB_TTL,
        "active_jobs": active,
        "ready_jobs": ready,
        "youtube_cookie_file": bool(core.cookie_path()),
        "deno": shutil.which("deno") is not None,
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "tmp_free_mb": tmp_free_mb,
    }
