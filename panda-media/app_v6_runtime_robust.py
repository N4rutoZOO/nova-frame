import os
import shutil
import threading

import yt_dlp
from fastapi import Form
from fastapi.responses import JSONResponse

import app_v6_runtime as stable

core = stable.core
app = stable.app
_guest_ctx = threading.local()
_original_options = core.base_ydl_options


def _authish(text: str) -> bool:
    low = (text or "").lower()
    markers = (
        "sign in to confirm",
        "confirm you're not a bot",
        "confirm you’re not a bot",
        "account cookies are no longer valid",
        "cookies are no longer valid",
        "login required",
        "authentication required",
        "authentification youtube requise",
        "youtube refuse la session",
        "session youtube",
        "session youtube valide",
        "not a bot",
    )
    return any(marker in low for marker in markers)


stable._authish = _authish


def robust_options():
    opts = _original_options()
    if getattr(_guest_ctx, "alternate", False):
        opts.pop("cookiefile", None)
        extractor_args = dict(opts.get("extractor_args") or {})
        youtube_args = dict(extractor_args.get("youtube") or {})
        youtube_args["player_client"] = ["android_vr", "web_embedded"]
        youtube_args["player_skip"] = ["webpage"]
        extractor_args["youtube"] = youtube_args
        opts["extractor_args"] = extractor_args
    return opts


core.base_ydl_options = robust_options


def _with_guest(alternate: bool, fn):
    old = getattr(_guest_ctx, "alternate", False)
    try:
        _guest_ctx.alternate = bool(alternate)
        return fn()
    finally:
        _guest_ctx.alternate = old


def _extract(clean: str, use_cookies=False, alternate=False):
    return _with_guest(alternate, lambda: stable._extract_info(clean, use_cookies))


def _failed_job_text(job_id: str):
    job = core.get_job(job_id) or {}
    return job, str(job.get("error") or job.get("message") or "")


def _cleanup_failed_attempt(job_id: str):
    job = core.get_job(job_id) or {}
    workdir = job.get("workdir")
    if workdir:
        shutil.rmtree(workdir, ignore_errors=True)
    core.update_job(job_id, workdir=None, result_path=None, error=None)


def _run_worker(job_id: str, payload: dict, use_cookies=False, alternate=False):
    return _with_guest(alternate, lambda: stable._run_original_worker(job_id, payload, use_cookies))


stable._remove_route("/info", "POST")


@app.post("/info")
def robust_media_info(url: str = Form(...)):
    try:
        clean = core.validate_public_url(url)
        platform = core.detect_platform(clean)
        if platform == "spotify":
            return {
                "platform": "spotify", "title": "Spotify", "uploader": "", "thumbnail": "",
                "duration": None, "qualities": [], "quality_sizes": {}, "audio_only": True,
                "blocked": False, "chapters": [], "youtube_path": None,
            }
        if platform in {"tidal", "deezer"}:
            return JSONResponse(status_code=400, content={"error": "Cette source n’est pas disponible pour le téléchargement direct."})

        path = "default"
        try:
            info = _extract(clean, False, False)
        except Exception as first_exc:
            if platform != "youtube" or not _authish(str(first_exc)):
                raise
            try:
                path = "alternate_guest"
                info = _extract(clean, False, True)
            except Exception as guest_exc:
                if not _authish(str(guest_exc)):
                    raise
                try:
                    path = "cookies"
                    info = _extract(clean, True, False)
                except Exception as cookie_exc:
                    if _authish(str(cookie_exc)):
                        mounted = bool(core.cookie_path())
                        raise RuntimeError(
                            "YouTube bloque cette requête depuis l’IP Cloud Run. "
                            + ("La session cookie montée est aussi refusée." if mounted else "Aucune session cookie valide n’est montée.")
                        )
                    raise

        if not info:
            raise RuntimeError("Aucun média détecté.")
        qualities = sorted({
            int(f["height"]) for f in info.get("formats", [])
            if f.get("height") and f.get("vcodec") != "none"
        }, reverse=True)
        chapters = core.extract_chapters(info)
        return {
            "platform": platform if platform != "generic" else (info.get("extractor_key") or "media"),
            "title": info.get("title"),
            "uploader": info.get("uploader") or info.get("channel") or info.get("artist"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "qualities": qualities,
            "quality_sizes": core.quality_sizes(info),
            "audio_only": not bool(qualities),
            "blocked": False,
            "chapters": chapters,
            "youtube_path": path if platform == "youtube" else None,
        }
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)[-1800:]})


def robust_worker(job_id: str, payload: dict):
    try:
        clean = core.validate_public_url(payload["url"])
        platform = core.detect_platform(clean)
    except Exception:
        return _run_worker(job_id, payload, False, False)

    if platform == "spotify":
        return stable.spotify_worker(job_id, payload)
    if platform != "youtube":
        return _run_worker(job_id, payload, False, False)

    _run_worker(job_id, payload, False, False)
    job, text = _failed_job_text(job_id)
    if job.get("status") != "error" or not _authish(text):
        return

    _cleanup_failed_attempt(job_id)
    core.update_job(job_id, status="queued", stage="guest_retry", progress=1,
                    message="Nouvelle tentative YouTube en mode invité alternatif", error=None)
    _run_worker(job_id, payload, False, True)
    job, text = _failed_job_text(job_id)
    if job.get("status") != "error" or not _authish(text):
        return

    _cleanup_failed_attempt(job_id)
    core.update_job(job_id, status="queued", stage="auth_retry", progress=1,
                    message="Dernière tentative avec session YouTube", error=None)
    _run_worker(job_id, payload, True, False)
    job, text = _failed_job_text(job_id)
    if job.get("status") == "error" and _authish(text):
        mounted = bool(core.cookie_path())
        message = (
            "YouTube bloque actuellement l’IP Cloud Run et la session cookie montée est refusée."
            if mounted else
            "YouTube bloque actuellement l’IP Cloud Run et aucune session cookie valide n’est montée."
        )
        core.update_job(job_id, message=message, error=message)


core.worker = robust_worker
core.HTML = core.HTML.replace("V6.2 STABLE", "V6.3 ROBUST")

stable._remove_route("/health", "GET")


@app.get("/health")
def robust_health():
    cookie_file = core.cookie_path()
    cookie_count = 0
    auth_cookie_count = 0
    if cookie_file and os.path.isfile(cookie_file):
        try:
            with open(cookie_file, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if not line or line.startswith("#"):
                        continue
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) >= 7:
                        cookie_count += 1
                        if parts[5] in {"SAPISID", "APISID", "__Secure-1PAPISID", "__Secure-3PAPISID", "LOGIN_INFO"}:
                            auth_cookie_count += 1
        except OSError:
            pass
    return {
        "status": "ok",
        "service": "panda-download",
        "ui": "plugin-v6.3",
        "youtube_strategy": "guest-default > guest-alt > cookies",
        "alternate_youtube_clients": ["android_vr", "web_embedded"],
        "youtube_cookies_mounted": bool(cookie_file),
        "youtube_cookie_count": cookie_count,
        "youtube_auth_cookie_count": auth_cookie_count,
        "deno": shutil.which("deno") is not None,
        "job_engine": True,
        "track_editor": True,
        "in_page_download_progress": True,
    }
