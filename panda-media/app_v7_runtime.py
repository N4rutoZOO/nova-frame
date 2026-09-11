from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from urllib.parse import quote
import mimetypes
import os
import re
import shutil
import subprocess
import time
import zipfile

import yt_dlp
import app_v6 as core

app = core.app
VERSION = "7.0"
FRAGMENTS = max(1, min(int(os.getenv("PANDA_FRAGMENT_CONCURRENCY", "6")), 12))
MAX_PENDING = max(2, min(int(os.getenv("PANDA_MAX_PENDING_JOBS", "6")), 20))
SPOTDL_THREADS = max(1, min(int(os.getenv("PANDA_SPOTDL_THREADS", "2")), 4))
MAX_MEDIA_BYTES = max(512 * 1024 * 1024, int(os.getenv("PANDA_MAX_MEDIA_BYTES", str(5 * 1024**3))))
DOWNLOAD_CHUNK = max(256 * 1024, min(int(os.getenv("PANDA_DOWNLOAD_CHUNK", str(2 * 1024 * 1024))), 8 * 1024 * 1024))
FFMPEG_PRESET = os.getenv("PANDA_FFMPEG_PRESET", "veryfast")

_original_base = core.base_ydl_options
_original_friendly = core.friendly_error


def human_bytes(value):
    if not isinstance(value, (int, float)) or value <= 0:
        return ""
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024


def fast_base_ydl_options():
    opts = _original_base()
    opts.update({
        "retries": 5,
        "fragment_retries": 10,
        "extractor_retries": 3,
        "socket_timeout": 25,
        "continuedl": True,
        "concurrent_fragment_downloads": FRAGMENTS,
        "buffersize": 1024 * 1024,
        "js_runtimes": {"deno": {}},
        "remote_components": {"ejs:github"},
    })
    headers = dict(opts.get("http_headers") or {})
    headers.setdefault("Accept-Language", "fr-FR,fr;q=0.9,en;q=0.7")
    opts["http_headers"] = headers
    return opts


core.base_ydl_options = fast_base_ydl_options


def friendly_error(exc):
    text = str(exc)
    low = text.lower()
    if "no space left" in low or "cannot allocate memory" in low or "out of memory" in low:
        return "Le serveur manque temporairement de mémoire pour ce média. Essaie une qualité plus basse ou un fichier plus court."
    if "timed out" in low or "timeout" in low:
        return "La source a mis trop de temps à répondre. Relance le job : le moteur réessaie automatiquement plusieurs fois."
    if "429" in low or "too many requests" in low:
        return "La plateforme limite temporairement les requêtes. Attends un peu puis réessaie."
    return _original_friendly(exc)


core.friendly_error = friendly_error


def is_cancelled(job_id):
    return core.is_cancelled(job_id)


def run_cancellable(job_id, cmd, cwd=None, timeout=3300):
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    started = time.time()
    while proc.poll() is None:
        if is_cancelled(job_id):
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise InterruptedError("Job annulé")
        if time.time() - started > timeout:
            proc.kill()
            raise RuntimeError("Le traitement a dépassé le délai maximal.")
        time.sleep(.25)
    out, err = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError((err or out or "Traitement externe en échec")[-2000:])
    return out


def choose_selector(mode, quality, video_format, audio_format):
    if mode == "audio":
        if audio_format == "m4a":
            return "ba[ext=m4a]/ba/b"
        if audio_format in {"opus", "ogg"}:
            return "ba[acodec^=opus]/ba/b"
        return "bestaudio/best"
    suffix = "" if quality == "best" else f"[height<={max(144, min(int(quality), 4320))}]"
    if video_format in {"mp4", "mov"}:
        return f"bv*[ext=mp4]{suffix}+ba[ext=m4a]/bv*{suffix}+ba/b{suffix}"
    if video_format == "webm":
        return f"bv*[ext=webm]{suffix}+ba[acodec^=opus]/bv*{suffix}+ba/b{suffix}"
    return f"bv*{suffix}+ba/b{suffix}"


def merge_container(video_format):
    if video_format == "webm":
        return "webm"
    if video_format in {"mp4", "mov"}:
        return "mp4"
    return "mkv"


def ffmpeg_audio(job_id, src, out, fmt, bitrate, title=None, track=None, album=None, artist=None, start=None, length=None):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if start is not None:
        cmd += ["-ss", f"{float(start):.3f}"]
    cmd += ["-i", src]
    if length is not None:
        cmd += ["-t", f"{float(length):.3f}"]
    cmd += ["-map", "0:a:0", "-vn"]
    br = None if bitrate == "best" else f"{bitrate}k"
    if fmt == "mp3": cmd += ["-c:a", "libmp3lame", "-b:a", br or "320k"]
    elif fmt == "m4a": cmd += ["-c:a", "aac", "-b:a", br or "256k", "-movflags", "+faststart"]
    elif fmt == "opus": cmd += ["-c:a", "libopus", "-b:a", br or "192k"]
    elif fmt == "ogg": cmd += ["-c:a", "libvorbis", "-b:a", br or "192k"]
    elif fmt == "flac": cmd += ["-c:a", "flac"]
    elif fmt == "wav": cmd += ["-c:a", "pcm_s16le"]
    else: cmd += ["-c:a", "copy"]
    if title: cmd += ["-metadata", f"title={title}"]
    if track is not None: cmd += ["-metadata", f"track={track}"]
    if album: cmd += ["-metadata", f"album={album}"]
    if artist: cmd += ["-metadata", f"artist={artist}"]
    cmd.append(out)
    run_cancellable(job_id, cmd)


def convert_audio(job_id, source, fmt, bitrate, workdir, title="", artist=""):
    if fmt == "original":
        return source
    current = os.path.splitext(source)[1].lower().lstrip(".")
    same = current == fmt or (fmt == "m4a" and current in {"m4a", "mp4"})
    if same and bitrate == "best":
        return source
    out = os.path.join(workdir, f"audio-final.{fmt}")
    ffmpeg_audio(job_id, source, out, fmt, bitrate, title=title, artist=artist)
    return out


def convert_video(job_id, source, fmt, workdir):
    current = os.path.splitext(source)[1].lower().lstrip(".")
    if current == fmt:
        return source
    out = os.path.join(workdir, f"video-final.{fmt}")
    try:
        run_cancellable(job_id, ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", source, "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", out], timeout=900)
        return out
    except InterruptedError:
        raise
    except Exception:
        try: os.remove(out)
        except OSError: pass
    base = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", source, "-map", "0:v:0", "-map", "0:a:0?"]
    if fmt in {"mp4", "mov", "mkv"}:
        cmd = base + ["-c:v", "libx264", "-preset", FFMPEG_PRESET, "-crf", "20", "-c:a", "aac", "-b:a", "192k"]
        if fmt in {"mp4", "mov"}: cmd += ["-movflags", "+faststart"]
    elif fmt == "webm":
        cmd = base + ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-crf", "31", "-b:v", "0", "-c:a", "libopus", "-b:a", "160k"]
    else:
        cmd = base + ["-c:v", "mpeg4", "-q:v", "3", "-c:a", "libmp3lame", "-b:a", "192k"]
    cmd.append(out)
    run_cancellable(job_id, cmd)
    return out


def build_tracks_zip(job_id, source, info, selected, custom_titles, audio_format, audio_quality, workdir):
    chapters = core.extract_chapters(info)
    selected = sorted({i for i in selected if isinstance(i, int) and 0 <= i < len(chapters)})
    if not chapters:
        raise RuntimeError("Aucun chapitre ou timestamp exploitable n’a été détecté.")
    if not selected:
        raise RuntimeError("Sélectionne au moins une track.")
    source_ext = os.path.splitext(source)[1].lower().lstrip(".") or "m4a"
    fmt = audio_format
    if fmt == "original":
        fmt = source_ext if source_ext in {"m4a", "mp3", "opus", "ogg", "flac", "wav", "webm", "aac", "mka"} else "m4a"
    album = core.clean_title(info.get("title") or "Mix")
    artist = info.get("uploader") or info.get("channel") or info.get("artist") or ""
    archive_path = os.path.join(workdir, "tracks.zip")
    tracklist = []
    total = len(selected)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for pos, idx in enumerate(selected, 1):
            if is_cancelled(job_id): raise InterruptedError("Job annulé")
            chapter = chapters[idx]
            no = int(chapter.get("number") or idx + 1)
            title = core.clean_title(custom_titles.get(str(idx)) or chapter.get("title"), no)
            start = float(chapter["start"]); length = max(.05, float(chapter["end"]) - start)
            path = os.path.join(workdir, f"track-{no:03d}.{fmt}")
            if audio_format == "original" and fmt == source_ext:
                ffmpeg_audio(job_id, source, path, "copy", "best", title, no, album, artist, start, length)
            else:
                ffmpeg_audio(job_id, source, path, fmt, audio_quality, title, no, album, artist, start, length)
            archive.write(path, arcname=f"{no:02d} - {title}.{fmt}")
            try: os.remove(path)
            except OSError: pass
            tracklist.append(f"{no:02d}. {title}")
            core.update_job(job_id, stage="splitting", progress=72 + int(pos/total*23), message=f"Découpage {pos}/{total}", track_current=pos, track_total=total)
        archive.writestr("tracklist.txt", "\n".join(tracklist) + "\n")
    return archive_path


def spotify_worker(job_id, payload, workdir):
    if payload.get("split_tracks"):
        raise RuntimeError("Pour Spotify, les morceaux sont déjà séparés : désactive le découpage par chapitres.")
    fmt = payload.get("audio_format") if payload.get("audio_format") in core.AUDIO_FORMATS else "original"
    quality = payload.get("audio_quality") if payload.get("audio_quality") in core.AUDIO_BITRATES else "best"
    target = "opus" if fmt == "original" else fmt
    if target not in {"mp3", "m4a", "opus", "flac", "wav", "ogg"}: target = "opus"
    bitrate = "disable" if quality == "best" or target in {"flac", "wav"} else f"{quality}k"
    cmd = [os.sys.executable, "-m", "spotdl", "download", payload["url"], "--format", target, "--bitrate", bitrate, "--threads", str(SPOTDL_THREADS), "--output", os.path.join(workdir, "{list-position} - {artists} - {title}.{output-ext}")]
    cookies = core.cookie_path()
    if cookies: cmd += ["--cookie-file", cookies]
    core.update_job(job_id, stage="downloading", progress=16, message="Recherche Spotify / correspondances audio")
    run_cancellable(job_id, cmd, cwd=workdir)
    files = [p for p in core.media_files(workdir) if not p.endswith(".zip")]
    if not files: raise RuntimeError("Aucun fichier Spotify généré.")
    if len(files) == 1: return files[0], os.path.basename(files[0])
    core.update_job(job_id, stage="packaging", progress=92, message=f"Création ZIP · {len(files)} pistes")
    archive = os.path.join(workdir, "spotify-download.zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for p in files: zf.write(p, arcname=os.path.basename(p))
    return archive, "spotify-download.zip"


def worker(job_id, payload):
    workdir = core.tempfile.mkdtemp(prefix=f"panda_v7_{job_id[:8]}_")
    core.update_job(job_id, workdir=workdir, status="running", stage="preparing", progress=2, message="Préparation")
    try:
        clean = core.validate_public_url(payload["url"]); platform = core.detect_platform(clean)
        if platform in {"tidal", "deezer"}: raise RuntimeError("Cette source n’est pas disponible pour le téléchargement direct.")
        if platform == "spotify":
            final_file, filename = spotify_worker(job_id, payload, workdir)
        else:
            mode = "audio" if payload.get("mode") == "audio" else "video"
            quality = payload.get("quality") or "best"
            aq = payload.get("audio_quality") if payload.get("audio_quality") in core.AUDIO_BITRATES else "best"
            vf = payload.get("video_format") if payload.get("video_format") in core.VIDEO_FORMATS else "mp4"
            af = payload.get("audio_format") if payload.get("audio_format") in core.AUDIO_FORMATS else "original"
            split = bool(payload.get("split_tracks")) and mode == "audio"
            opts = fast_base_ydl_options(); opts.update({"outtmpl": os.path.join(workdir, "%(id)s.%(ext)s"), "restrictfilenames": True, "trim_file_name": 80, "windowsfilenames": True})
            try: opts["format"] = choose_selector(mode, quality, vf, af)
            except Exception: opts["format"] = choose_selector(mode, "best", vf, af)
            if mode == "video": opts["merge_output_format"] = merge_container(vf)
            def hook(d):
                if is_cancelled(job_id): raise InterruptedError("Job annulé")
                if d.get("status") == "downloading":
                    got=d.get("downloaded_bytes") or 0; total=d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    if total and total > MAX_MEDIA_BYTES: raise RuntimeError(f"Média trop volumineux ({human_bytes(total)} ; limite {human_bytes(MAX_MEDIA_BYTES)}).")
                    ratio=got/total if total else 0; speed=d.get("speed") or 0; eta=d.get("eta")
                    msg=f"Téléchargement {int(ratio*100)}%" if total else "Téléchargement"
                    if speed: msg += f" · {human_bytes(speed)}/s"
                    if isinstance(eta,(int,float)): msg += f" · ETA {int(eta)}s"
                    core.update_job(job_id, stage="downloading", progress=8+int(max(0,min(1,ratio))*52), message=msg, speed=speed, eta=eta)
                elif d.get("status") == "finished": core.update_job(job_id, stage="processing", progress=62, message="Téléchargement terminé")
            opts["progress_hooks"]=[hook]
            core.update_job(job_id, stage="downloading", progress=6, message=f"Connexion · {FRAGMENTS} fragments parallèles")
            with yt_dlp.YoutubeDL(opts) as ydl: info=ydl.extract_info(clean, download=True)
            if is_cancelled(job_id): raise InterruptedError("Job annulé")
            files=[p for p in core.media_files(workdir) if not p.endswith(".zip")]
            if not files: raise RuntimeError("Aucun fichier média final généré.")
            source=max(files,key=os.path.getsize); title=info.get("title") or info.get("id") or "download"; artist=info.get("uploader") or info.get("channel") or info.get("artist") or ""
            if mode == "video":
                core.update_job(job_id, stage="converting", progress=68, message=f"Optimisation {vf.upper()}"); final_file=convert_video(job_id, source, vf, workdir); filename=core.pretty_name(title, os.path.splitext(final_file)[1])
            elif split:
                core.update_job(job_id, stage="splitting", progress=70, message="Préparation des tracks"); final_file=build_tracks_zip(job_id, source, info, payload.get("selected_tracks") or [], payload.get("track_titles") or {}, af, aq, workdir); filename=core.pretty_name(f"{title} - tracks", ".zip")
            else:
                core.update_job(job_id, stage="converting", progress=72, message="Préparation audio"); final_file=convert_audio(job_id, source, af, aq, workdir, title, artist); filename=core.pretty_name(title, os.path.splitext(final_file)[1])
        size=os.path.getsize(final_file); core.update_job(job_id,status="ready",stage="ready",progress=100,message=f"Prêt · {human_bytes(size)}",result_path=final_file,filename=filename,media_type=mimetypes.guess_type(final_file)[0] or "application/octet-stream",result_size=size)
    except InterruptedError:
        shutil.rmtree(workdir,ignore_errors=True); core.update_job(job_id,status="cancelled",stage="cancelled",progress=0,message="Job annulé",result_path=None,workdir=None)
    except Exception as exc:
        msg=friendly_error(exc); shutil.rmtree(workdir,ignore_errors=True); core.update_job(job_id,status="error",stage="error",progress=0,message=msg,error=msg,result_path=None,workdir=None)


core.worker = worker

core.HTML = core.HTML.replace("PANDA</b>.DOWNLOAD · V6", "PANDA</b>.DOWNLOAD · V7")
core.HTML = core.HTML.replace(
    "videoQualityField=$('#videoQualityField'),videoFormatField=$('#videoFormatField')",
    "videoQualityField=$('#videoQualityField'),audioQualityField=$('#audioQualityField'),videoFormatField=$('#videoFormatField')",
)
core.HTML = core.HTML.replace(
    "videoQualityField.style.display=mode==='video'?'block':'none';videoFormatField.style.display=mode==='video'?'block':'none';audioFormatField.style.display=mode==='audio'?'block':'none';",
    "videoQualityField.style.display=mode==='video'?'block':'none';audioQualityField.style.display=mode==='audio'?'block':'none';videoFormatField.style.display=mode==='video'?'block':'none';audioFormatField.style.display=mode==='audio'?'block':'none';",
)
core.HTML = core.HTML.replace(
    "if(d.status==='ready'){downloadReady.href=`/jobs/${activeJob}/download`;downloadReady.classList.add('show');downloadReady.textContent=`TÉLÉCHARGER ${d.filename||''} ↓`;cancelJob.style.display='none';startJob.disabled=false;return}",
    "if(d.status==='ready'){const finishedJob=activeJob;downloadReady.href=`/jobs/${finishedJob}/download`;downloadReady.classList.add('show');downloadReady.textContent=`TÉLÉCHARGER ${d.filename||''} ↓`;cancelJob.style.display='none';startJob.disabled=false;activeJob=null;return}",
)
EXTRA_CSS = r'''
.urlbox:focus-within{border-color:#6b83ad;box-shadow:0 0 0 3px rgba(110,167,255,.08)}
.btn,.modebtn,.presetbtn,.smallbtn{touch-action:manipulation}
.jobbox{box-shadow:inset 0 1px 0 rgba(255,255,255,.04),0 14px 36px rgba(0,0,0,.2)}
.track input[type=text]:focus{color:#fff;background:#10151d;border-radius:5px}
@media(max-width:560px){.media-title{-webkit-line-clamp:2;display:-webkit-box;-webkit-box-orient:vertical;overflow:hidden}.jobactions .btn{flex:1}.editor-tools{width:100%}.editor-tools .smallbtn{flex:1}}
'''
EXTRA_JS = r'''
(()=>{
 const aq=document.querySelector('#audioQuality'), af=document.querySelector('#audioFormat'), online=document.querySelector('.online'), urlInput=document.querySelector('#url');
 function syncLossless(){if(!aq||!af)return;const loss=['flac','wav'].includes(af.value);aq.disabled=loss;aq.title=loss?'Le bitrate ne s’applique pas aux formats lossless':'';}
 if(af){af.addEventListener('change',syncLossless);syncLossless()}
 if(online){const net=()=>{online.textContent=navigator.onLine?'ENGINE ONLINE':'HORS LIGNE';online.style.color=navigator.onLine?'':'#ff919d'};window.addEventListener('online',net);window.addEventListener('offline',net);net()}
 if(urlInput && navigator.clipboard){urlInput.addEventListener('dblclick',async()=>{try{urlInput.value=await navigator.clipboard.readText()}catch(e){}})}
})();
'''
core.HTML = core.HTML.replace("</style>", EXTRA_CSS + "</style>").replace("</script>", EXTRA_JS + "</script>")

@app.middleware("http")
async def v7_queue_guard(request: Request, call_next):
    if request.method == "POST" and request.url.path == "/jobs":
        with core.JOB_LOCK:
            active = sum(1 for j in core.JOBS.values() if j.get("status") in {"queued", "running"})
        if active >= MAX_PENDING:
            return JSONResponse(status_code=429, content={"error":"Le serveur traite déjà plusieurs téléchargements. Réessaie dans quelques instants."})
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response

app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) not in {"/jobs/{job_id}/download", "/health"}]


def file_iter(path, start, end):
    with open(path,"rb") as f:
        f.seek(start); remaining=end-start+1
        while remaining>0:
            chunk=f.read(min(DOWNLOAD_CHUNK,remaining))
            if not chunk: break
            remaining-=len(chunk); yield chunk


@app.get("/jobs/{job_id}/download")
def ranged_download(job_id: str, request: Request):
    job=core.get_job(job_id)
    if not job: return JSONResponse(status_code=404,content={"error":"Job introuvable ou expiré"})
    if job.get("status")!="ready": return JSONResponse(status_code=409,content={"error":"Le fichier n’est pas encore prêt"})
    path=job.get("result_path")
    if not path or not os.path.isfile(path): return JSONResponse(status_code=410,content={"error":"Le fichier a expiré"})
    size=os.path.getsize(path); name=job.get("filename") or os.path.basename(path); media=job.get("media_type") or "application/octet-stream"
    headers={"Accept-Ranges":"bytes","Content-Disposition":f"attachment; filename*=UTF-8''{quote(name,safe='')}","Cache-Control":"private, no-store"}
    rh=request.headers.get("range")
    if rh:
        m=re.match(r"bytes=(\d*)-(\d*)$",rh.strip())
        if not m: return Response(status_code=416,headers={"Content-Range":f"bytes */{size}"})
        a,b=m.groups()
        if a=="" and b:
            length=min(int(b),size); start=size-length; end=size-1
        else:
            start=int(a or 0); end=int(b) if b else size-1
        if start<0 or end>=size or start>end: return Response(status_code=416,headers={"Content-Range":f"bytes */{size}"})
        headers.update({"Content-Range":f"bytes {start}-{end}/{size}","Content-Length":str(end-start+1)})
        return StreamingResponse(file_iter(path,start,end),status_code=206,media_type=media,headers=headers)
    headers["Content-Length"]=str(size)
    return StreamingResponse(file_iter(path,0,size-1),media_type=media,headers=headers)


@app.get("/health")
def v7_health():
    return {"status":"ok","service":"panda-download","ui":"plugin-v7","version":VERSION,"job_engine":True,"track_editor":True,"range_downloads":True,"fragment_concurrency":FRAGMENTS,"youtube_cookies":bool(core.cookie_path()),"deno":shutil.which("deno") is not None,"ffmpeg":shutil.which("ffmpeg") is not None}
