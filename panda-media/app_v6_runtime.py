import mimetypes
import os
import shutil
import subprocess
import tempfile
import threading
import uuid

import yt_dlp
from fastapi import Form
from fastapi.responses import JSONResponse

import app_v6 as core

app = core.app
_original_worker = core.worker
_original_base_ydl_options = core.base_ydl_options
_auth_ctx = threading.local()


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
        "youtube refuse la session",
        "session youtube",
    )
    return any(marker in low for marker in markers)


def _private_cookie_copy():
    src = core.cookie_path()
    if not src or not os.path.isfile(src):
        return None
    dst = os.path.join(tempfile.gettempdir(), f"panda-youtube-{uuid.uuid4().hex}.txt")
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o600)
    return dst


def resilient_base_ydl_options():
    """Public-first: a stale account cookie can never break public YouTube videos."""
    opts = _original_base_ydl_options()
    opts.pop("cookiefile", None)
    cookie_file = getattr(_auth_ctx, "cookiefile", None)
    if getattr(_auth_ctx, "use_cookies", False) and cookie_file and os.path.isfile(cookie_file):
        opts["cookiefile"] = cookie_file
    return opts


core.base_ydl_options = resilient_base_ydl_options


def _with_auth_mode(use_cookies, fn):
    cookie_file = None
    try:
        _auth_ctx.use_cookies = bool(use_cookies)
        if use_cookies:
            cookie_file = _private_cookie_copy()
            _auth_ctx.cookiefile = cookie_file
        else:
            _auth_ctx.cookiefile = None
        return fn()
    finally:
        _auth_ctx.use_cookies = False
        _auth_ctx.cookiefile = None
        if cookie_file:
            try:
                os.remove(cookie_file)
            except OSError:
                pass


def _extract_info(clean: str, use_cookies: bool):
    def run():
        opts = core.base_ydl_options()
        opts["skip_download"] = True
        opts["outtmpl"] = "%(id)s.%(ext)s"
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(clean, download=False)
    return _with_auth_mode(use_cookies, run)


def _remove_route(path: str, method: str):
    method = method.upper()
    app.router.routes[:] = [
        route for route in app.router.routes
        if not (getattr(route, "path", None) == path and method in (getattr(route, "methods", set()) or set()))
    ]


# Replace only /info; all other V6 endpoints remain untouched.
_remove_route("/info", "POST")


@app.post("/info")
def resilient_media_info(url: str = Form(...)):
    try:
        clean = core.validate_public_url(url)
        platform = core.detect_platform(clean)
        if platform == "spotify":
            return {
                "platform": "spotify", "title": "Spotify", "uploader": "", "thumbnail": "",
                "duration": None, "qualities": [], "quality_sizes": {}, "audio_only": True,
                "blocked": False, "chapters": [],
            }
        if platform in {"tidal", "deezer"}:
            return JSONResponse(status_code=400, content={"error": "Cette source n’est pas disponible pour le téléchargement direct."})

        try:
            info = _extract_info(clean, False)
        except Exception as public_exc:
            # Cookies are only attempted when the public/guest path explicitly needs auth.
            if platform != "youtube" or not _authish(str(public_exc)):
                raise
            try:
                info = _extract_info(clean, True)
            except Exception as cookie_exc:
                if _authish(str(cookie_exc)):
                    raise RuntimeError(
                        "Cette vidéo nécessite une session YouTube valide. Les vidéos publiques restent disponibles même si les cookies expirent."
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
        }
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": core.friendly_error(exc)})


def spotify_worker(job_id: str, payload: dict):
    workdir = core.tempfile.mkdtemp(prefix=f"panda_spotify_{job_id[:8]}_")
    core.update_job(job_id, workdir=workdir, status="running", stage="downloading", progress=8, message="Analyse Spotify")
    try:
        if payload.get("split_tracks"):
            raise RuntimeError("Le mode chapitres/tracks n’est pas utilisé pour Spotify : les playlists/albums sont déjà séparés en pistes.")

        audio_format = payload.get("audio_format") if payload.get("audio_format") in core.AUDIO_FORMATS else "original"
        audio_quality = payload.get("audio_quality") if payload.get("audio_quality") in core.AUDIO_BITRATES else "best"
        target_format = "opus" if audio_format == "original" else audio_format
        if target_format not in {"mp3", "m4a", "opus", "flac", "wav", "ogg"}:
            target_format = "opus"
        bitrate = "disable" if audio_quality == "best" or target_format in {"flac", "wav"} else f"{audio_quality}k"

        base_cmd = [
            os.sys.executable, "-m", "spotdl", "download", payload["url"],
            "--format", target_format,
            "--bitrate", bitrate,
            "--threads", "1",
            "--output", os.path.join(workdir, "{list-position} - {artists} - {title}.{output-ext}"),
        ]

        core.update_job(job_id, stage="downloading", progress=20, message="Recherche des correspondances audio")
        proc = subprocess.run(base_cmd, cwd=workdir, capture_output=True, text=True, timeout=3300, check=False)
        combined = (proc.stderr or "") + "\n" + (proc.stdout or "")

        # Retry with an isolated cookie copy only if spotDL/YouTube explicitly asks for auth.
        if proc.returncode != 0 and _authish(combined):
            cookie_file = _private_cookie_copy()
            if cookie_file:
                try:
                    retry_cmd = base_cmd + ["--cookie-file", cookie_file]
                    proc = subprocess.run(retry_cmd, cwd=workdir, capture_output=True, text=True, timeout=3300, check=False)
                    combined = (proc.stderr or "") + "\n" + (proc.stdout or "")
                finally:
                    try:
                        os.remove(cookie_file)
                    except OSError:
                        pass

        if proc.returncode != 0:
            raise RuntimeError(combined[-1800:] or "spotDL a échoué")
        if core.is_cancelled(job_id):
            raise InterruptedError("Job annulé")

        files = core.media_files(workdir)
        if not files:
            raise RuntimeError("Aucun fichier Spotify généré.")
        core.update_job(job_id, stage="packaging", progress=90, message="Préparation du téléchargement")

        if len(files) == 1:
            final_file = files[0]
            filename = os.path.basename(final_file)
        else:
            archive_base = os.path.join(workdir, "spotify-download")
            final_file = shutil.make_archive(archive_base, "zip", workdir)
            filename = "spotify-download.zip"

        core.update_job(
            job_id,
            status="ready",
            stage="ready",
            progress=100,
            message="Prêt à télécharger",
            result_path=final_file,
            filename=filename,
            media_type=mimetypes.guess_type(final_file)[0] or "application/octet-stream",
            result_size=os.path.getsize(final_file),
        )
    except InterruptedError:
        shutil.rmtree(workdir, ignore_errors=True)
        core.update_job(job_id, status="cancelled", stage="cancelled", progress=0, message="Job annulé", result_path=None)
    except Exception as exc:
        core.update_job(job_id, status="error", stage="error", progress=0, message=core.friendly_error(exc), error=core.friendly_error(exc))


def _run_original_worker(job_id: str, payload: dict, use_cookies: bool):
    def run():
        return _original_worker(job_id, payload)
    return _with_auth_mode(use_cookies, run)


def worker(job_id: str, payload: dict):
    try:
        platform = core.detect_platform(core.validate_public_url(payload["url"]))
    except Exception:
        return _run_original_worker(job_id, payload, False)

    if platform == "spotify":
        return spotify_worker(job_id, payload)

    # First run is deliberately cookie-free. An expired cookie therefore cannot break
    # normal/public media. Account cookies are a fallback only for auth-required YouTube.
    _run_original_worker(job_id, payload, False)
    job = core.get_job(job_id) or {}
    error_text = str(job.get("error") or job.get("message") or "")
    if platform == "youtube" and job.get("status") == "error" and _authish(error_text):
        core.update_job(job_id, status="queued", stage="auth_retry", progress=1, message="Nouvelle tentative avec session YouTube", error=None)
        _run_original_worker(job_id, payload, True)
        job = core.get_job(job_id) or {}
        error_text = str(job.get("error") or job.get("message") or "")
        if job.get("status") == "error" and _authish(error_text):
            core.update_job(
                job_id,
                message="Cette vidéo nécessite une session YouTube valide. Le reste du site continue de fonctionner sans cookies.",
                error="Cette vidéo nécessite une session YouTube valide. Le reste du site continue de fonctionner sans cookies.",
            )


# create_job resolves the module global named `worker` at request time.
core.worker = worker

# -------------------------
# FULL V6 INTERFACE RESTORE
# -------------------------
# Keep the complete controls visible even before an analysis succeeds. Only the media
# result card/track editor depend on analysis data.
core.HTML = core.HTML.replace(".workspace{display:none}", ".workspace{display:block}")
core.HTML = core.HTML.replace('<div class="workspace" id="workspace">', '<div class="workspace show" id="workspace">')
core.HTML = core.HTML.replace("<b>PANDA</b>.DOWNLOAD</div><div class=\"online\">V6 JOB ENGINE", "<b>PANDA</b>.DOWNLOAD</div><div class=\"online\">V6.2 STABLE")

# Restore audio quality module visibility logic.
core.HTML = core.HTML.replace(
    "videoQualityField=$('#videoQualityField'),videoFormatField=$('#videoFormatField')",
    "videoQualityField=$('#videoQualityField'),audioQualityField=$('#audioQualityField'),videoFormatField=$('#videoFormatField')",
)
core.HTML = core.HTML.replace(
    "videoQualityField.style.display=mode==='video'?'block':'none';videoFormatField.style.display=mode==='video'?'block':'none';audioFormatField.style.display=mode==='audio'?'block':'none';",
    "videoQualityField.style.display=mode==='video'?'block':'none';audioQualityField.style.display=mode==='audio'?'block':'none';videoFormatField.style.display=mode==='video'?'block':'none';audioFormatField.style.display=mode==='audio'?'block':'none';",
)

# Keep a ready job id so the final file transfer happens inside the current page.
core.HTML = core.HTML.replace(
    "let mode='video',chapters=[],qualitySizes={},activeJob=null,pollTimer=null;",
    "let mode='video',chapters=[],qualitySizes={},activeJob=null,pollTimer=null,readyJob=null,readyFilename='download',readySize=0;",
)
core.HTML = core.HTML.replace(
    "if(d.status==='ready'){downloadReady.href=`/jobs/${activeJob}/download`;downloadReady.classList.add('show');downloadReady.textContent=`TÉLÉCHARGER ${d.filename||''} ↓`;cancelJob.style.display='none';startJob.disabled=false;return}",
    "if(d.status==='ready'){readyJob=activeJob;readyFilename=d.filename||'download';readySize=d.result_size||0;downloadReady.href='#';downloadReady.classList.add('show');downloadReady.textContent=`TÉLÉCHARGER ${readyFilename} ↓`;cancelJob.style.display='none';startJob.disabled=false;activeJob=null;return}",
)

DOWNLOAD_JS = r'''
async function downloadInPage(e){
  e.preventDefault();
  if(!readyJob)return;
  const endpoint=`/jobs/${readyJob}/download`;
  downloadReady.style.pointerEvents='none';
  jobbox.classList.add('show');
  jobstage.textContent='TÉLÉCHARGEMENT';
  jobmsg.textContent='Connexion au fichier…';
  jobpct.textContent='0%';progressbar.style.width='0%';
  try{
    const r=await fetch(endpoint);
    if(!r.ok){let m='Téléchargement impossible';try{const d=await r.json();m=d.detail||d.error||m}catch{}throw new Error(m)}
    const total=Number(r.headers.get('content-length'))||readySize||0;
    const type=r.headers.get('content-type')||'application/octet-stream';
    const reader=r.body&&r.body.getReader?r.body.getReader():null;
    if(!reader){
      const a=document.createElement('a');a.href=endpoint;a.download=readyFilename;document.body.appendChild(a);a.click();a.remove();
      jobpct.textContent='100%';progressbar.style.width='100%';jobmsg.textContent='Téléchargement confié au navigateur';return;
    }

    let received=0;
    const update=()=>{const p=total?Math.min(100,Math.round(received/total*100)):0;jobpct.textContent=total?`${p}%`:`${bytes(received)}`;progressbar.style.width=total?`${p}%`:'35%';jobmsg.textContent=total?`${bytes(received)} / ${bytes(total)}`:`${bytes(received)} reçus`;};

    // Chromium desktop: stream large files directly to disk, avoiding a huge RAM blob.
    if(total>256*1024*1024 && window.showSaveFilePicker){
      const handle=await window.showSaveFilePicker({suggestedName:readyFilename});
      const writable=await handle.createWritable();
      while(true){const {done,value}=await reader.read();if(done)break;await writable.write(value);received+=value.byteLength;update()}
      await writable.close();
    }else if(total>512*1024*1024){
      // On browsers without the File System Access API, protect mobile/browser memory.
      try{await reader.cancel()}catch{}
      const a=document.createElement('a');a.href=endpoint;a.download=readyFilename;document.body.appendChild(a);a.click();a.remove();
      received=total;update();
    }else{
      const chunks=[];
      while(true){const {done,value}=await reader.read();if(done)break;chunks.push(value);received+=value.byteLength;update()}
      const blob=new Blob(chunks,{type});
      const objectUrl=URL.createObjectURL(blob);
      const a=document.createElement('a');a.href=objectUrl;a.download=readyFilename;document.body.appendChild(a);a.click();a.remove();
      setTimeout(()=>URL.revokeObjectURL(objectUrl),60000);
    }
    jobstage.textContent='TERMINÉ';jobmsg.textContent=`${readyFilename} · téléchargement terminé`;jobpct.textContent='100%';progressbar.style.width='100%';
  }catch(err){
    if(err&&err.name==='AbortError'){jobstage.textContent='ANNULÉ';jobmsg.textContent='Enregistrement annulé';}
    else{error.textContent=err.message||'Téléchargement impossible';error.style.display='block';jobstage.textContent='ERREUR';jobmsg.textContent=err.message||'';}
  }finally{downloadReady.style.pointerEvents='auto'}
}
downloadReady.addEventListener('click',downloadInPage);
'''
core.HTML = core.HTML.replace(
    "cancelJob.onclick=async()=>{if(!activeJob)return;await fetch(`/jobs/${activeJob}/cancel`,{method:'POST'});};\nsetMode('video');",
    "cancelJob.onclick=async()=>{if(!activeJob)return;await fetch(`/jobs/${activeJob}/cancel`,{method:'POST'});};\n" + DOWNLOAD_JS + "\nsetMode('video');",
)
