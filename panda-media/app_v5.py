from fastapi import FastAPI, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
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


def clean_title(value: str, index: int) -> str:
    value = (value or "").strip()
    value = re.sub(r"^\s*[\-–—•|]+\s*", "", value)
    value = re.sub(r"^\d{1,3}[.\-)\s]+", "", value)
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._-")
    return (value[:90] or f"Track {index:02d}")


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
    out = []
    for idx, (start, title) in enumerate(points, 1):
        end = points[idx][0] if idx < len(points) else duration
        if end is None or end <= start:
            continue
        out.append({
            "number": idx,
            "title": clean_title(title, idx),
            "start": float(start),
            "end": float(end),
            "duration": float(end - start),
        })
    return out


def extract_chapters(info: dict):
    out = []
    for idx, chapter in enumerate(info.get("chapters") or [], 1):
        try:
            start = float(chapter.get("start_time"))
            end = float(chapter.get("end_time"))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        out.append({
            "number": idx,
            "title": clean_title(chapter.get("title"), idx),
            "start": start,
            "end": end,
            "duration": end - start,
        })
    return out or chapters_from_description(info.get("description") or "", info.get("duration"))


def parse_selected_tracks(raw: str, count: int):
    try:
        values = json.loads(raw or "[]")
    except json.JSONDecodeError:
        values = []
    if not isinstance(values, list):
        values = []
    selected = []
    for value in values:
        try:
            idx = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < count and idx not in selected:
            selected.append(idx)
    return selected


def audio_ffmpeg_args(source: str, start: float, length: float, out: str, fmt: str, bitrate: str, title: str, track_no: int):
    base = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-i", source, "-t", f"{length:.3f}",
        "-map", "0:a:0", "-vn",
    ]
    br = None if bitrate == "best" else f"{bitrate}k"
    if fmt == "mp3":
        codec = ["-c:a", "libmp3lame", "-b:a", br or "320k"]
    elif fmt == "m4a":
        codec = ["-c:a", "aac", "-b:a", br or "256k", "-movflags", "+faststart"]
    elif fmt == "opus":
        codec = ["-c:a", "libopus", "-b:a", br or "192k"]
    elif fmt == "ogg":
        codec = ["-c:a", "libvorbis", "-b:a", br or "192k"]
    elif fmt == "flac":
        codec = ["-c:a", "flac"]
    elif fmt == "wav":
        codec = ["-c:a", "pcm_s16le"]
    else:
        codec = ["-c:a", "copy"]
    return base + codec + ["-metadata", f"title={title}", "-metadata", f"track={track_no}", out]


def split_selected_to_zip(source: str, chapters: list, selected: list, audio_format: str, audio_quality: str, temp_dir: str, mix_title: str):
    if not selected:
        raise RuntimeError("Sélectionne au moins une track avant le téléchargement.")

    source_ext = os.path.splitext(source)[1].lower().lstrip(".") or "m4a"
    fmt = audio_format
    if fmt == "original":
        fmt = source_ext
        if fmt not in {"m4a", "mp3", "opus", "ogg", "flac", "wav", "webm", "aac", "mka"}:
            fmt = "m4a"

    zip_path = os.path.join(temp_dir, "selected-tracks.zip")
    tracklist_lines = []

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for idx in selected:
            chapter = chapters[idx]
            original_no = int(chapter.get("number") or idx + 1)
            title = clean_title(chapter.get("title"), original_no)
            start = float(chapter["start"])
            length = max(0.05, float(chapter["end"]) - start)

            ext = fmt
            if fmt == "webm":
                ext = "webm"
            track_name = f"{original_no:02d} - {title}.{ext}"
            track_path = os.path.join(temp_dir, f"track-{original_no:03d}.{ext}")

            if audio_format == "original" and fmt == source_ext:
                args = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{start:.3f}", "-i", source, "-t", f"{length:.3f}",
                    "-map", "0:a:0", "-vn", "-c:a", "copy",
                    "-metadata", f"title={title}", "-metadata", f"track={original_no}", track_path,
                ]
            else:
                target_fmt = audio_format if audio_format != "original" else "m4a"
                track_path = os.path.join(temp_dir, f"track-{original_no:03d}.{target_fmt}")
                track_name = f"{original_no:02d} - {title}.{target_fmt}"
                args = audio_ffmpeg_args(source, start, length, track_path, target_fmt, audio_quality, title, original_no)

            proc = subprocess.run(args, capture_output=True, text=True, timeout=3300, check=False)
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or f"Impossible de créer {track_name}")[-1800:])

            archive.write(track_path, arcname=track_name)
            tracklist_lines.append(f"{original_no:02d}. {title}")
            try:
                os.remove(track_path)
            except OSError:
                pass

        archive.writestr("tracklist.txt", "\n".join(tracklist_lines) + "\n")
        archive.writestr("README.txt", f"{mix_title}\n{len(selected)} track(s) sélectionnée(s).\n")

    return zip_path


HTML = r'''<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#202432"><title>panda.download.com</title>
<style>
:root{--bg:#171a23;--shell:#343a49;--panel:#11151d;--line:#485061;--text:#d9dde7;--muted:#7f8797;--blue:#69a4ff;--orange:#ff9b35;--pink:#d85ae7;--danger:#ff929d}*{box-sizing:border-box}html{scrollbar-width:none}html::-webkit-scrollbar,body::-webkit-scrollbar,*::-webkit-scrollbar{display:none;width:0;height:0}html,body{margin:0;min-height:100%;font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--text)}body{overflow-x:hidden;background:radial-gradient(circle at 50% -15%,rgba(112,121,158,.28),transparent 42%),linear-gradient(180deg,#303547,#202431 48%,#171a23)}button,input,select{font:inherit}.page{width:min(1080px,calc(100% - 24px));margin:auto;padding:26px 0 46px}.top{display:flex;justify-content:space-between;align-items:center;padding:4px 36px 16px;color:#8991a1;font-size:12px;font-weight:850;letter-spacing:.08em;text-transform:uppercase}.brand{color:#b7bdc9}.brand b{color:#e0e3e9}.online{display:flex;align-items:center;gap:8px}.online:before{content:"";width:7px;height:7px;border-radius:50%;background:var(--blue);box-shadow:0 0 10px var(--blue)}.device{border-radius:42px;padding:18px;background:linear-gradient(145deg,#505667,#363c4b 24%,#252b36 62%,#20252f);border:1px solid #6c7384;box-shadow:0 32px 76px rgba(0,0,0,.48),inset 0 1px 0 rgba(255,255,255,.16)}.inner{border-radius:30px;padding:18px;background:linear-gradient(180deg,#1d222c,#171b23);border:1px solid #11151c}.display{height:310px;position:relative;overflow:hidden;border-radius:22px;background:linear-gradient(180deg,#0c1118,#11151d);border:1px solid #4c5362;box-shadow:inset 0 0 0 2px rgba(0,0,0,.45),inset 0 8px 24px rgba(0,0,0,.35)}.grid{position:absolute;inset:0;background:repeating-linear-gradient(90deg,transparent 0 18%,rgba(113,122,145,.12) 18.1% 18.25%,transparent 18.4% 36%)}.wave1,.wave2{position:absolute;bottom:-28px;height:210px;border-radius:50% 50% 0 0/75% 75% 0 0}.wave1{left:-2%;width:50%;background:radial-gradient(ellipse at 48% 70%,rgba(173,79,255,.88),rgba(78,57,255,.75) 48%,transparent 74%);transform:rotate(-6deg)}.wave2{left:29%;width:56%;background:radial-gradient(ellipse at 44% 72%,rgba(219,86,219,.84),rgba(255,126,67,.90) 52%,transparent 75%);transform:rotate(3deg)}.display-title{position:absolute;top:54px;left:0;right:0;text-align:center;font-size:25px;font-weight:950;letter-spacing:-1.5px;color:#737b8b}.display-title b{color:#9ca3b1}.display-title i{font-style:normal;color:var(--orange)}.urlrow{position:absolute;left:26px;right:26px;bottom:22px;display:grid;grid-template-columns:1fr 126px;gap:10px;z-index:8}.urlbox{height:52px;border-radius:12px;background:rgba(7,10,15,.84);border:1px solid #505767;display:flex;align-items:center;padding:0 14px}.urlbox input{width:100%;border:0;outline:0;background:transparent;color:#d0d5df;font-size:13px}.analyse,.download,.smallbtn{border:1px solid #596171;border-radius:12px;color:#d0d5df;background:linear-gradient(#383f4d,#252b36);font-weight:900;cursor:pointer}.analyse{font-size:11px}.analyse:disabled,.download:disabled{opacity:.42;cursor:not-allowed}.result{position:absolute;inset:22px 20px 82px;display:none;grid-template-columns:210px 1fr;gap:18px;align-items:center;padding:14px;border-radius:16px;background:rgba(8,11,16,.92);border:1px solid #3c4351;backdrop-filter:blur(12px);z-index:7}.result.show{display:grid}.thumb{aspect-ratio:16/9;border-radius:11px;overflow:hidden;background:#202530;border:1px solid #474e5d;position:relative}.thumb img{width:100%;height:100%;object-fit:cover}.duration{position:absolute;right:6px;bottom:6px;padding:4px 6px;background:rgba(0,0,0,.74);border-radius:6px;font-size:9px}.media-title{font-size:16px;font-weight:900;line-height:1.3}.media-sub{margin-top:7px;color:var(--muted);font-size:11px}.platform{display:inline-block;margin-top:9px;padding:5px 8px;border-radius:7px;background:#232936;border:1px solid #434a5a;color:#929aaa;font-size:9px;font-weight:900}.message{display:none;margin:11px 2px 0;padding:10px 12px;border-radius:10px;font-size:11px}.message.status{color:#8f98a7}.message.error{color:var(--danger);background:rgba(255,105,120,.07);border:1px solid rgba(255,105,120,.18)}.controls{display:grid;grid-template-columns:1fr 1fr 1fr 1.15fr;gap:11px;margin-top:18px}.module{min-height:205px;border-radius:21px;padding:15px 14px;background:linear-gradient(155deg,#303644,#252b37 48%,#1f242e);border:1px solid #404756;box-shadow:inset 0 1px 0 rgba(255,255,255,.07),0 8px 18px rgba(0,0,0,.2)}.module-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:13px}.module-title{font-size:13px;font-weight:950}.power{width:22px;height:22px;border-radius:50%;border:1px solid #151922;background:#252b36;display:grid;place-items:center}.power:after{content:"";width:7px;height:7px;border-radius:50%;background:var(--blue);box-shadow:0 0 9px rgba(104,164,255,.75)}.orange .power:after{background:var(--orange)}.purple .power:after{background:var(--pink)}.knob{width:74px;height:74px;border-radius:50%;position:relative;margin:4px auto 12px;background:radial-gradient(circle at 42% 38%,#3c4352 0 8%,#303644 31%,#222833 66%,#181c25 100%);border:1px solid #505767;box-shadow:inset 0 -6px 12px rgba(0,0,0,.35),0 6px 12px rgba(0,0,0,.35)}.knob:before{content:"";position:absolute;top:14px;left:50%;width:3px;height:20px;background:var(--blue);transform:translateX(-50%) rotate(58deg);transform-origin:50% 25px}.orange .knob:before{background:var(--orange)}.screen,.field{border-radius:12px;background:#11151d;border:1px solid #343b49;padding:9px 10px;box-shadow:inset 0 0 10px rgba(0,0,0,.62)}.screen small,.field small{display:block;font-size:8px;color:#6c7484;font-weight:900;margin-bottom:6px}.screen select,.field select{width:100%;border:0;outline:0;background:transparent;color:#84b3ff;font-size:12px;font-weight:900}.screen select option,.field select option{background:#151922;color:#e0e4eb}.modebuttons{display:grid;gap:9px}.modebtn{height:50px;border-radius:13px;border:1px solid #424958;background:linear-gradient(#323846,#252b36);color:#8e96a6;font-size:10px;font-weight:900;cursor:pointer}.modebtn.active{color:#9fc0ff;border-color:#5d7db0}.output-grid{display:grid;gap:9px}.download{width:100%;height:50px;margin-top:10px;font-size:10px}.tracks-panel{display:none;margin-top:12px;border-radius:19px;padding:14px;background:linear-gradient(155deg,#2a303c,#202631);border:1px solid #404756}.tracks-panel.show{display:block}.tracks-head{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.track-toggle{display:flex;align-items:center;gap:10px;font-size:11px;font-weight:900;cursor:pointer}.track-toggle input{display:none}.switch{width:42px;height:23px;border-radius:999px;background:#171c25;border:1px solid #444c5c;position:relative}.switch:after{content:"";position:absolute;top:3px;left:3px;width:15px;height:15px;border-radius:50%;background:#7a8291;transition:.18s}.track-toggle input:checked+.switch:after{left:22px;background:#86b5ff}.track-toggle input:checked+.switch{border-color:#5e7dab}.track-tools{display:flex;align-items:center;gap:7px}.smallbtn{padding:7px 9px;font-size:9px}.chapter-meta{font-size:9px;color:#7e8798}.tracklist{display:none;margin-top:12px;border-top:1px solid #39404d;padding-top:10px;grid-template-columns:1fr 1fr;gap:7px}.tracklist.show{display:grid}.track{display:grid;grid-template-columns:20px 30px minmax(0,1fr) auto;gap:8px;align-items:center;padding:9px;border-radius:10px;background:#151a23;border:1px solid #303744;font-size:10px;cursor:pointer}.track input{accent-color:#78a9ff;width:15px;height:15px}.track b{color:#8db9ff;font-size:9px}.track span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.track em{font-style:normal;color:#677081;font-size:9px}.track.off{opacity:.48}.processing-note{display:none;margin-top:10px;color:#8d96a5;font-size:10px}.processing-note.show{display:block}
@media(max-width:900px){.page{width:min(100% - 18px,760px)}.top{padding:4px 14px 12px}.device{border-radius:30px;padding:12px}.inner{border-radius:22px;padding:12px}.display{height:285px}.controls{grid-template-columns:1fr 1fr}.tracklist{grid-template-columns:1fr}}
@media(max-width:560px){body{background:linear-gradient(180deg,#292e3d,#171a23)}.page{width:100%;padding:10px 8px 26px}.top{padding:3px 8px 9px;font-size:10px}.device{border-radius:24px;padding:8px}.inner{border-radius:18px;padding:8px}.display{height:258px;border-radius:16px}.display-title{top:32px;font-size:19px}.urlrow{left:10px;right:10px;bottom:10px;grid-template-columns:1fr}.urlbox{height:48px}.analyse{height:46px}.result{inset:10px 8px 116px;grid-template-columns:88px 1fr;gap:9px;padding:8px}.media-title{font-size:12px}.media-sub,.platform{font-size:8px}.controls{grid-template-columns:1fr;gap:8px;margin-top:9px}.module{min-height:auto;border-radius:16px;padding:12px}.knob{display:none}.modebuttons{grid-template-columns:1fr 1fr}.modebtn{height:48px}.screen,.field{min-height:58px}.download{height:52px}.tracks-panel{border-radius:16px;padding:12px}.tracks-head{align-items:flex-start}.track-tools{width:100%;display:grid;grid-template-columns:1fr 1fr}.smallbtn{height:40px}.tracklist{grid-template-columns:1fr}.track{grid-template-columns:20px 28px minmax(0,1fr) auto;padding:10px}.wave1,.wave2{height:165px}.wave1{width:64%}.wave2{left:28%;width:72%}}
</style></head><body>
<div class="page"><div class="top"><div class="brand"><b>PANDA</b>.DOWNLOAD</div><div class="online">V5 ONLINE</div></div><section class="device"><div class="inner">
<div class="display"><div class="grid"></div><div class="wave1"></div><div class="wave2"></div><div class="display-title"><b>PANDA</b> DOWNLOAD<i>.</i></div><div class="result" id="result"><div class="thumb"><img id="thumb"><div class="duration" id="duration"></div></div><div><div class="media-title" id="title"></div><div class="media-sub" id="uploader"></div><div class="platform" id="platform"></div></div></div><div class="urlrow"><div class="urlbox"><input id="url" placeholder="Colle une URL vidéo ou audio..." autocomplete="off"></div><button class="analyse" id="analyse">ANALYSER</button></div></div>
<div class="message status" id="status">Analyse en cours…</div><div class="message error" id="error"></div>
<form method="POST" action="/download" target="_blank" id="downloadForm"><input type="hidden" name="url" id="durl"><input type="hidden" name="mode" id="dmode" value="video"><input type="hidden" name="quality" id="dquality" value="best"><input type="hidden" name="split_tracks" id="dsplit" value="0"><input type="hidden" name="selected_tracks" id="dselected" value="[]">
<div class="controls"><div class="module"><div class="module-head"><span class="module-title">MODE</span><span class="power"></span></div><div class="modebuttons"><button type="button" class="modebtn active" id="videoMode">VIDÉO</button><button type="button" class="modebtn" id="audioMode">AUDIO</button></div></div>
<div class="module"><div class="module-head"><span class="module-title">VIDEO</span><span class="power"></span></div><div class="knob"></div><div class="screen"><small>QUALITÉ</small><select id="quality"><option value="best">MEILLEURE</option></select></div></div>
<div class="module orange"><div class="module-head"><span class="module-title">AUDIO</span><span class="power"></span></div><div class="knob"></div><div class="screen"><small>QUALITÉ</small><select name="audio_quality" id="audioQuality"><option value="best">BEST SOURCE</option><option value="320">320 KBPS</option><option value="256">256 KBPS</option><option value="192">192 KBPS</option><option value="128">128 KBPS</option></select></div></div>
<div class="module purple"><div class="module-head"><span class="module-title">OUTPUT</span><span class="power"></span></div><div class="output-grid"><div class="field" id="videoOutput"><small>FORMAT VIDÉO</small><select name="video_format"><option value="mp4">MP4</option><option value="mkv">MKV</option><option value="webm">WEBM</option><option value="mov">MOV</option><option value="avi">AVI</option></select></div><div class="field" id="audioOutput" style="display:none"><small>FORMAT AUDIO</small><select name="audio_format"><option value="original">ORIGINAL</option><option value="mp3">MP3</option><option value="m4a">M4A / AAC</option><option value="opus">OPUS</option><option value="flac">FLAC</option><option value="wav">WAV</option><option value="ogg">OGG</option></select></div></div><button class="download" id="download">↓ TÉLÉCHARGER LA VIDÉO</button></div></div>
<div class="tracks-panel" id="tracksPanel"><div class="tracks-head"><label class="track-toggle"><input type="checkbox" id="splitTracks"><span class="switch"></span><span>SÉPARER EN TRACKS</span></label><span class="chapter-meta" id="chapterMeta">Aucun chapitre détecté</span><div class="track-tools"><button type="button" class="smallbtn" id="selectAll">TOUT SÉLECTIONNER</button><button type="button" class="smallbtn" id="selectNone">TOUT DÉSÉLECTIONNER</button></div></div><div class="tracklist" id="tracklist"></div><div class="processing-note" id="processingNote">Le téléchargement s’ouvre dans un nouvel onglet pour garder cette page réactive.</div></div></form><iframe name="downloadSink" style="display:none"></iframe>
</div></section></div>
<script>
const $=s=>document.querySelector(s);const url=$('#url'),analyse=$('#analyse'),result=$('#result'),status=$('#status'),error=$('#error'),quality=$('#quality'),videoMode=$('#videoMode'),audioMode=$('#audioMode'),videoOutput=$('#videoOutput'),audioOutput=$('#audioOutput'),download=$('#download'),tracksPanel=$('#tracksPanel'),splitTracks=$('#splitTracks'),tracklist=$('#tracklist'),chapterMeta=$('#chapterMeta'),selectAll=$('#selectAll'),selectNone=$('#selectNone'),form=$('#downloadForm'),processingNote=$('#processingNote');let mode='video',audioOnly=false,chapters=[];
function fmt(s){if(s==null)return'';s=Math.max(0,Math.floor(s));const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),x=s%60;return h?`${h}:${String(m).padStart(2,'0')}:${String(x).padStart(2,'0')}`:`${m}:${String(x).padStart(2,'0')}`}
function checkedIndexes(){return [...document.querySelectorAll('.track-check:checked')].map(x=>Number(x.value))}
function refreshTracks(){const enabled=mode==='audio'&&splitTracks.checked&&chapters.length>0;tracklist.classList.toggle('show',enabled);document.querySelectorAll('.track').forEach(row=>{const cb=row.querySelector('.track-check');row.classList.toggle('off',!cb.checked)});const count=checkedIndexes().length;if(mode==='video')download.textContent='↓ TÉLÉCHARGER LA VIDÉO';else if(enabled)download.textContent=`↓ TÉLÉCHARGER ${count} TRACK${count>1?'S':''} (.ZIP)`;else download.textContent='↓ TÉLÉCHARGER L’AUDIO';download.disabled=enabled&&count===0}
function setMode(next){if(audioOnly&&next==='video')return;mode=next;videoMode.classList.toggle('active',mode==='video');audioMode.classList.toggle('active',mode==='audio');videoMode.disabled=audioOnly;quality.disabled=mode==='audio';videoOutput.style.display=mode==='video'?'block':'none';audioOutput.style.display=mode==='audio'?'block':'none';tracksPanel.classList.toggle('show',mode==='audio');if(mode!=='audio')splitTracks.checked=false;refreshTracks()}
function fillQualities(values){quality.innerHTML='<option value="best">MEILLEURE</option>';[...new Set(values||[])].sort((a,b)=>b-a).forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v>=2160?`${v}p · 4K`:v>=1440?`${v}p · 2K`:v>=1080?`${v}p · FULL HD`:v>=720?`${v}p · HD`:`${v}p`;quality.appendChild(o)})}
function renderChapters(list){chapters=list||[];splitTracks.checked=false;splitTracks.disabled=!chapters.length;chapterMeta.textContent=chapters.length?`${chapters.length} CHAPITRES · ${chapters.length} SÉLECTIONNÉS`:'AUCUN CHAPITRE DÉTECTÉ';tracklist.innerHTML='';chapters.forEach((c,i)=>{const label=document.createElement('label');label.className='track';label.innerHTML=`<input class="track-check" type="checkbox" value="${i}" checked><b>${String(c.number||i+1).padStart(2,'0')}</b><span></span><em>${fmt(c.start)}</em>`;label.querySelector('span').textContent=c.title||`Track ${i+1}`;label.querySelector('input').addEventListener('change',()=>{chapterMeta.textContent=`${chapters.length} CHAPITRES · ${checkedIndexes().length} SÉLECTIONNÉS`;refreshTracks()});tracklist.appendChild(label)});refreshTracks()}
selectAll.addEventListener('click',()=>{document.querySelectorAll('.track-check').forEach(x=>x.checked=true);chapterMeta.textContent=`${chapters.length} CHAPITRES · ${chapters.length} SÉLECTIONNÉS`;refreshTracks()});selectNone.addEventListener('click',()=>{document.querySelectorAll('.track-check').forEach(x=>x.checked=false);chapterMeta.textContent=`${chapters.length} CHAPITRES · 0 SÉLECTIONNÉ`;refreshTracks()});splitTracks.addEventListener('change',refreshTracks);videoMode.addEventListener('click',()=>setMode('video'));audioMode.addEventListener('click',()=>setMode('audio'));
function syncForm(){const selected=checkedIndexes();$('#durl').value=url.value.trim();$('#dmode').value=mode;$('#dquality').value=quality.value;$('#dsplit').value=(mode==='audio'&&splitTracks.checked)?'1':'0';$('#dselected').value=JSON.stringify(selected);processingNote.classList.add('show')}
form.addEventListener('submit',e=>{syncForm();if(mode==='audio'&&splitTracks.checked&&checkedIndexes().length===0){e.preventDefault();error.textContent='Sélectionne au moins une track.';error.style.display='block'}});
async function run(){error.style.display='none';result.classList.remove('show');const value=url.value.trim();if(!value){error.textContent='Colle une URL valide.';error.style.display='block';return}status.style.display='block';analyse.disabled=true;try{const fd=new FormData();fd.append('url',value);const r=await fetch('/info',{method:'POST',body:fd});const d=await r.json();if(!r.ok)throw new Error(d.error||'Analyse impossible.');$('#thumb').src=d.thumbnail||'';$('#title').textContent=d.title||'Média';$('#uploader').textContent=d.uploader||'';$('#duration').textContent=fmt(d.duration);$('#platform').textContent=(d.platform||'MEDIA').toUpperCase();fillQualities(d.qualities);renderChapters(d.chapters);audioOnly=!!d.audio_only;setMode(audioOnly?'audio':'video');download.disabled=!!d.blocked;result.classList.add('show')}catch(e){error.textContent=e.message;error.style.display='block'}finally{status.style.display='none';analyse.disabled=false}}
analyse.addEventListener('click',run);url.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();run()}});setMode('video');renderChapters([]);
</script></body></html>'''


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "panda-download",
        "youtube_cookies": bool(cookie_path()),
        "deno": shutil.which("deno") is not None,
        "ui": "plugin-v5",
        "chapter_split": True,
        "track_selection": True,
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
            return {"platform":"spotify","title":"Spotify","uploader":"spotDL","thumbnail":"","duration":None,"qualities":[],"audio_only":True,"blocked":False,"chapters":[]}
        if platform in {"tidal", "deezer"}:
            return {"platform":platform,"title":platform.upper(),"uploader":"","thumbnail":"","duration":None,"qualities":[],"audio_only":True,"blocked":True,"chapters":[]}
        opts = base_ydl_options()
        opts.update({"skip_download": True, "outtmpl": "%(id)s.%(ext)s"})
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(clean, download=False)
        if not info:
            raise RuntimeError("Aucun média détecté.")
        qualities = sorted({int(f["height"]) for f in info.get("formats", []) if f.get("height") and f.get("vcodec") != "none"}, reverse=True)
        return {
            "platform": platform if platform != "generic" else (info.get("extractor_key") or "media"),
            "title": info.get("title"),
            "uploader": info.get("uploader") or info.get("channel") or info.get("artist"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "qualities": qualities,
            "audio_only": not bool(qualities),
            "blocked": False,
            "chapters": extract_chapters(info),
        }
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": friendly_error(exc)})


@app.post("/download")
def download_media(
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    mode: str = Form("video"),
    quality: str = Form("best"),
    audio_quality: str = Form("best"),
    video_format: str = Form("mp4"),
    audio_format: str = Form("original"),
    split_tracks: str = Form("0"),
    selected_tracks: str = Form("[]"),
):
    temp_dir = tempfile.mkdtemp(prefix="panda_v5_")
    try:
        clean = validate_public_url(url)
        platform = detect_platform(clean)
        mode = "audio" if mode == "audio" else "video"
        video_format = video_format if video_format in VIDEO_FORMATS else "mp4"
        audio_format = audio_format if audio_format in AUDIO_FORMATS else "original"
        audio_quality = audio_quality if audio_quality in AUDIO_BITRATES else "best"
        do_split = mode == "audio" and split_tracks == "1"

        if platform in {"tidal", "deezer"}:
            raise RuntimeError("Cette source n’est pas disponible pour le téléchargement direct.")
        if platform == "spotify":
            raise RuntimeError("Le mode Spotify n’est pas disponible dans le splitter V5.")

        output = os.path.join(temp_dir, "%(id)s.%(ext)s")
        opts = base_ydl_options()
        opts.update({"outtmpl": output, "restrictfilenames": True, "trim_file_name": 80, "windowsfilenames": True})
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

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(clean, download=True)

        title = info.get("title") or info.get("id") or "download"
        files = media_files(temp_dir)
        if not files:
            raise RuntimeError("Aucun fichier final généré.")
        source = max(files, key=os.path.getsize)

        if mode == "video":
            final_file = convert_video(source, video_format, temp_dir)
            ext = os.path.splitext(final_file)[1]
            filename = pretty_name(title, ext)
            media_type = mimetypes.guess_type(final_file)[0] or "application/octet-stream"
        elif do_split:
            chapters = extract_chapters(info)
            if not chapters:
                raise RuntimeError("Aucun chapitre ou timestamp exploitable n’a été détecté.")
            selected = parse_selected_tracks(selected_tracks, len(chapters))
            final_file = split_selected_to_zip(source, chapters, selected, audio_format, audio_quality, temp_dir, title)
            try:
                if source != final_file and os.path.isfile(source):
                    os.remove(source)
            except OSError:
                pass
            filename = pretty_name(f"{title} - selected tracks", ".zip")
            media_type = "application/zip"
        else:
            final_file = convert_audio(source, audio_format, audio_quality, temp_dir)
            ext = os.path.splitext(final_file)[1]
            filename = pretty_name(title, ext)
            media_type = mimetypes.guess_type(final_file)[0] or "application/octet-stream"

        background_tasks.add_task(shutil.rmtree, temp_dir, ignore_errors=True)
        return FileResponse(final_file, media_type=media_type, filename=filename, background=background_tasks)
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return JSONResponse(status_code=500, content={"error": friendly_error(exc)})
