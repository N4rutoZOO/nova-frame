import json
import mimetypes
import os
import shutil
import subprocess
import time
import uuid
import zipfile
from urllib.parse import parse_qs, urlparse

from fastapi import Form
from fastapi.responses import JSONResponse

import app_v6_cli_runtime as cli

core = cli.core
stable = cli.stable
app = cli.app
VERSION = "6.5-playlists"
PLAYLIST_LIMIT = 100


def _playlist_id(url: str):
    try:
        return (parse_qs(urlparse(url).query).get("list") or [None])[0]
    except Exception:
        return None


def _is_playlist_url(url: str):
    return bool(_playlist_id(url))


def _playlist_json(url: str, use_cookies=False):
    cmd = [
        *cli.YTDLP,
        "--flat-playlist",
        "--dump-single-json",
        "--no-warnings",
        "--playlist-end", str(PLAYLIST_LIMIT),
        url,
    ]
    cookie = None
    if use_cookies:
        cookie = stable._private_cookie_copy()
        if cookie:
            cmd[-1:-1] = ["--cookies", cookie]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "Analyse playlist impossible")[-3000:])
        return json.loads(proc.stdout)
    finally:
        if cookie:
            try:
                os.remove(cookie)
            except OSError:
                pass


def _analyse_playlist(clean: str):
    try:
        data = _playlist_json(clean, False)
    except Exception as public_exc:
        if not stable._authish(str(public_exc)):
            raise
        data = _playlist_json(clean, True)

    entries = []
    for idx, item in enumerate(data.get("entries") or []):
        if not item:
            continue
        video_id = item.get("id")
        webpage = item.get("webpage_url") or item.get("url")
        if video_id and (not webpage or not str(webpage).startswith("http")):
            webpage = f"https://www.youtube.com/watch?v={video_id}"
        title = item.get("title") or f"Vidéo {idx + 1}"
        duration = item.get("duration")
        thumbnail = item.get("thumbnail") or ""
        entries.append({
            "index": idx,
            "playlist_index": idx + 1,
            "id": video_id or "",
            "title": title,
            "duration": duration,
            "thumbnail": thumbnail,
            "url": webpage or "",
            "uploader": item.get("uploader") or item.get("channel") or "",
        })

    if not entries:
        raise RuntimeError("Aucune vidéo détectée dans cette playlist.")

    total_duration = sum(float(x.get("duration") or 0) for x in entries)
    first = entries[0]
    pid = _playlist_id(clean) or ""
    is_radio = pid.startswith("RD")
    return {
        "platform": "youtube-playlist",
        "title": data.get("title") or ("YouTube Mix" if is_radio else "Playlist YouTube"),
        "uploader": data.get("uploader") or data.get("channel") or "YouTube",
        "thumbnail": first.get("thumbnail") or "",
        "duration": total_duration or None,
        "qualities": [2160, 1440, 1080, 720, 480, 360, 240, 144],
        "quality_sizes": {},
        "audio_only": False,
        "blocked": False,
        "chapters": [],
        "is_playlist": True,
        "playlist_id": pid,
        "playlist_radio": is_radio,
        "playlist_limited": len(entries) >= PLAYLIST_LIMIT,
        "playlist_entries": entries,
    }


stable._remove_route("/info", "POST")


@app.post("/info")
def media_info(url: str = Form(...)):
    try:
        clean = core.validate_public_url(url)
        if core.detect_platform(clean) == "youtube" and _is_playlist_url(clean):
            return _analyse_playlist(clean)
        return stable.resilient_media_info(url)
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": core.friendly_error(exc)})


def _parse_indices(raw):
    result = []
    try:
        values = json.loads(raw or "[]")
    except Exception:
        values = []
    if not isinstance(values, list):
        return []
    for value in values:
        try:
            value = int(value)
        except Exception:
            continue
        if 0 <= value < PLAYLIST_LIMIT:
            result.append(value)
    return sorted(set(result))


stable._remove_route("/jobs", "POST")


@app.post("/jobs")
def create_job(
    url: str = Form(...),
    mode: str = Form("video"),
    quality: str = Form("best"),
    audio_quality: str = Form("best"),
    video_format: str = Form("mp4"),
    audio_format: str = Form("original"),
    split_tracks: str = Form("0"),
    selected_tracks: str = Form("[]"),
    track_titles: str = Form("{}"),
    playlist_mode: str = Form("0"),
    selected_playlist: str = Form("[]"),
):
    core.cleanup_jobs()
    try:
        clean = core.validate_public_url(url)
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": core.friendly_error(exc)})

    selected = []
    for value in core.parse_json_list(selected_tracks):
        try:
            selected.append(int(value))
        except Exception:
            pass

    playlist_selected = _parse_indices(selected_playlist)
    use_playlist = playlist_mode == "1" and _is_playlist_url(clean)
    if use_playlist and not playlist_selected:
        return JSONResponse(status_code=400, content={"error": "Sélectionne au moins une vidéo de la playlist."})

    payload = {
        "url": clean,
        "mode": mode,
        "quality": quality,
        "audio_quality": audio_quality,
        "video_format": video_format,
        "audio_format": audio_format,
        "split_tracks": split_tracks == "1",
        "selected_tracks": selected,
        "track_titles": core.parse_json_dict(track_titles),
        "playlist_mode": use_playlist,
        "selected_playlist": playlist_selected,
    }

    job_id = uuid.uuid4().hex
    now = time.time()
    with core.JOB_LOCK:
        core.JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "message": "En attente",
            "created": now,
            "updated": now,
            "filename": None,
            "result_path": None,
            "result_size": None,
            "error": None,
            "cancel_requested": False,
            "workdir": None,
        }
    core.EXECUTOR.submit(core.worker, job_id, payload)
    return {"job_id": job_id, "status": "queued"}


def _playlist_selector(quality):
    if quality == "best":
        return "bv*+ba/b"
    try:
        height = max(144, min(int(quality), 4320))
    except (TypeError, ValueError):
        height = 1080
    return f"bv*[height<={height}]+ba/b[height<={height}]"


def _run_playlist_download(job_id, payload, workdir, use_cookies=False):
    selected = payload.get("selected_playlist") or []
    selected_one_based = [x + 1 for x in selected]
    selected_map = {value: pos for pos, value in enumerate(selected_one_based, 1)}
    total_selected = len(selected_one_based)
    quality = payload.get("quality") or "best"
    video_format = payload.get("video_format") if payload.get("video_format") in core.VIDEO_FORMATS else "mp4"
    merge_format = video_format if video_format in {"mp4", "mkv", "webm"} else "mp4"

    cmd = [
        *cli.YTDLP,
        "--newline",
        "--progress",
        "--yes-playlist",
        "--playlist-items", ",".join(str(x) for x in selected_one_based),
        "--retries", "5",
        "--fragment-retries", "5",
        "--embed-metadata",
        "--write-info-json",
        "--windows-filenames",
        "--trim-filenames", "140",
        "--progress-template",
        "download:PANDAPL|%(info.playlist_index)s|%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
        "-f", _playlist_selector(quality),
        "--merge-output-format", merge_format,
        "-o", os.path.join(workdir, "%(playlist_index)03d - %(title)s.%(ext)s"),
    ]

    cookie = None
    if use_cookies:
        cookie = stable._private_cookie_copy()
        if cookie:
            cmd += ["--cookies", cookie]
    cmd.append(payload["url"])

    proc = subprocess.Popen(cmd, cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    tail = []
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
                if line.startswith("PANDAPL|"):
                    parts = line.split("|", 5)
                    try:
                        original_index = int(parts[1])
                    except Exception:
                        original_index = selected_one_based[0]
                    file_pct = cli._progress_value(parts[2] if len(parts) > 2 else "")
                    position = selected_map.get(original_index, 1)
                    overall = ((position - 1) + file_pct / 100.0) / max(1, total_selected)
                    speed = (parts[3] if len(parts) > 3 else "").strip()
                    eta = (parts[4] if len(parts) > 4 else "").strip()
                    msg = f"Vidéo {position}/{total_selected} · {file_pct:.0f}%"
                    if speed and speed not in {"N/A", "Unknown"}:
                        msg += f" · {speed}"
                    if eta and eta not in {"N/A", "Unknown"}:
                        msg += f" · ETA {eta}"
                    core.update_job(job_id, stage="downloading", progress=7 + int(overall * 72), message=msg, item_current=position, item_total=total_selected)

            if proc.poll() is not None:
                if proc.stdout:
                    rest = proc.stdout.read()
                    if rest:
                        tail.extend(rest.splitlines()[-50:])
                break
            if not line:
                time.sleep(.05)

        if proc.returncode != 0:
            raise RuntimeError("\n".join(tail[-50:])[-5000:] or "yt-dlp playlist a échoué.")
    finally:
        if proc.poll() is None:
            cli._terminate(proc)
        if cookie:
            try:
                os.remove(cookie)
            except OSError:
                pass


def _playlist_files(workdir):
    return sorted([
        p for p in core.media_files(workdir)
        if not p.lower().endswith(".zip")
    ])


def playlist_worker(job_id, payload):
    workdir = core.tempfile.mkdtemp(prefix=f"panda_playlist_{job_id[:8]}_")
    core.update_job(job_id, workdir=workdir, status="running", stage="preparing", progress=2, message="Préparation playlist yt-dlp")
    try:
        if payload.get("mode") != "video":
            raise RuntimeError("Le Playlist Editor est actuellement prévu pour les vidéos. Passe en mode VIDÉO.")

        core.update_job(job_id, stage="downloading", progress=5, message="yt-dlp · téléchargement de la sélection")
        try:
            _run_playlist_download(job_id, payload, workdir, False)
        except InterruptedError:
            raise
        except Exception as public_exc:
            if not stable._authish(str(public_exc)):
                raise
            core.update_job(job_id, stage="auth_retry", progress=4, message="YouTube demande une session · nouvelle tentative")
            _run_playlist_download(job_id, payload, workdir, True)

        files = _playlist_files(workdir)
        if not files:
            raise RuntimeError("Aucune vidéo de la playlist n’a été générée.")

        wanted_format = payload.get("video_format") if payload.get("video_format") in core.VIDEO_FORMATS else "mp4"
        final_files = []
        total = len(files)
        for pos, source in enumerate(files, 1):
            if core.is_cancelled(job_id):
                raise InterruptedError("Job annulé")
            current_ext = os.path.splitext(source)[1].lower().lstrip(".")
            if current_ext == wanted_format:
                final_files.append(source)
            elif wanted_format in {"mov", "avi"}:
                core.update_job(job_id, stage="converting", progress=80 + int(pos / total * 10), message=f"Conversion {pos}/{total} · {wanted_format.upper()}")
                final_files.append(core.convert_video(source, wanted_format, workdir))
            else:
                final_files.append(source)

        core.update_job(job_id, stage="packaging", progress=92, message=f"Création du ZIP · {len(final_files)} vidéos")
        zip_path = os.path.join(workdir, "playlist-videos.zip")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for path in final_files:
                archive.write(path, arcname=os.path.basename(path))

        size = os.path.getsize(zip_path)
        core.update_job(job_id, status="ready", stage="ready", progress=100, message=f"Playlist prête · {len(final_files)} vidéos", result_path=zip_path, filename="playlist-videos.zip", media_type="application/zip", result_size=size)

    except InterruptedError:
        shutil.rmtree(workdir, ignore_errors=True)
        core.update_job(job_id, status="cancelled", stage="cancelled", progress=0, message="Job annulé", result_path=None, workdir=None)
    except Exception as exc:
        msg = core.friendly_error(exc)
        shutil.rmtree(workdir, ignore_errors=True)
        core.update_job(job_id, status="error", stage="error", progress=0, message=msg, error=msg, result_path=None, workdir=None)


_original_cli_worker = cli.cli_worker


def worker(job_id, payload):
    if payload.get("playlist_mode"):
        return playlist_worker(job_id, payload)
    return _original_cli_worker(job_id, payload)


core.worker = worker

PLAYLIST_HTML = r'''
<section class="playlist-editor" id="playlistEditor">
  <div class="editor-head">
    <div><div class="editor-title">PLAYLIST EDITOR</div><div class="track-count" id="playlistCount"></div></div>
    <div class="editor-tools">
      <button class="smallbtn" id="allPlaylist" type="button">TOUT</button>
      <button class="smallbtn" id="nonePlaylist" type="button">AUCUN</button>
      <button class="smallbtn" id="invertPlaylist" type="button">INVERSER</button>
    </div>
  </div>
  <div class="playlist-list" id="playlistList"></div>
</section>
'''

PLAYLIST_CSS = r'''
.playlist-editor{display:none;margin-top:12px;border-radius:19px;padding:14px;background:linear-gradient(155deg,#2a303c,#202631);border:1px solid #404756}.playlist-editor.show{display:block}.playlist-list{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:10px}.playlist-item{display:grid;grid-template-columns:20px 58px 34px minmax(0,1fr) auto;gap:8px;align-items:center;padding:8px;border-radius:11px;background:#151a23;border:1px solid #303744}.playlist-item input{accent-color:#75a8ff}.playlist-thumb{width:58px;aspect-ratio:16/9;border-radius:7px;object-fit:cover;background:#232a36}.playlist-item b{font-size:9px;color:#8db9ff}.playlist-name{min-width:0;font-size:10px;font-weight:750;color:#c8ced8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.playlist-time{font-size:9px;color:#697282}.playlist-item.off{opacity:.48}@media(max-width:900px){.playlist-list{grid-template-columns:1fr}}@media(max-width:560px){.playlist-editor{border-radius:15px;padding:11px}.playlist-item{grid-template-columns:22px 70px 30px minmax(0,1fr);padding:9px}.playlist-time{grid-column:4}.playlist-thumb{width:70px}}
'''

core.HTML = core.HTML.replace("</style></head>", PLAYLIST_CSS + "</style></head>")
core.HTML = core.HTML.replace('<section class="jobbox" id="jobbox">', PLAYLIST_HTML + '<section class="jobbox" id="jobbox">')
core.HTML = core.HTML.replace("let mode='video',chapters=[],qualitySizes={},activeJob=null,pollTimer=null,readyJob=null,readyFilename='download',readySize=0;", "let mode='video',chapters=[],qualitySizes={},activeJob=null,pollTimer=null,readyJob=null,readyFilename='download',readySize=0,playlistEntries=[],isPlaylist=false;")
core.HTML = core.HTML.replace("function fmt(s){", "const playlistEditor=$('#playlistEditor'),playlistList=$('#playlistList'),playlistCount=$('#playlistCount');\nfunction fmt(s){")
core.HTML = core.HTML.replace("trackEditor.classList.toggle('show',mode==='audio'&&chapters.length>0);if(mode==='video')splitTracks.checked=false;refreshSummary()}", "trackEditor.classList.toggle('show',mode==='audio'&&!isPlaylist&&chapters.length>0);playlistEditor.classList.toggle('show',mode==='video'&&isPlaylist);if(mode==='video')splitTracks.checked=false;refreshSummary()}")

PLAYLIST_JS = r'''
function selectedPlaylistIndices(){return [...document.querySelectorAll('.playlistcheck')].map((x,i)=>x.checked?i:null).filter(x=>x!==null)}
function updatePlaylistCount(){if(!isPlaylist){playlistCount.textContent='';return}const n=selectedPlaylistIndices().length;playlistCount.textContent=`${n} / ${playlistEntries.length} vidéos sélectionnées`;document.querySelectorAll('.playlist-item').forEach((row,i)=>row.classList.toggle('off',!document.querySelectorAll('.playlistcheck')[i].checked))}
function renderPlaylist(items){playlistEntries=items||[];playlistList.innerHTML='';playlistEntries.forEach((p,i)=>{const row=document.createElement('label');row.className='playlist-item';const checked=document.createElement('input');checked.type='checkbox';checked.className='playlistcheck';checked.checked=true;checked.onchange=updatePlaylistCount;const img=document.createElement('img');img.className='playlist-thumb';img.loading='lazy';img.src=p.thumbnail||'';const no=document.createElement('b');no.textContent=String(i+1).padStart(2,'0');const name=document.createElement('div');name.className='playlist-name';name.textContent=p.title||`Vidéo ${i+1}`;name.title=name.textContent;const t=document.createElement('div');t.className='playlist-time';t.textContent=fmt(p.duration);row.append(checked,img,no,name,t);playlistList.appendChild(row)});updatePlaylistCount()}
function choosePlaylist(state){document.querySelectorAll('.playlistcheck').forEach(x=>x.checked=state);updatePlaylistCount()}
$('#allPlaylist').onclick=()=>choosePlaylist(true);$('#nonePlaylist').onclick=()=>choosePlaylist(false);$('#invertPlaylist').onclick=()=>{document.querySelectorAll('.playlistcheck').forEach(x=>x.checked=!x.checked);updatePlaylistCount()};
'''
core.HTML = core.HTML.replace("async function analyseUrl(){", PLAYLIST_JS + "\nasync function analyseUrl(){")
core.HTML = core.HTML.replace("chapters=d.chapters||[];qualitySizes=d.quality_sizes||{};fillQualities(d.qualities);renderTracks(chapters);", "isPlaylist=!!d.is_playlist;chapters=isPlaylist?[]:(d.chapters||[]);playlistEntries=d.playlist_entries||[];qualitySizes=d.quality_sizes||{};fillQualities(d.qualities);renderTracks(chapters);renderPlaylist(playlistEntries);")
core.HTML = core.HTML.replace("$('#chapterStat').textContent=chapters.length?`${chapters.length} chapitres`:'Pas de chapitres';", "$('#chapterStat').textContent=isPlaylist?`${playlistEntries.length} vidéos`:(chapters.length?`${chapters.length} chapitres`:'Pas de chapitres');")
core.HTML = core.HTML.replace("setMode(d.audio_only?'audio':'video');saveHistory", "setMode(isPlaylist?'video':(d.audio_only?'audio':'video'));saveHistory")
core.HTML = core.HTML.replace("async function createJob(){if(activeJob)return;const selected=selectedIndices();", "async function createJob(){if(activeJob)return;const selected=selectedIndices();const selectedPlaylist=selectedPlaylistIndices();if(isPlaylist&&mode==='video'&&!selectedPlaylist.length){error.textContent='Sélectionne au moins une vidéo de la playlist.';error.style.display='block';return}")
core.HTML = core.HTML.replace("f.append('track_titles',JSON.stringify(titles));startJob.disabled=true;", "f.append('track_titles',JSON.stringify(titles));f.append('playlist_mode',isPlaylist?'1':'0');f.append('selected_playlist',JSON.stringify(selectedPlaylist));startJob.disabled=true;")
core.HTML = core.HTML.replace("V6.4 · YT-DLP CORE", "V6.5 · PLAYLISTS")

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
        "playlist_editor": True,
        "playlist_limit": PLAYLIST_LIMIT,
        "youtube_cookies": bool(core.cookie_path()),
        "deno": shutil.which("deno") is not None,
        "ffmpeg": shutil.which("ffmpeg") is not None,
    }
