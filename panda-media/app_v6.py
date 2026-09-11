from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from urllib.request import Request, urlopen
from urllib.parse import urlparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
import zipfile

import yt_dlp

from app_v3 import (
    VIDEO_FORMATS,
    AUDIO_FORMATS,
    AUDIO_BITRATES,
    base_ydl_options,
    cookie_path,
    convert_audio,
    convert_video,
    detect_platform,
    friendly_error,
    media_files,
    pretty_name,
    validate_public_url,
)

app = FastAPI(title="panda.download.com")

JOBS = {}
JOB_LOCK = Lock()
EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="panda-job")
JOB_TTL = 7200


def clean_title(value: str, index: int = 0) -> str:
    value = (value or "").strip()
    value = re.sub(r"^\s*[\-–—•|]+\s*", "", value)
    value = re.sub(r"^\d{1,3}[.\-)\s]+", "", value)
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._-")
    fallback = f"Track {index:02d}" if index else "download"
    return (value[:90] or fallback)


def timestamp_seconds(h, m, s):
    return int(h or 0) * 3600 + int(m) * 60 + int(s)


def chapters_from_description(description: str, duration=None):
    points = []
    pattern = re.compile(r"^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s*(?:[-–—|:]\s*)?(.*)\s*$")
    for line in (description or "").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        sec = timestamp_seconds(match.group(1), match.group(2), match.group(3))
        title = (match.group(4) or "").strip()
        if points and sec <= points[-1][0]:
            continue
        points.append((sec, title))
    if len(points) < 2:
        return []
    result = []
    for idx, (start, title) in enumerate(points, 1):
        end = points[idx][0] if idx < len(points) else duration
        if end is None or end <= start:
            continue
        result.append({
            "number": idx,
            "title": clean_title(title, idx),
            "start": float(start),
            "end": float(end),
            "duration": float(end - start),
        })
    return result


def extract_chapters(info: dict):
    result = []
    for idx, chapter in enumerate(info.get("chapters") or [], 1):
        try:
            start = float(chapter.get("start_time"))
            end = float(chapter.get("end_time"))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        result.append({
            "number": idx,
            "title": clean_title(chapter.get("title"), idx),
            "start": start,
            "end": end,
            "duration": end - start,
        })
    return result or chapters_from_description(info.get("description") or "", info.get("duration"))


def quality_sizes(info: dict):
    audio_sizes = [f.get("filesize") or f.get("filesize_approx") for f in info.get("formats", []) if f.get("acodec") != "none" and f.get("vcodec") == "none"]
    audio_size = max([x for x in audio_sizes if isinstance(x, (int, float))] or [0])
    sizes = {}
    for fmt in info.get("formats", []):
        height = fmt.get("height")
        if not height or fmt.get("vcodec") == "none":
            continue
        size = fmt.get("filesize") or fmt.get("filesize_approx")
        if not isinstance(size, (int, float)):
            continue
        total = int(size + audio_size)
        key = str(int(height))
        if total > sizes.get(key, 0):
            sizes[key] = total
    if sizes:
        sizes["best"] = max(sizes.values())
    return sizes


def update_job(job_id: str, **values):
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job.update(values)
        job["updated"] = time.time()


def get_job(job_id: str):
    with JOB_LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job else None


def is_cancelled(job_id: str):
    with JOB_LOCK:
        job = JOBS.get(job_id)
        return bool(job and job.get("cancel_requested"))


def cleanup_jobs():
    now = time.time()
    doomed = []
    with JOB_LOCK:
        for job_id, job in list(JOBS.items()):
            if job.get("status") in {"ready", "error", "cancelled"} and now - job.get("updated", now) > JOB_TTL:
                doomed.append((job_id, job.get("workdir")))
                del JOBS[job_id]
    for _, workdir in doomed:
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def parse_json_list(raw: str):
    try:
        value = json.loads(raw or "[]")
        return value if isinstance(value, list) else []
    except Exception:
        return []


def parse_json_dict(raw: str):
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def download_cover(url: str, workdir: str):
    if not url:
        return None
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=15) as response:
            content_type = (response.headers.get("Content-Type") or "").lower()
            ext = ".jpg"
            if "png" in content_type:
                ext = ".png"
            elif "webp" in content_type:
                ext = ".webp"
            path = os.path.join(workdir, "cover" + ext)
            with open(path, "wb") as handle:
                handle.write(response.read(8 * 1024 * 1024))
            return path
    except Exception:
        return None


def ffmpeg_track(source, output, start, length, fmt, bitrate, title, number, album, artist):
    br = None if bitrate == "best" else f"{bitrate}k"
    args = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-i", source,
        "-t", f"{length:.3f}", "-map", "0:a:0", "-vn",
    ]
    if fmt == "mp3":
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


def build_tracks_zip(job_id, source, info, selected, custom_titles, audio_format, audio_quality, workdir):
    chapters = extract_chapters(info)
    if not chapters:
        raise RuntimeError("Aucun chapitre ou timestamp exploitable n’a été détecté.")
    selected = [i for i in selected if isinstance(i, int) and 0 <= i < len(chapters)]
    if not selected:
        raise RuntimeError("Sélectionne au moins une track.")

    source_ext = os.path.splitext(source)[1].lower().lstrip(".") or "m4a"
    fmt = audio_format
    if fmt == "original":
        fmt = source_ext if source_ext in {"m4a", "mp3", "opus", "ogg", "flac", "wav", "webm", "aac", "mka"} else "m4a"

    album = clean_title(info.get("title") or "Mix")
    artist = clean_title(info.get("uploader") or info.get("channel") or info.get("artist") or "", 0) if (info.get("uploader") or info.get("channel") or info.get("artist")) else ""
    zip_path = os.path.join(workdir, "tracks.zip")
    tracklist = []
    metadata = []
    cover = download_cover(info.get("thumbnail"), workdir)
    total = len(selected)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for pos, idx in enumerate(selected, 1):
            if is_cancelled(job_id):
                raise InterruptedError("Job annulé")
            chapter = chapters[idx]
            original_no = int(chapter.get("number") or idx + 1)
            custom = custom_titles.get(str(idx)) or custom_titles.get(str(original_no))
            title = clean_title(custom or chapter.get("title"), original_no)
            start = float(chapter["start"])
            length = max(0.05, float(chapter["end"]) - start)
            track_path = os.path.join(workdir, f"track-{original_no:03d}.{fmt}")
            ffmpeg_track(source, track_path, start, length, fmt, audio_quality, title, original_no, album, artist)
            arcname = f"{original_no:02d} - {title}.{fmt}"
            archive.write(track_path, arcname=arcname)
            try:
                os.remove(track_path)
            except OSError:
                pass
            tracklist.append(f"{original_no:02d}. {title}")
            metadata.append({"number": original_no, "title": title, "start": start, "duration": length})
            progress = 70 + int((pos / total) * 24)
            update_job(job_id, stage="splitting", progress=progress, message=f"Découpage {pos}/{total}", track_current=pos, track_total=total)

        archive.writestr("tracklist.txt", "\n".join(tracklist) + "\n")
        archive.writestr("metadata.json", json.dumps({"album": album, "artist": artist, "tracks": metadata}, ensure_ascii=False, indent=2))
        if cover and os.path.isfile(cover):
            archive.write(cover, arcname=os.path.basename(cover))

    return zip_path


def worker(job_id: str, payload: dict):
    workdir = tempfile.mkdtemp(prefix=f"panda_v6_{job_id[:8]}_")
    update_job(job_id, workdir=workdir, status="running", stage="preparing", progress=3, message="Préparation")
    try:
        clean = validate_public_url(payload["url"])
        platform = detect_platform(clean)
        if platform in {"tidal", "deezer"}:
            raise RuntimeError("Cette source n’est pas disponible pour le téléchargement direct.")
        if platform == "spotify":
            raise RuntimeError("Le moteur V6 Spotify sera réactivé dans une prochaine révision. Utilise pour l’instant une source prise en charge par yt-dlp.")

        mode = "audio" if payload.get("mode") == "audio" else "video"
        quality = payload.get("quality") or "best"
        audio_quality = payload.get("audio_quality") if payload.get("audio_quality") in AUDIO_BITRATES else "best"
        video_format = payload.get("video_format") if payload.get("video_format") in VIDEO_FORMATS else "mp4"
        audio_format = payload.get("audio_format") if payload.get("audio_format") in AUDIO_FORMATS else "original"
        split_tracks = bool(payload.get("split_tracks")) and mode == "audio"
        selected = payload.get("selected_tracks") or []
        custom_titles = payload.get("track_titles") or {}

        output = os.path.join(workdir, "%(id)s.%(ext)s")
        opts = base_ydl_options()
        opts.update({"outtmpl": output, "restrictfilenames": True, "trim_file_name": 80, "windowsfilenames": True})

        def hook(data):
            if is_cancelled(job_id):
                raise InterruptedError("Job annulé")
            if data.get("status") == "downloading":
                downloaded = data.get("downloaded_bytes") or 0
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                ratio = (downloaded / total) if total else 0
                pct = 10 + int(max(0, min(1, ratio)) * 48)
                speed = data.get("speed")
                speed_text = f" · {speed/1024/1024:.1f} MB/s" if isinstance(speed, (int, float)) and speed else ""
                update_job(job_id, stage="downloading", progress=pct, message=f"Téléchargement {int(ratio*100) if total else 0}%{speed_text}")
            elif data.get("status") == "finished":
                update_job(job_id, stage="processing", progress=60, message="Téléchargement terminé")

        opts["progress_hooks"] = [hook]
        if mode == "audio":
            opts["format"] = "bestaudio/best"
        else:
            if quality == "best":
                selector = "bv*+ba/b"
            else:
                try:
                    height = max(144, min(int(quality), 4320))
                except ValueError:
                    height = 1080
                selector = f"bv*[height<={height}]+ba/b[height<={height}]"
            opts["format"] = selector
            opts["merge_output_format"] = "mkv"

        update_job(job_id, stage="downloading", progress=8, message="Connexion à la source")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(clean, download=True)

        if is_cancelled(job_id):
            raise InterruptedError("Job annulé")

        files = media_files(workdir)
        if not files:
            raise RuntimeError("Aucun fichier média final généré.")
        source = max(files, key=os.path.getsize)
        title = info.get("title") or info.get("id") or "download"

        if mode == "video":
            update_job(job_id, stage="converting", progress=68, message=f"Conversion {video_format.upper()}")
            final_file = convert_video(source, video_format, workdir)
            ext = os.path.splitext(final_file)[1]
            filename = pretty_name(title, ext)
        elif split_tracks:
            update_job(job_id, stage="splitting", progress=70, message="Préparation des tracks")
            final_file = build_tracks_zip(job_id, source, info, selected, custom_titles, audio_format, audio_quality, workdir)
            filename = pretty_name(f"{title} - tracks", ".zip")
        else:
            update_job(job_id, stage="converting", progress=72, message="Conversion audio")
            final_file = convert_audio(source, audio_format, audio_quality, workdir)
            ext = os.path.splitext(final_file)[1]
            filename = pretty_name(title, ext)

        if is_cancelled(job_id):
            raise InterruptedError("Job annulé")

        size = os.path.getsize(final_file)
        media_type = mimetypes.guess_type(final_file)[0] or "application/octet-stream"
        update_job(
            job_id,
            status="ready",
            stage="ready",
            progress=100,
            message="Prêt à télécharger",
            result_path=final_file,
            filename=filename,
            media_type=media_type,
            result_size=size,
        )
    except InterruptedError:
        shutil.rmtree(workdir, ignore_errors=True)
        update_job(job_id, status="cancelled", stage="cancelled", progress=0, message="Job annulé", result_path=None)
    except Exception as exc:
        update_job(job_id, status="error", stage="error", message=friendly_error(exc), error=friendly_error(exc), progress=0)


HTML = r'''<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#171a23"><title>panda.download.com</title>
<style>
:root{--bg:#151821;--shell:#353b49;--panel:#11151d;--panel2:#1a1f29;--line:#454c5b;--text:#dce0e9;--muted:#7d8596;--blue:#6ea7ff;--purple:#875dff;--pink:#dc60e7;--orange:#ff9b36;--danger:#ff919d;--good:#7bdba8}*{box-sizing:border-box}html{scrollbar-width:none}html::-webkit-scrollbar,body::-webkit-scrollbar,*::-webkit-scrollbar{display:none;width:0;height:0}html,body{margin:0;min-height:100%;font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--text)}body{overflow-x:hidden;background:radial-gradient(circle at 50% -15%,rgba(108,117,154,.28),transparent 43%),linear-gradient(180deg,#303547,#202431 46%,#151821)}button,input,select{font:inherit}.page{width:min(1180px,calc(100% - 24px));margin:auto;padding:24px 0 50px}.top{display:flex;justify-content:space-between;align-items:center;padding:4px 34px 15px;color:#8d95a5;font-size:11px;font-weight:900;letter-spacing:.09em;text-transform:uppercase}.brand b{color:#e5e8ee}.online{display:flex;gap:8px;align-items:center}.online:before{content:"";width:7px;height:7px;border-radius:50%;background:var(--good);box-shadow:0 0 10px var(--good)}.device{border-radius:40px;padding:16px;background:linear-gradient(145deg,#505667,#363c4b 24%,#252b36 62%,#20252f);border:1px solid #6c7384;box-shadow:0 32px 76px rgba(0,0,0,.48),inset 0 1px 0 rgba(255,255,255,.16)}.inner{border-radius:28px;padding:16px;background:linear-gradient(180deg,#1d222c,#171b23);border:1px solid #11151c}.display{height:300px;position:relative;overflow:hidden;border-radius:20px;background:linear-gradient(180deg,#0c1118,#11151d);border:1px solid #4c5362;box-shadow:inset 0 0 0 2px rgba(0,0,0,.45),inset 0 8px 24px rgba(0,0,0,.35)}.grid{position:absolute;inset:0;background:repeating-linear-gradient(90deg,transparent 0 16%,rgba(113,122,145,.11) 16.1% 16.25%,transparent 16.4% 32%)}.wave1,.wave2{position:absolute;bottom:-32px;height:215px;border-radius:50% 50% 0 0/75% 75% 0 0}.wave1{left:-3%;width:51%;background:radial-gradient(ellipse at 48% 70%,rgba(173,79,255,.88),rgba(78,57,255,.75) 48%,transparent 74%);transform:rotate(-6deg)}.wave2{left:28%;width:58%;background:radial-gradient(ellipse at 44% 72%,rgba(219,86,219,.84),rgba(255,126,67,.9) 52%,transparent 75%);transform:rotate(3deg)}.hero-title{position:absolute;top:50px;left:0;right:0;text-align:center;font-size:27px;font-weight:950;letter-spacing:-1.5px;color:#747d8d}.hero-title b{color:#9fa6b5}.hero-title i{font-style:normal;color:var(--orange)}.urlrow{position:absolute;left:28px;right:28px;bottom:22px;display:grid;grid-template-columns:1fr 135px;gap:10px;z-index:5}.urlbox{height:54px;border-radius:13px;background:rgba(7,10,15,.86);border:1px solid #505767;display:flex;align-items:center;padding:0 15px}.urlbox input{width:100%;border:0;outline:0;background:transparent;color:#d4d9e3;font-size:13px}.btn{border:1px solid #596171;border-radius:13px;color:#d5d9e2;background:linear-gradient(#383f4d,#252b36);font-weight:900;cursor:pointer;box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 4px 11px rgba(0,0,0,.22)}.btn:hover{filter:brightness(1.11)}.btn:disabled{opacity:.42;cursor:not-allowed}.result{position:absolute;inset:20px 20px 84px;display:none;grid-template-columns:220px 1fr;gap:18px;align-items:center;padding:14px;border-radius:16px;background:rgba(8,11,16,.92);border:1px solid #3c4351;backdrop-filter:blur(12px);z-index:4}.result.show{display:grid}.thumb{aspect-ratio:16/9;border-radius:11px;overflow:hidden;background:#202530;border:1px solid #474e5d;position:relative}.thumb img{width:100%;height:100%;object-fit:cover}.duration{position:absolute;right:6px;bottom:6px;padding:4px 6px;background:rgba(0,0,0,.75);border-radius:6px;font-size:9px}.media-title{font-size:17px;font-weight:900;line-height:1.3}.media-sub{margin-top:7px;color:var(--muted);font-size:11px}.platform{display:inline-block;margin-top:9px;padding:5px 8px;border-radius:7px;background:#232936;border:1px solid #434a5a;color:#929aaa;font-size:9px;font-weight:900}.stats{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px}.stat{padding:5px 7px;border-radius:7px;background:#171c25;border:1px solid #303744;color:#7f8898;font-size:9px}.message{display:none;margin:11px 2px 0;padding:10px 12px;border-radius:10px;font-size:11px}.message.status{color:#929aaa}.message.error{color:var(--danger);background:rgba(255,105,120,.07);border:1px solid rgba(255,105,120,.18)}.workspace{display:none}.workspace.show{display:block}.controls{display:grid;grid-template-columns:1fr 1fr 1fr 1.2fr;gap:10px;margin-top:16px}.module{min-height:190px;border-radius:19px;padding:14px;background:linear-gradient(155deg,#303644,#252b37 48%,#1f242e);border:1px solid #404756;box-shadow:inset 0 1px 0 rgba(255,255,255,.07),0 8px 18px rgba(0,0,0,.2)}.module-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.module-title{font-size:12px;font-weight:950}.led{width:21px;height:21px;border-radius:50%;border:1px solid #151922;background:#252b36;display:grid;place-items:center}.led:after{content:"";width:7px;height:7px;border-radius:50%;background:var(--blue);box-shadow:0 0 9px rgba(104,164,255,.75)}.orange .led:after{background:var(--orange)}.purple .led:after{background:var(--pink)}.modebuttons{display:grid;gap:8px}.modebtn{height:48px;border-radius:12px;border:1px solid #424958;background:linear-gradient(#323846,#252b36);color:#8e96a6;font-size:10px;font-weight:900;cursor:pointer}.modebtn.active{color:#9fc0ff;border-color:#5d7db0}.field{border-radius:11px;background:#11151d;border:1px solid #343b49;padding:9px 10px;margin-bottom:8px}.field small{display:block;font-size:8px;color:#6c7484;font-weight:900;margin-bottom:5px}.field select{width:100%;border:0;outline:0;background:transparent;color:#8db8ff;font-size:11px;font-weight:900}.orange .field select{color:#ffad4c}.purple .field select{color:#e487ec}.field select option{background:#151922;color:#e0e4eb}.preset-row{display:grid;grid-template-columns:1fr 1fr;gap:7px}.presetbtn{height:39px;border-radius:10px;border:1px solid #3e4655;background:#222833;color:#8e96a6;font-size:9px;font-weight:900;cursor:pointer}.presetbtn:hover{color:#c7cdd7}.summary{margin-top:10px;padding:9px 10px;border-radius:10px;background:#121720;border:1px solid #303744;color:#768091;font-size:9px;line-height:1.5}.track-editor{display:none;margin-top:12px;border-radius:19px;padding:14px;background:linear-gradient(155deg,#2a303c,#202631);border:1px solid #404756}.track-editor.show{display:block}.editor-head{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}.editor-title{font-size:12px;font-weight:950}.editor-tools{display:flex;gap:7px;flex-wrap:wrap}.smallbtn{padding:7px 9px;border-radius:9px;border:1px solid #444c5b;background:#202630;color:#929aaa;font-size:9px;font-weight:900;cursor:pointer}.split-toggle{display:flex;gap:9px;align-items:center;font-size:10px;font-weight:900;cursor:pointer}.split-toggle input{accent-color:#75a8ff}.timeline{display:flex;gap:3px;height:42px;margin-top:12px;padding:6px;border-radius:10px;background:#141922;border:1px solid #303744;overflow:hidden}.segment{min-width:5px;border:0;border-radius:5px;background:#343d50;cursor:pointer;transition:.12s}.segment.selected{background:linear-gradient(90deg,#785cff,#d35de5)}.track-count{margin-top:8px;color:#7f8898;font-size:9px}.tracklist{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:10px}.track{display:grid;grid-template-columns:20px 30px minmax(0,1fr) auto;gap:8px;align-items:center;padding:9px;border-radius:10px;background:#151a23;border:1px solid #303744}.track input[type=checkbox]{accent-color:#75a8ff}.track b{font-size:9px;color:#8db9ff}.track input[type=text]{width:100%;min-width:0;border:0;outline:0;background:transparent;color:#c8ced8;font-size:10px;font-weight:700}.track em{font-style:normal;color:#697282;font-size:9px}.jobbox{display:none;margin-top:14px;border-radius:19px;padding:15px;background:#171c25;border:1px solid #3d4554}.jobbox.show{display:block}.jobtop{display:flex;justify-content:space-between;align-items:center;gap:12px}.jobstage{font-size:12px;font-weight:950}.jobmsg{margin-top:5px;color:#7f8898;font-size:10px}.progress{height:10px;margin-top:12px;border-radius:999px;background:#0f131a;border:1px solid #303744;overflow:hidden}.progressbar{height:100%;width:0;background:linear-gradient(90deg,#6d64ff,#db5ee5,#ff9a36);transition:width .25s}.jobactions{display:flex;gap:8px;margin-top:12px}.jobactions .btn{height:43px;padding:0 14px;font-size:10px}.download-ready{display:none}.download-ready.show{display:inline-flex;align-items:center;text-decoration:none}.history{margin-top:10px;color:#697282;font-size:9px}.bottom-download{width:100%;height:52px;margin-top:9px;font-size:10px}
@media(max-width:900px){.page{width:min(100% - 16px,760px)}.top{padding:4px 12px 12px}.device{border-radius:29px;padding:11px}.inner{border-radius:21px;padding:11px}.controls{grid-template-columns:1fr 1fr}.tracklist{grid-template-columns:1fr}}
@media(max-width:560px){body{background:linear-gradient(180deg,#292e3d,#171a23)}.page{width:100%;padding:9px 7px 95px}.top{padding:3px 7px 8px;font-size:9px}.device{border-radius:23px;padding:7px}.inner{border-radius:17px;padding:7px}.display{height:255px;border-radius:15px}.hero-title{top:31px;font-size:19px}.urlrow{left:9px;right:9px;bottom:9px;grid-template-columns:1fr}.urlbox{height:47px}.urlrow .btn{height:45px}.result{inset:10px 8px 112px;grid-template-columns:90px 1fr;gap:9px;padding:8px}.media-title{font-size:12px}.media-sub,.platform,.stat{font-size:8px}.controls{grid-template-columns:1fr;gap:7px;margin-top:8px}.module{min-height:auto;border-radius:15px;padding:11px}.modebuttons{grid-template-columns:1fr 1fr}.modebtn{height:47px}.track-editor{border-radius:15px;padding:11px}.editor-head{align-items:flex-start;flex-direction:column}.timeline{height:36px}.tracklist{grid-template-columns:1fr}.track{padding:10px}.jobbox{border-radius:15px}.jobactions{position:fixed;left:8px;right:8px;bottom:8px;z-index:30;padding:8px;background:rgba(20,24,33,.94);border:1px solid #3d4554;border-radius:15px;backdrop-filter:blur(15px)}.jobactions .btn,.jobactions a{flex:1;justify-content:center}.wave1,.wave2{height:160px}.wave1{width:65%}.wave2{left:26%;width:74%}}
</style></head><body><div class="page"><div class="top"><div class="brand"><b>PANDA</b>.DOWNLOAD</div><div class="online">V6 JOB ENGINE</div></div><section class="device"><div class="inner"><div class="display"><div class="grid"></div><div class="wave1"></div><div class="wave2"></div><div class="hero-title"><b>PANDA</b> DOWNLOAD<i>.</i></div><div class="result" id="result"><div class="thumb"><img id="thumb"><div class="duration" id="duration"></div></div><div><div class="media-title" id="title"></div><div class="media-sub" id="uploader"></div><div class="platform" id="platform"></div><div class="stats"><span class="stat" id="chapterStat"></span><span class="stat" id="sizeStat"></span></div></div></div><div class="urlrow"><div class="urlbox"><input id="url" placeholder="Colle une URL vidéo ou audio..." autocomplete="off"></div><button class="btn" id="analyse">ANALYSER</button></div></div><div class="message status" id="status">Analyse en cours…</div><div class="message error" id="error"></div><div class="workspace" id="workspace"><div class="controls"><div class="module"><div class="module-head"><span class="module-title">MODE</span><span class="led"></span></div><div class="modebuttons"><button class="modebtn active" id="videoMode">VIDÉO</button><button class="modebtn" id="audioMode">AUDIO</button></div><div class="summary" id="modeSummary">Vidéo complète</div></div><div class="module"><div class="module-head"><span class="module-title">QUALITY</span><span class="led"></span></div><div class="field" id="videoQualityField"><small>VIDÉO</small><select id="quality"><option value="best">MEILLEURE</option></select></div><div class="field" id="audioQualityField"><small>AUDIO</small><select id="audioQuality"><option value="best">SOURCE</option><option value="320">320 KBPS</option><option value="256">256 KBPS</option><option value="192">192 KBPS</option><option value="128">128 KBPS</option></select></div><div class="summary" id="qualitySummary">Qualité maximale</div></div><div class="module orange"><div class="module-head"><span class="module-title">FORMAT</span><span class="led"></span></div><div class="field" id="videoFormatField"><small>VIDÉO</small><select id="videoFormat"><option value="mp4">MP4</option><option value="mkv">MKV</option><option value="webm">WEBM</option><option value="mov">MOV</option><option value="avi">AVI</option></select></div><div class="field" id="audioFormatField" style="display:none"><small>AUDIO</small><select id="audioFormat"><option value="original">ORIGINAL</option><option value="mp3">MP3</option><option value="m4a">M4A / AAC</option><option value="opus">OPUS</option><option value="flac">FLAC</option><option value="wav">WAV</option><option value="ogg">OGG</option></select></div><div class="summary" id="formatSummary">Sortie MP4</div></div><div class="module purple"><div class="module-head"><span class="module-title">PRESETS</span><span class="led"></span></div><div class="preset-row"><button class="presetbtn" data-preset="video1080">1080P MP4</button><button class="presetbtn" data-preset="videoBest">BEST VIDEO</button><button class="presetbtn" data-preset="mp3320">MP3 320</button><button class="presetbtn" data-preset="tracksMp3">TRACKS MP3</button><button class="presetbtn" data-preset="tracksFlac">TRACKS FLAC</button><button class="presetbtn" data-preset="opus">OPUS</button></div><button class="btn bottom-download" id="startJob">CRÉER LE JOB ↓</button></div></div><section class="track-editor" id="trackEditor"><div class="editor-head"><div><div class="editor-title">TRACK EDITOR</div><div class="track-count" id="trackCount"></div></div><div class="editor-tools"><label class="split-toggle"><input type="checkbox" id="splitTracks"> SÉPARER EN TRACKS</label><button class="smallbtn" id="allTracks">TOUT</button><button class="smallbtn" id="noneTracks">AUCUN</button><button class="smallbtn" id="invertTracks">INVERSER</button></div></div><div class="timeline" id="timeline"></div><div class="tracklist" id="tracklist"></div></section><section class="jobbox" id="jobbox"><div class="jobtop"><div><div class="jobstage" id="jobstage">PRÉPARATION</div><div class="jobmsg" id="jobmsg"></div></div><strong id="jobpct">0%</strong></div><div class="progress"><div class="progressbar" id="progressbar"></div></div><div class="jobactions"><button class="btn" id="cancelJob">ANNULER</button><a class="btn download-ready" id="downloadReady">TÉLÉCHARGER ↓</a></div></section><div class="history" id="history"></div></div></div></section></div>
<script>
const $=s=>document.querySelector(s);const url=$('#url'),analyse=$('#analyse'),result=$('#result'),status=$('#status'),error=$('#error'),workspace=$('#workspace'),videoMode=$('#videoMode'),audioMode=$('#audioMode'),quality=$('#quality'),audioQuality=$('#audioQuality'),videoFormat=$('#videoFormat'),audioFormat=$('#audioFormat'),videoQualityField=$('#videoQualityField'),videoFormatField=$('#videoFormatField'),audioFormatField=$('#audioFormatField'),trackEditor=$('#trackEditor'),splitTracks=$('#splitTracks'),tracklist=$('#tracklist'),timeline=$('#timeline'),trackCount=$('#trackCount'),startJob=$('#startJob'),jobbox=$('#jobbox'),jobstage=$('#jobstage'),jobmsg=$('#jobmsg'),jobpct=$('#jobpct'),progressbar=$('#progressbar'),cancelJob=$('#cancelJob'),downloadReady=$('#downloadReady');let mode='video',chapters=[],qualitySizes={},activeJob=null,pollTimer=null;
function fmt(s){if(s==null)return'';s=Math.max(0,Math.floor(s));const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),x=s%60;return h?`${h}:${String(m).padStart(2,'0')}:${String(x).padStart(2,'0')}`:`${m}:${String(x).padStart(2,'0')}`}
function bytes(n){if(!n)return'—';const u=['B','KB','MB','GB'];let i=0;while(n>1024&&i<u.length-1){n/=1024;i++}return `${n.toFixed(i>1?1:0)} ${u[i]}`}
function setMode(next){mode=next;videoMode.classList.toggle('active',mode==='video');audioMode.classList.toggle('active',mode==='audio');videoQualityField.style.display=mode==='video'?'block':'none';videoFormatField.style.display=mode==='video'?'block':'none';audioFormatField.style.display=mode==='audio'?'block':'none';trackEditor.classList.toggle('show',mode==='audio'&&chapters.length>0);if(mode==='video')splitTracks.checked=false;refreshSummary()}
function refreshSummary(){$('#modeSummary').textContent=mode==='video'?'Vidéo complète':splitTracks.checked?`${selectedIndices().length} tracks sélectionnées`:'Audio complet';$('#qualitySummary').textContent=mode==='video'?(quality.value==='best'?'Qualité maximale':`${quality.value}p · ~${bytes(qualitySizes[quality.value])}`):(audioQuality.value==='best'?'Source audio':'Encodage '+audioQuality.value+' kbps');$('#formatSummary').textContent='Sortie '+(mode==='video'?videoFormat.value.toUpperCase():audioFormat.value.toUpperCase());updateTrackCount()}
function fillQualities(values){quality.innerHTML='<option value="best">MEILLEURE</option>';[...new Set(values||[])].sort((a,b)=>b-a).forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v>=2160?`${v}p · 4K`:v>=1440?`${v}p · 2K`:v>=1080?`${v}p · FULL HD`:v>=720?`${v}p · HD`:`${v}p`;quality.appendChild(o)})}
function selectedIndices(){return [...document.querySelectorAll('.trackcheck')].map((x,i)=>x.checked?i:null).filter(x=>x!==null)}
function updateTrackCount(){if(!chapters.length){trackCount.textContent='Aucun chapitre';return}trackCount.textContent=`${selectedIndices().length} / ${chapters.length} sélectionnées`;[...timeline.children].forEach((el,i)=>el.classList.toggle('selected',document.querySelectorAll('.trackcheck')[i]?.checked))}
function renderTracks(list){chapters=list||[];tracklist.innerHTML='';timeline.innerHTML='';const total=chapters.reduce((s,c)=>s+(c.duration||0),0)||1;chapters.forEach((c,i)=>{const row=document.createElement('label');row.className='track';row.innerHTML=`<input class="trackcheck" type="checkbox" checked><b>${String(i+1).padStart(2,'0')}</b><input class="tracktitle" type="text"><em>${fmt(c.start)}</em>`;row.querySelector('.tracktitle').value=c.title||`Track ${i+1}`;row.querySelector('.trackcheck').addEventListener('change',()=>{updateTrackCount();refreshSummary()});tracklist.appendChild(row);const seg=document.createElement('button');seg.type='button';seg.className='segment selected';seg.style.flexGrow=Math.max(1,c.duration||1);seg.title=c.title||`Track ${i+1}`;seg.addEventListener('click',()=>{const cb=document.querySelectorAll('.trackcheck')[i];cb.checked=!cb.checked;updateTrackCount();refreshSummary()});timeline.appendChild(seg)});splitTracks.disabled=!chapters.length;updateTrackCount()}
function chooseAll(value){document.querySelectorAll('.trackcheck').forEach(x=>x.checked=value);updateTrackCount();refreshSummary()}
$('#allTracks').onclick=()=>chooseAll(true);$('#noneTracks').onclick=()=>chooseAll(false);$('#invertTracks').onclick=()=>{document.querySelectorAll('.trackcheck').forEach(x=>x.checked=!x.checked);updateTrackCount();refreshSummary()};splitTracks.onchange=refreshSummary;videoMode.onclick=()=>setMode('video');audioMode.onclick=()=>setMode('audio');[quality,audioQuality,videoFormat,audioFormat].forEach(x=>x.onchange=refreshSummary);
function preset(name){if(name==='video1080'){setMode('video');quality.value=[...quality.options].some(o=>o.value==='1080')?'1080':'best';videoFormat.value='mp4'}if(name==='videoBest'){setMode('video');quality.value='best';videoFormat.value='mp4'}if(name==='mp3320'){setMode('audio');splitTracks.checked=false;audioFormat.value='mp3';audioQuality.value='320'}if(name==='tracksMp3'){setMode('audio');splitTracks.checked=true;audioFormat.value='mp3';audioQuality.value='320';chooseAll(true)}if(name==='tracksFlac'){setMode('audio');splitTracks.checked=true;audioFormat.value='flac';audioQuality.value='best';chooseAll(true)}if(name==='opus'){setMode('audio');splitTracks.checked=false;audioFormat.value='opus';audioQuality.value='best'}refreshSummary()}document.querySelectorAll('.presetbtn').forEach(b=>b.onclick=()=>preset(b.dataset.preset));
async function analyseUrl(){error.style.display='none';workspace.classList.remove('show');result.classList.remove('show');const value=url.value.trim();if(!value){error.textContent='Colle une URL valide.';error.style.display='block';return}status.style.display='block';analyse.disabled=true;try{const f=new FormData();f.append('url',value);const r=await fetch('/info',{method:'POST',body:f});const d=await r.json();if(!r.ok)throw new Error(d.error||'Analyse impossible');$('#thumb').src=d.thumbnail||'';$('#title').textContent=d.title||'Média';$('#uploader').textContent=d.uploader||'';$('#duration').textContent=fmt(d.duration);$('#platform').textContent=(d.platform||'MEDIA').toUpperCase();chapters=d.chapters||[];qualitySizes=d.quality_sizes||{};fillQualities(d.qualities);renderTracks(chapters);$('#chapterStat').textContent=chapters.length?`${chapters.length} chapitres`:'Pas de chapitres';$('#sizeStat').textContent=qualitySizes.best?`~${bytes(qualitySizes.best)}`:'Taille inconnue';result.classList.add('show');workspace.classList.add('show');setMode(d.audio_only?'audio':'video');saveHistory(value,d.title||'Média')}catch(e){error.textContent=e.message;error.style.display='block'}finally{status.style.display='none';analyse.disabled=false}}
analyse.onclick=analyseUrl;url.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();analyseUrl()}});
function saveHistory(u,t){let h=[];try{h=JSON.parse(localStorage.getItem('pandaHistory')||'[]')}catch{}h=[{u,t,ts:Date.now()},...h.filter(x=>x.u!==u)].slice(0,6);localStorage.setItem('pandaHistory',JSON.stringify(h));renderHistory()}
function renderHistory(){let h=[];try{h=JSON.parse(localStorage.getItem('pandaHistory')||'[]')}catch{}$('#history').textContent=h.length?`Récents : ${h.map(x=>x.t).join(' · ')}`:''}renderHistory();
async function createJob(){if(activeJob)return;const selected=selectedIndices();if(mode==='audio'&&splitTracks.checked&&!selected.length){error.textContent='Sélectionne au moins une track.';error.style.display='block';return}const titles={};document.querySelectorAll('.tracktitle').forEach((x,i)=>titles[String(i)]=x.value);const f=new FormData();f.append('url',url.value.trim());f.append('mode',mode);f.append('quality',quality.value);f.append('audio_quality',audioQuality.value);f.append('video_format',videoFormat.value);f.append('audio_format',audioFormat.value);f.append('split_tracks',splitTracks.checked?'1':'0');f.append('selected_tracks',JSON.stringify(selected));f.append('track_titles',JSON.stringify(titles));startJob.disabled=true;error.style.display='none';try{const r=await fetch('/jobs',{method:'POST',body:f});const d=await r.json();if(!r.ok)throw new Error(d.error||'Impossible de créer le job');activeJob=d.job_id;jobbox.classList.add('show');downloadReady.classList.remove('show');cancelJob.style.display='inline-block';pollJob()}catch(e){error.textContent=e.message;error.style.display='block';startJob.disabled=false}}
startJob.onclick=createJob;
async function pollJob(){if(!activeJob)return;try{const r=await fetch(`/jobs/${activeJob}`);const d=await r.json();if(!r.ok)throw new Error(d.detail||'Job introuvable');jobstage.textContent=(d.stage||d.status||'JOB').toUpperCase();jobmsg.textContent=d.message||'';jobpct.textContent=`${d.progress||0}%`;progressbar.style.width=`${d.progress||0}%`;if(d.status==='ready'){downloadReady.href=`/jobs/${activeJob}/download`;downloadReady.classList.add('show');downloadReady.textContent=`TÉLÉCHARGER ${d.filename||''} ↓`;cancelJob.style.display='none';startJob.disabled=false;return}if(d.status==='error'||d.status==='cancelled'){error.textContent=d.error||d.message||'Job arrêté';error.style.display='block';startJob.disabled=false;activeJob=null;return}pollTimer=setTimeout(pollJob,900)}catch(e){error.textContent=e.message;error.style.display='block';startJob.disabled=false;activeJob=null}}
cancelJob.onclick=async()=>{if(!activeJob)return;await fetch(`/jobs/${activeJob}/cancel`,{method:'POST'});};
setMode('video');renderTracks([]);refreshSummary();
</script></body></html>'''


@app.get("/", response_class=HTMLResponse)
def home():
    cleanup_jobs()
    return HTML


@app.get("/health")
def health():
    cleanup_jobs()
    return {
        "status": "ok",
        "service": "panda-download",
        "ui": "plugin-v6",
        "job_engine": True,
        "track_editor": True,
        "youtube_cookies": bool(cookie_path()),
        "deno": shutil.which("deno") is not None,
        "jobs": len(JOBS),
    }


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.post("/info")
def media_info(url: str = Form(...)):
    try:
        clean = validate_public_url(url)
        platform = detect_platform(clean)
        if platform == "spotify":
            return {"platform":"spotify","title":"Spotify","uploader":"","thumbnail":"","duration":None,"qualities":[],"quality_sizes":{},"audio_only":True,"blocked":False,"chapters":[]}
        if platform in {"tidal", "deezer"}:
            return JSONResponse(status_code=400, content={"error":"Cette source n’est pas disponible pour le téléchargement direct."})
        opts = base_ydl_options()
        opts["skip_download"] = True
        opts["outtmpl"] = "%(id)s.%(ext)s"
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(clean, download=False)
        if not info:
            raise RuntimeError("Aucun média détecté.")
        qualities = sorted({int(f["height"]) for f in info.get("formats", []) if f.get("height") and f.get("vcodec") != "none"}, reverse=True)
        chapters = extract_chapters(info)
        return {
            "platform": platform if platform != "generic" else (info.get("extractor_key") or "media"),
            "title": info.get("title"),
            "uploader": info.get("uploader") or info.get("channel") or info.get("artist"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "qualities": qualities,
            "quality_sizes": quality_sizes(info),
            "audio_only": not bool(qualities),
            "blocked": False,
            "chapters": chapters,
        }
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": friendly_error(exc)})


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
):
    cleanup_jobs()
    try:
        clean = validate_public_url(url)
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": friendly_error(exc)})
    selected = []
    for value in parse_json_list(selected_tracks):
        try:
            selected.append(int(value))
        except Exception:
            pass
    payload = {
        "url": clean,
        "mode": mode,
        "quality": quality,
        "audio_quality": audio_quality,
        "video_format": video_format,
        "audio_format": audio_format,
        "split_tracks": split_tracks == "1",
        "selected_tracks": selected,
        "track_titles": parse_json_dict(track_titles),
    }
    job_id = uuid.uuid4().hex
    now = time.time()
    with JOB_LOCK:
        JOBS[job_id] = {
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
    EXECUTOR.submit(worker, job_id, payload)
    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    cleanup_jobs()
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job introuvable ou expiré")
    public = {k: v for k, v in job.items() if k not in {"result_path", "workdir"}}
    return public


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job introuvable")
    if job.get("status") in {"ready", "error", "cancelled"}:
        return {"status": job.get("status")}
    update_job(job_id, cancel_requested=True, message="Annulation demandée")
    return {"status": "cancelling"}


@app.get("/jobs/{job_id}/download")
def job_download(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job introuvable ou expiré")
    if job.get("status") != "ready":
        raise HTTPException(status_code=409, detail="Le fichier n’est pas encore prêt")
    path = job.get("result_path")
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=410, detail="Le fichier a expiré")
    return FileResponse(path, media_type=job.get("media_type") or "application/octet-stream", filename=job.get("filename") or os.path.basename(path))
