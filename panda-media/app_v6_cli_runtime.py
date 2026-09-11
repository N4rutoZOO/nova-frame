import glob
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import time

import app_v6_runtime as stable

core = stable.core
app = stable.app

VERSION = "6.4-cli"
YTDLP = [sys.executable, "-m", "yt_dlp"]


def _terminate(proc):
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def _progress_value(raw):
    text = (raw or "").strip().replace("%", "")
    try:
        return max(0.0, min(100.0, float(text)))
    except ValueError:
        return 0.0


def _run_ytdlp(job_id, args, cwd):
    cmd = [
        *YTDLP,
        "--newline",
        "--progress",
        "--no-playlist",
        "--retries", "5",
        "--fragment-retries", "5",
        "--embed-metadata",
        "--write-info-json",
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
        bufsize=1,
    )
    tail = []
    try:
        while True:
            if core.is_cancelled(job_id):
                _terminate(proc)
                raise InterruptedError("Job annulé")

            line = proc.stdout.readline() if proc.stdout else ""
            if line:
                line = line.rstrip()
                tail.append(line)
                if len(tail) > 80:
                    tail = tail[-80:]
                if line.startswith("PANDA|"):
                    parts = line.split("|", 3)
                    pct = _progress_value(parts[1] if len(parts) > 1 else "")
                    speed = (parts[2] if len(parts) > 2 else "").strip()
                    eta = (parts[3] if len(parts) > 3 else "").strip()
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

            if proc.poll() is not None:
                if proc.stdout:
                    rest = proc.stdout.read()
                    if rest:
                        tail.extend(rest.splitlines()[-40:])
                break

            if not line:
                time.sleep(0.05)

        if proc.returncode != 0:
            raise RuntimeError("\n".join(tail[-40:])[-4000:] or "yt-dlp a échoué.")
    finally:
        if proc.poll() is None:
            _terminate(proc)


def _cookie_args():
    cookie = stable._private_cookie_copy()
    return cookie, (["--cookies", cookie] if cookie else [])


def _yt_args(url, workdir, mode, quality, video_format):
    args = [
        "-o", os.path.join(workdir, "%(id)s.%(ext)s"),
    ]
    if mode == "audio":
        args += ["-f", "bestaudio/best"]
    else:
        if quality == "best":
            selector = "bv*+ba/b"
        else:
            try:
                height = max(144, min(int(quality), 4320))
            except (TypeError, ValueError):
                height = 1080
            selector = f"bv*[height<={height}]+ba/b[height<={height}]"
        args += ["-f", selector]
        args += ["--merge-output-format", "mp4" if video_format == "mp4" else "mkv"]
    args.append(url)
    return args


def _load_info(workdir):
    matches = sorted(
        glob.glob(os.path.join(workdir, "*.info.json")),
        key=lambda p: os.path.getmtime(p),
        reverse=True,
    )
    if not matches:
        return {}
    try:
        with open(matches[0], "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def _download_with_fallback(job_id, clean, platform, args, workdir):
    try:
        _run_ytdlp(job_id, args, workdir)
        return
    except InterruptedError:
        raise
    except Exception as public_exc:
        if platform != "youtube" or not stable._authish(str(public_exc)):
            raise
        cookie_file, cookie_args = _cookie_args()
        if not cookie_args:
            raise RuntimeError(
                "YouTube demande une authentification pour cette vidéo. "
                "Les téléchargements publics utilisent bien yt-dlp sans cookies en priorité."
            ) from public_exc
        try:
            core.update_job(
                job_id,
                stage="auth_retry",
                progress=4,
                message="YouTube demande une session · nouvelle tentative",
            )
            _run_ytdlp(job_id, [*args[:-1], *cookie_args, args[-1]], workdir)
        finally:
            if cookie_file:
                try:
                    os.remove(cookie_file)
                except OSError:
                    pass


def cli_worker(job_id, payload):
    workdir = core.tempfile.mkdtemp(prefix=f"panda_cli_{job_id[:8]}_")
    core.update_job(
        job_id,
        workdir=workdir,
        status="running",
        stage="preparing",
        progress=2,
        message="Préparation yt-dlp",
    )
    try:
        clean = core.validate_public_url(payload["url"])
        platform = core.detect_platform(clean)

        if platform in {"tidal", "deezer"}:
            raise RuntimeError("Cette source n’est pas disponible pour le téléchargement direct.")

        if platform == "spotify":
            return stable.spotify_worker(job_id, payload)

        mode = "audio" if payload.get("mode") == "audio" else "video"
        quality = payload.get("quality") or "best"
        audio_quality = (
            payload.get("audio_quality")
            if payload.get("audio_quality") in core.AUDIO_BITRATES
            else "best"
        )
        video_format = (
            payload.get("video_format")
            if payload.get("video_format") in core.VIDEO_FORMATS
            else "mp4"
        )
        audio_format = (
            payload.get("audio_format")
            if payload.get("audio_format") in core.AUDIO_FORMATS
            else "original"
        )
        split_tracks = bool(payload.get("split_tracks")) and mode == "audio"

        args = _yt_args(clean, workdir, mode, quality, video_format)
        core.update_job(
            job_id,
            stage="downloading",
            progress=6,
            message="yt-dlp · connexion à la source",
        )
        _download_with_fallback(job_id, clean, platform, args, workdir)

        if core.is_cancelled(job_id):
            raise InterruptedError("Job annulé")

        info = _load_info(workdir)
        files = core.media_files(workdir)
        if not files:
            raise RuntimeError("yt-dlp n’a généré aucun fichier média.")

        source = max(files, key=os.path.getsize)
        title = info.get("title") or info.get("id") or os.path.splitext(os.path.basename(source))[0] or "download"

        if mode == "video":
            if video_format == "mp4" and os.path.splitext(source)[1].lower() == ".mp4":
                final_file = source
                core.update_job(
                    job_id,
                    stage="processing",
                    progress=94,
                    message="MP4 prêt · aucune conversion supplémentaire",
                )
            else:
                core.update_job(
                    job_id,
                    stage="converting",
                    progress=70,
                    message=f"Conversion {video_format.upper()}",
                )
                final_file = core.convert_video(source, video_format, workdir)
            filename = core.pretty_name(title, os.path.splitext(final_file)[1])

        elif split_tracks:
            if not info:
                raise RuntimeError("La tracklist n’a pas pu être récupérée depuis yt-dlp.")
            core.update_job(
                job_id,
                stage="splitting",
                progress=70,
                message="Découpage des tracks",
            )
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
                core.update_job(
                    job_id,
                    stage="processing",
                    progress=94,
                    message="Audio source prêt · aucune conversion",
                )
            else:
                core.update_job(
                    job_id,
                    stage="converting",
                    progress=72,
                    message=f"Conversion {audio_format.upper()}",
                )
                final_file = core.convert_audio(source, audio_format, audio_quality, workdir)
            filename = core.pretty_name(title, os.path.splitext(final_file)[1])

        size = os.path.getsize(final_file)
        core.update_job(
            job_id,
            status="ready",
            stage="ready",
            progress=100,
            message="Prêt à télécharger",
            result_path=final_file,
            filename=filename,
            media_type=mimetypes.guess_type(final_file)[0] or "application/octet-stream",
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


core.worker = cli_worker
core.HTML = core.HTML.replace("V6.2 STABLE", "V6.4 · YT-DLP CORE")

stable._remove_route("/health", "GET")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "panda-download",
        "ui": "plugin-v6",
        "engine": "yt-dlp-cli",
        "version": VERSION,
        "track_editor": True,
        "youtube_cookies": bool(core.cookie_path()),
        "deno": shutil.which("deno") is not None,
        "ffmpeg": shutil.which("ffmpeg") is not None,
    }
