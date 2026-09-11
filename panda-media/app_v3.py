from fastapi import FastAPI, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from urllib.parse import urlparse
import glob
import ipaddress
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import tempfile

import yt_dlp

app = FastAPI(title="panda.download.com")

COOKIE_SECRET = os.getenv("YTDLP_COOKIES_FILE", "/secrets/youtube-cookies.txt")
USER_AGENT = os.getenv("YTDLP_USER_AGENT", "").strip()
_COOKIE_TMP = None

VIDEO_FORMATS = {"mp4", "mkv", "webm", "mov", "avi"}
AUDIO_FORMATS = {"original", "mp3", "m4a", "opus", "flac", "wav", "ogg"}
AUDIO_BITRATES = {"best", "320", "256", "192", "128"}

HTML = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#202432">
<title>panda.download.com</title>
<style>
:root{--page:#202432;--page2:#151821;--shell:#353b49;--shell2:#252b36;--panel:#11151d;--panel2:#1a1f29;--line:#454c5b;--text:#d8dce6;--muted:#7d8596;--purple:#7956ff;--pink:#dc5fe4;--orange:#ff9a32;--blue:#68a4ff;--danger:#ff929d;--ok:#92b9ff}
*{box-sizing:border-box}
html{scrollbar-width:none}
html::-webkit-scrollbar,body::-webkit-scrollbar,*::-webkit-scrollbar{width:0;height:0;display:none}
html,body{margin:0;min-height:100%;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--page2);color:var(--text)}
body{min-height:100vh;overflow-x:hidden;background:radial-gradient(circle at 50% -18%,rgba(103,111,145,.30),transparent 43%),linear-gradient(180deg,#313548 0%,#232735 40%,#151821 100%)}
button,input,select{font:inherit}button{-webkit-tap-highlight-color:transparent}.page{width:min(1080px,calc(100% - 26px));margin:auto;padding:28px 0 48px}.topline{display:grid;grid-template-columns:220px 1fr 220px;align-items:center;gap:18px;padding:5px 38px 18px;color:#858c9d;font-size:12px;font-weight:850;letter-spacing:.08em;text-transform:uppercase}.brand{color:#aeb4c2;white-space:nowrap}.brand b{color:#d1d5de}.preset{height:30px;border:1px solid #596071;border-radius:999px;display:flex;align-items:center;justify-content:center;gap:12px;background:rgba(19,22,30,.25);color:#a6acb8}.status-dot{width:7px;height:7px;border-radius:50%;background:#7ba7ff;box-shadow:0 0 10px rgba(123,167,255,.8)}.tools{display:flex;justify-content:flex-end;gap:8px;color:#72798a}.device{position:relative;border-radius:43px;padding:18px;background:linear-gradient(145deg,#4c5262,#363c4b 20%,#282e3a 55%,#20252f);border:1px solid #6d7484;box-shadow:0 30px 74px rgba(0,0,0,.52),inset 0 1px 0 rgba(255,255,255,.18),inset 0 -2px 0 rgba(0,0,0,.42)}.inner{position:relative;border-radius:31px;padding:18px 22px 22px;background:linear-gradient(180deg,#1d222c,#171b23);border:1px solid #12161d;box-shadow:inset 0 0 0 1px rgba(255,255,255,.035),inset 0 14px 30px rgba(0,0,0,.18)}
.display{height:310px;position:relative;overflow:hidden;border-radius:22px;background:linear-gradient(to right,transparent 0 13%,rgba(119,127,147,.12) 13.1% 13.25%,transparent 13.4% 31%,rgba(119,127,147,.12) 31.1% 31.25%,transparent 31.4% 50%,rgba(119,127,147,.12) 50.1% 50.25%,transparent 50.4% 69%,rgba(119,127,147,.12) 69.1% 69.25%,transparent 69.4% 87%,rgba(119,127,147,.12) 87.1% 87.25%,transparent 87.4%),linear-gradient(180deg,#0c1118,#11151d);border:1px solid #4c5362;box-shadow:inset 0 0 0 2px rgba(0,0,0,.48),inset 0 8px 24px rgba(0,0,0,.38)}.freqs{position:absolute;top:12px;left:21px;right:21px;display:flex;justify-content:space-between;font-size:9px;color:#5f6778;z-index:5}.spectrum{position:absolute;inset:46px 0 0;opacity:.35;background:repeating-linear-gradient(90deg,transparent 0 16px,rgba(115,123,143,.26) 17px,transparent 19px);clip-path:polygon(0 85%,4% 66%,8% 75%,12% 53%,16% 69%,20% 45%,25% 78%,30% 56%,35% 68%,40% 37%,45% 73%,50% 50%,55% 61%,60% 29%,65% 72%,70% 44%,75% 66%,80% 25%,85% 71%,90% 41%,95% 59%,100% 35%,100% 100%,0 100%)}.wave1,.wave2{position:absolute;bottom:-20px;height:205px;border-radius:50% 50% 0 0/75% 75% 0 0;pointer-events:none}.wave1{left:-15px;width:48%;background:radial-gradient(ellipse at 48% 70%,rgba(183,77,255,.9),rgba(84,57,255,.8) 48%,rgba(84,57,255,.10) 72%,transparent 73%);transform:rotate(-7deg)}.wave2{left:28%;width:55%;background:radial-gradient(ellipse at 44% 72%,rgba(224,91,220,.88),rgba(255,125,68,.92) 52%,rgba(255,145,45,.10) 74%,transparent 75%);transform:rotate(3deg)}.display-title{position:absolute;top:64px;left:0;right:0;text-align:center;z-index:4;font-size:25px;font-weight:950;letter-spacing:-1.6px;color:#697181;text-shadow:0 1px 0 #10131a}.display-title b{color:#8a92a2}.display-title i{font-style:normal;color:var(--orange)}
.urlrow{position:absolute;left:26px;right:26px;bottom:22px;z-index:8;display:grid;grid-template-columns:1fr 126px;gap:10px}.urlbox{height:52px;border-radius:12px;background:rgba(8,11,16,.82);border:1px solid #505767;display:flex;align-items:center;padding:0 14px;box-shadow:inset 0 1px 6px rgba(0,0,0,.6)}.urlbox input{width:100%;border:0;outline:0;background:transparent;color:#cbd1dc;font-size:13px}.urlbox input::placeholder{color:#596171}.analyse{border:1px solid #596171;border-radius:12px;color:#c0c6d1;background:linear-gradient(#363c49,#252b36);font-size:11px;font-weight:900;letter-spacing:.05em;cursor:pointer;box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 4px 10px rgba(0,0,0,.24)}.analyse:hover{filter:brightness(1.12)}.analyse:disabled{opacity:.45;cursor:not-allowed}
.result{position:absolute;inset:34px 20px 82px;z-index:7;display:none;grid-template-columns:210px 1fr;gap:18px;align-items:center;padding:14px;border-radius:16px;background:rgba(8,11,16,.9);border:1px solid #3b4250;backdrop-filter:blur(12px)}.result.show{display:grid;animation:appear .2s ease}@keyframes appear{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}.thumb{aspect-ratio:16/9;border-radius:11px;overflow:hidden;background:#202530;position:relative;border:1px solid #474e5d}.thumb img{width:100%;height:100%;object-fit:cover;display:block}.duration{position:absolute;right:6px;bottom:6px;padding:4px 6px;background:rgba(0,0,0,.74);border-radius:6px;font-size:9px}.media-title{font-size:16px;font-weight:900;line-height:1.3;color:#d8dce5}.media-sub{margin-top:7px;color:#7f8797;font-size:11px}.platform{display:inline-block;margin-top:10px;padding:5px 8px;border-radius:7px;background:#232936;border:1px solid #434a5a;color:#929aaa;font-size:9px;font-weight:900;letter-spacing:.08em}.message{display:none;margin:12px 2px 0;padding:10px 12px;border-radius:10px;font-size:11px;line-height:1.4}.message.status{color:#8992a2}.message.error{color:var(--danger);background:rgba(255,105,120,.07);border:1px solid rgba(255,105,120,.18)}.message.notice{color:#aeb5c1;background:#1b202a;border:1px solid #323946}
.controls{display:grid;grid-template-columns:1fr 1fr 1fr 1.12fr;gap:11px;margin-top:18px}.module{min-height:210px;border-radius:21px;padding:15px 14px 13px;background:linear-gradient(155deg,#303644,#252b37 48%,#1f242e);border:1px solid #404756;box-shadow:inset 0 1px 0 rgba(255,255,255,.07),inset 0 -1px 0 rgba(0,0,0,.45),0 8px 18px rgba(0,0,0,.2)}.module-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:13px}.module-title{font-size:13px;font-weight:950;color:#c1c6d0}.power{width:22px;height:22px;border-radius:50%;border:1px solid #151922;background:linear-gradient(#303745,#1b202a);display:grid;place-items:center;box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 2px 5px rgba(0,0,0,.4)}.power:after{content:"";width:7px;height:7px;border-radius:50%;background:var(--blue);box-shadow:0 0 9px rgba(104,164,255,.75)}.orange .power:after{background:var(--orange);box-shadow:0 0 9px rgba(255,154,50,.7)}.purple .power:after{background:var(--pink);box-shadow:0 0 9px rgba(220,95,228,.7)}.knob{width:76px;height:76px;border-radius:50%;position:relative;margin:4px auto 12px;background:radial-gradient(circle at 42% 38%,#3c4352 0 8%,#303644 31%,#222833 66%,#181c25 100%);border:1px solid #505767;box-shadow:inset 0 1px 1px rgba(255,255,255,.1),inset 0 -6px 12px rgba(0,0,0,.35),0 6px 12px rgba(0,0,0,.35)}.knob:before{content:"";position:absolute;top:14px;left:50%;width:3px;height:20px;border-radius:3px;background:var(--blue);transform:translateX(-50%) rotate(58deg);transform-origin:50% 25px}.orange .knob:before{background:var(--orange)}.purple .knob:before{background:var(--pink)}
.screen{min-height:74px;border-radius:12px;background:#11151d;border:1px solid #343b49;box-shadow:inset 0 0 10px rgba(0,0,0,.65);padding:9px 10px;display:flex;flex-direction:column;justify-content:center}.screen small{font-size:8px;color:#6c7484;font-weight:900;letter-spacing:.08em;margin-bottom:6px}.screen select{width:100%;border:0;outline:0;background:transparent;color:#7fb0ff;font-size:12px;font-weight:900}.orange .screen select{color:#ffac49}.purple .screen select{color:#e07dea}.screen select option{background:#151922;color:#d7dbe5}.modebuttons{display:grid;grid-template-columns:1fr;gap:9px}.modebtn{height:50px;border-radius:13px;border:1px solid #424958;background:linear-gradient(#323846,#252b36);color:#8e96a6;font-size:10px;font-weight:900;cursor:pointer}.modebtn.active{color:#9fc0ff;border-color:#5d7db0;box-shadow:inset 0 0 0 1px rgba(104,164,255,.12),0 0 12px rgba(104,164,255,.08)}.audio-display{height:72px;border-radius:12px;background:#11151d;border:1px solid #343b49;display:grid;place-items:center;overflow:hidden;box-shadow:inset 0 0 10px rgba(0,0,0,.65);margin-bottom:9px}.bars{display:flex;align-items:center;gap:2px;height:50px}.bars i{width:3px;border-radius:3px;background:linear-gradient(#d959e9,#8058ff)}.bars i:nth-child(1){height:18px}.bars i:nth-child(2){height:30px}.bars i:nth-child(3){height:45px}.bars i:nth-child(4){height:24px}.bars i:nth-child(5){height:50px}.bars i:nth-child(6){height:36px}.bars i:nth-child(7){height:44px}.bars i:nth-child(8){height:22px}.bars i:nth-child(9){height:47px}.bars i:nth-child(10){height:31px}.output-grid{display:grid;gap:9px}.field{border-radius:12px;background:#11151d;border:1px solid #343b49;padding:9px 10px}.field small{display:block;font-size:8px;color:#6c7484;font-weight:900;letter-spacing:.08em;margin-bottom:5px}.field select{width:100%;border:0;outline:0;background:transparent;color:#c6cad3;font-size:11px;font-weight:900}.field select option{background:#151922;color:#e0e4eb}.download{width:100%;height:50px;margin-top:10px;border:1px solid #5a6170;border-radius:13px;color:#d4d8e1;background:linear-gradient(#373e4c,#252b36);font-size:10px;font-weight:950;letter-spacing:.04em;cursor:pointer;box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 4px 12px rgba(0,0,0,.24)}.download:hover{filter:brightness(1.12)}.download:disabled{opacity:.42;cursor:not-allowed}.note{margin-top:10px;color:#687181;font-size:9px;line-height:1.35}
@media(max-width:900px){.page{width:min(100% - 18px,760px)}.topline{grid-template-columns:1fr auto;padding:4px 14px 12px}.preset{display:none}.device{border-radius:30px;padding:12px}.inner{border-radius:22px;padding:12px}.display{height:282px}.controls{grid-template-columns:1fr 1fr}.module{min-height:200px}}
@media(max-width:560px){body{background:linear-gradient(180deg,#2d3242 0,#1b1f2a 42%,#13161d 100%)}.page{width:100%;padding:10px 8px 28px}.topline{grid-template-columns:1fr auto;padding:3px 9px 10px;font-size:10px}.tools{font-size:9px}.device{border-radius:24px;padding:8px;box-shadow:0 18px 44px rgba(0,0,0,.46),inset 0 1px 0 rgba(255,255,255,.13)}.inner{border-radius:18px;padding:9px}.display{height:280px;border-radius:16px}.freqs{left:12px;right:12px;font-size:7px}.display-title{top:42px;font-size:20px}.urlrow{left:10px;right:10px;bottom:10px;grid-template-columns:1fr}.urlbox{height:48px}.analyse{height:46px}.result{inset:22px 9px 116px;grid-template-columns:92px 1fr;gap:10px;padding:9px;border-radius:12px}.media-title{font-size:13px}.media-sub{font-size:9px}.platform{font-size:8px;margin-top:6px}.controls{grid-template-columns:1fr;gap:8px;margin-top:9px}.module{min-height:0;border-radius:16px;padding:12px}.module-head{margin-bottom:9px}.module-title{font-size:12px}.knob{display:none}.modebuttons{grid-template-columns:1fr 1fr}.modebtn{height:48px}.screen{min-height:62px}.audio-display{height:58px}.output-grid{grid-template-columns:1fr 1fr}.download{height:54px;font-size:11px}.note{font-size:8px}}
@media(max-width:360px){.output-grid{grid-template-columns:1fr}.brand{font-size:9px}}
</style></head><body>
<div class="page"><div class="topline"><div class="brand"><b>PANDA</b>.DOWNLOAD.COM</div><div class="preset"><span class="status-dot"></span><span id="topStatus">READY</span></div><div class="tools">MEDIA ENGINE</div></div>
<section class="device"><div class="inner"><div class="display"><div class="freqs"><span>10</span><span>20</span><span>40</span><span>80</span><span>160</span><span>320</span></div><div class="spectrum"></div><div class="wave1"></div><div class="wave2"></div><div class="display-title"><b>PANDA</b> DOWNLOAD<i>•</i></div>
<div class="result" id="result"><div class="thumb"><img id="thumb" alt=""><div class="duration" id="duration"></div></div><div><div class="media-title" id="title"></div><div class="media-sub" id="uploader"></div><div class="platform" id="platform"></div></div></div>
<div class="urlrow"><div class="urlbox"><input id="url" autocomplete="off" placeholder="Colle une URL vidéo ou audio…"></div><button class="analyse" id="analyse">ANALYSER</button></div></div>
<div class="message status" id="status">Analyse en cours…</div><div class="message error" id="error"></div><div class="message notice" id="notice"></div>
<div class="controls">
<section class="module"><div class="module-head"><div class="module-title">MODE</div><div class="power"></div></div><div class="modebuttons"><button class="modebtn active" type="button" id="videoMode">VIDÉO</button><button class="modebtn" type="button" id="audioMode">AUDIO</button></div><div class="note">Choisis le type de média à récupérer.</div></section>
<section class="module orange"><div class="module-head"><div class="module-title">QUALITÉ</div><div class="power"></div></div><div class="knob"></div><div class="screen"><small id="qualityLabel">VIDEO QUALITY</small><select id="quality"><option value="best">BEST AVAILABLE</option></select><select id="audioQuality" style="display:none"><option value="best">SOURCE · BEST</option><option value="320">320 KBPS</option><option value="256">256 KBPS</option><option value="192">192 KBPS</option><option value="128">128 KBPS</option></select></div></section>
<section class="module purple"><div class="module-head"><div class="module-title">AUDIO</div><div class="power"></div></div><div class="audio-display"><div class="bars"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div></div><div class="screen"><small>ENCODING</small><span id="audioSummary" style="font-size:12px;font-weight:900;color:#e07dea">BEST SOURCE</span></div></section>
<section class="module"><div class="module-head"><div class="module-title">OUTPUT</div><div class="power"></div></div><div class="output-grid"><div class="field"><small>VIDEO FORMAT</small><select id="videoFormat"><option value="mp4">MP4</option><option value="mkv">MKV</option><option value="webm">WEBM</option><option value="mov">MOV</option><option value="avi">AVI</option></select></div><div class="field"><small>AUDIO FORMAT</small><select id="audioFormat"><option value="original">ORIGINAL</option><option value="mp3">MP3</option><option value="m4a">M4A / AAC</option><option value="opus">OPUS</option><option value="flac">FLAC</option><option value="wav">WAV</option><option value="ogg">OGG</option></select></div></div>
<form method="POST" action="/download" onsubmit="syncDownload()"><input type="hidden" name="url" id="durl"><input type="hidden" name="mode" id="dmode" value="video"><input type="hidden" name="quality" id="dquality" value="best"><input type="hidden" name="audio_quality" id="daudioQuality" value="best"><input type="hidden" name="video_format" id="dvideoFormat" value="mp4"><input type="hidden" name="audio_format" id="daudioFormat" value="original"><button class="download" id="download">DOWNLOAD VIDEO</button></form></section>
</div></div></section></div>
<script>
const $=s=>document.querySelector(s);const url=$('#url'),analyse=$('#analyse'),result=$('#result'),status=$('#status'),error=$('#error'),notice=$('#notice');const videoMode=$('#videoMode'),audioMode=$('#audioMode'),quality=$('#quality'),audioQuality=$('#audioQuality');const videoFormat=$('#videoFormat'),audioFormat=$('#audioFormat'),download=$('#download'),audioSummary=$('#audioSummary'),qualityLabel=$('#qualityLabel');let mode='video',audioOnly=false;
function fmtDuration(s){if(!s)return '';const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),x=Math.floor(s%60);return h?`${h}:${String(m).padStart(2,'0')}:${String(x).padStart(2,'0')}`:`${m}:${String(x).padStart(2,'0')}`}
function fillQualities(values){quality.innerHTML='<option value="best">BEST AVAILABLE</option>';[...new Set(values||[])].sort((a,b)=>b-a).forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v>=2160?`${v}p · 4K`:v>=1440?`${v}p · 2K`:v>=1080?`${v}p · FULL HD`:v>=720?`${v}p · HD`:`${v}p`;quality.appendChild(o)})}
function refreshUI(){const audio=mode==='audio';videoMode.classList.toggle('active',!audio);audioMode.classList.toggle('active',audio);videoMode.disabled=audioOnly;quality.style.display=audio?'none':'block';audioQuality.style.display=audio?'block':'none';qualityLabel.textContent=audio?'AUDIO QUALITY':'VIDEO QUALITY';videoFormat.disabled=audio;audioFormat.disabled=!audio;videoFormat.closest('.field').style.opacity=audio?'.38':'1';audioFormat.closest('.field').style.opacity=audio?'1':'.38';download.textContent=audio?'DOWNLOAD AUDIO':'DOWNLOAD VIDEO';const aq=audioQuality.value==='best'?'BEST SOURCE':audioQuality.value+' KBPS';audioSummary.textContent=audio?`${audioFormat.options[audioFormat.selectedIndex].text} · ${aq}`:'BEST AUDIO STREAM'}
function setMode(next){if(audioOnly&&next==='video')return;mode=next;refreshUI()}videoMode.onclick=()=>setMode('video');audioMode.onclick=()=>setMode('audio');audioQuality.onchange=refreshUI;audioFormat.onchange=refreshUI;videoFormat.onchange=refreshUI;
function syncDownload(){$('#durl').value=url.value.trim();$('#dmode').value=mode;$('#dquality').value=quality.value;$('#daudioQuality').value=audioQuality.value;$('#dvideoFormat').value=videoFormat.value;$('#daudioFormat').value=audioFormat.value}
async function run(){error.style.display=notice.style.display='none';result.classList.remove('show');const value=url.value.trim();if(!value){error.textContent='Colle une URL valide.';error.style.display='block';return}status.style.display='block';analyse.disabled=true;$('#topStatus').textContent='ANALYSING';try{const f=new FormData();f.append('url',value);const r=await fetch('/info',{method:'POST',body:f});const d=await r.json();if(!r.ok)throw new Error(d.error||'Analyse impossible.');$('#thumb').src=d.thumbnail||'';$('#title').textContent=d.title||'Média';$('#uploader').textContent=d.uploader||'';$('#duration').textContent=fmtDuration(d.duration);$('#platform').textContent=(d.platform||'MEDIA').toUpperCase();fillQualities(d.qualities);audioOnly=!!d.audio_only;setMode(audioOnly?'audio':'video');download.disabled=!!d.blocked;if(d.notice){notice.textContent=d.notice;notice.style.display='block'}result.classList.add('show');$('#topStatus').textContent='READY'}catch(e){error.textContent=e.message;error.style.display='block';$('#topStatus').textContent='ERROR'}finally{status.style.display='none';analyse.disabled=false}}
analyse.onclick=run;url.addEventListener('keydown',e=>{if(e.key==='Enter')run()});refreshUI();
</script></body></html>"""

def cookie_path():
    global _COOKIE_TMP
    if _COOKIE_TMP and os.path.isfile(_COOKIE_TMP): return _COOKIE_TMP
    if COOKIE_SECRET and os.path.isfile(COOKIE_SECRET):
        try:
            target="/tmp/youtube-cookies.txt";shutil.copyfile(COOKIE_SECRET,target);os.chmod(target,0o600);_COOKIE_TMP=target;return target
        except OSError:return None
    return None

def base_ydl_options():
    opts={"quiet":True,"no_warnings":True,"noplaylist":True,"retries":3,"fragment_retries":3,"restrictfilenames":True,"trim_file_name":80,"windowsfilenames":True}
    c=cookie_path()
    if c:opts["cookiefile"]=c
    if USER_AGENT:opts["http_headers"]={"User-Agent":USER_AGENT}
    return opts

def friendly_error(exc):
    text=str(exc);low=text.lower()
    if "[errno 22]" in low or "invalid argument" in low:return "Le moteur a reçu un chemin ou un fichier temporaire invalide. Réessaie avec la dernière version."
    if "sign in to confirm" in low and "bot" in low:return "YouTube refuse la session actuelle. Mets à jour les cookies YouTube puis redéploie."
    if "cookies" in low and "youtube" in low:return "Authentification YouTube requise. Mets à jour le secret youtube-cookies."
    return text[-1800:]

def validate_public_url(raw_url:str)->str:
    value=raw_url.strip();p=urlparse(value)
    if p.scheme not in {"http","https"} or not p.hostname:raise ValueError("URL HTTP/HTTPS invalide.")
    host=p.hostname.lower()
    if host in {"localhost","localhost.localdomain"}:raise ValueError("Hôte local refusé.")
    try:
        for info in socket.getaddrinfo(host,None):
            ip=ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:raise ValueError("Adresse réseau privée/interne refusée.")
    except socket.gaierror as exc:raise ValueError("Nom de domaine introuvable.") from exc
    return value

def detect_platform(raw_url:str)->str:
    host=(urlparse(raw_url).hostname or "").lower()
    if host.endswith("spotify.com"):return "spotify"
    if host.endswith("tidal.com"):return "tidal"
    if host.endswith("deezer.com"):return "deezer"
    if "instagram.com" in host:return "instagram"
    if "tiktok.com" in host:return "tiktok"
    if host=="youtu.be" or "youtube.com" in host:return "youtube"
    if "soundcloud.com" in host:return "soundcloud"
    if "vimeo.com" in host:return "vimeo"
    if host.endswith("x.com") or "twitter.com" in host:return "x"
    if "facebook.com" in host or host.endswith("fb.watch"):return "facebook"
    return "generic"

def media_files(temp_dir:str):return [p for p in glob.glob(os.path.join(temp_dir,"**","*"),recursive=True) if os.path.isfile(p) and not p.endswith((".part",".ytdl",".temp",".json",".spotdl"))]
def pretty_name(title,ext):
    name=re.sub(r'[\\/:*?"<>|\x00-\x1f]+',"_",title or "download").strip(" ._");return (name[:110] or "download")+ext

def run_ffmpeg(args):
    proc=subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",*args],capture_output=True,text=True,timeout=3300,check=False)
    if proc.returncode!=0:raise RuntimeError((proc.stderr or "FFmpeg conversion failed")[-1800:])

def convert_audio(src:str,fmt:str,bitrate:str,temp_dir:str)->str:
    if fmt=="original":return src
    out=os.path.join(temp_dir,f"audio-final.{fmt}");br=None if bitrate=="best" else f"{bitrate}k"
    if fmt=="mp3":args=["-i",src,"-vn","-c:a","libmp3lame","-b:a",br or "320k",out]
    elif fmt=="m4a":args=["-i",src,"-vn","-c:a","aac","-b:a",br or "256k","-movflags","+faststart",out]
    elif fmt=="opus":args=["-i",src,"-vn","-c:a","libopus","-b:a",br or "192k",out]
    elif fmt=="ogg":args=["-i",src,"-vn","-c:a","libvorbis","-b:a",br or "192k",out]
    elif fmt=="flac":args=["-i",src,"-vn","-c:a","flac",out]
    elif fmt=="wav":args=["-i",src,"-vn","-c:a","pcm_s16le",out]
    else:raise RuntimeError("Format audio non pris en charge.")
    run_ffmpeg(args);return out

def convert_video(src:str,fmt:str,temp_dir:str)->str:
    if fmt not in VIDEO_FORMATS:fmt="mp4"
    current=os.path.splitext(src)[1].lower().lstrip(".")
    if current==fmt:return src
    out=os.path.join(temp_dir,f"video-final.{fmt}")
    if fmt in {"mp4","mkv","mov"}:
        proc=subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y","-i",src,"-map","0:v:0","-map","0:a:0?","-c","copy",out],capture_output=True,text=True,timeout=3300,check=False)
        if proc.returncode==0:return out
    if fmt=="mp4":args=["-i",src,"-c:v","libx264","-preset","veryfast","-crf","20","-c:a","aac","-b:a","192k","-movflags","+faststart",out]
    elif fmt=="mov":args=["-i",src,"-c:v","libx264","-preset","veryfast","-crf","20","-c:a","aac","-b:a","192k","-movflags","+faststart",out]
    elif fmt=="mkv":args=["-i",src,"-c:v","libx264","-preset","veryfast","-crf","20","-c:a","aac","-b:a","192k",out]
    elif fmt=="webm":args=["-i",src,"-c:v","libvpx-vp9","-deadline","realtime","-cpu-used","5","-crf","31","-b:v","0","-c:a","libopus","-b:a","160k",out]
    else:args=["-i",src,"-c:v","mpeg4","-q:v","3","-c:a","libmp3lame","-b:a","192k",out]
    run_ffmpeg(args);return out

@app.get("/",response_class=HTMLResponse)
async def home():return HTML
@app.get("/health")
async def health():return {"status":"ok","service":"panda-download","youtube_cookies":bool(cookie_path()),"deno":shutil.which("deno") is not None,"ui":"plugin-v3"}
@app.get("/favicon.ico")
async def favicon():return Response(status_code=204)

@app.post("/info")
async def media_info(url:str=Form(...)):
    try:
        clean=validate_public_url(url);platform=detect_platform(clean)
        if platform=="spotify":return {"platform":"spotify","title":"Spotify","uploader":"spotDL","thumbnail":"","duration":None,"qualities":[],"audio_only":True,"blocked":False,"notice":None}
        if platform in {"tidal","deezer"}:return {"platform":platform,"title":platform.upper(),"uploader":"","thumbnail":"","duration":None,"qualities":[],"audio_only":True,"blocked":True,"notice":"Cette source n’est pas disponible pour le téléchargement direct."}
        opts=base_ydl_options();opts["skip_download"]=True;opts["outtmpl"]="%(id)s.%(ext)s"
        with yt_dlp.YoutubeDL(opts) as ydl:info=ydl.extract_info(clean,download=False)
        if not info:raise RuntimeError("Aucun média détecté.")
        qualities=sorted({int(f["height"]) for f in info.get("formats",[]) if f.get("height") and f.get("vcodec")!="none"},reverse=True)
        return {"platform":platform if platform!="generic" else (info.get("extractor_key") or "media"),"title":info.get("title"),"uploader":info.get("uploader") or info.get("channel") or info.get("artist"),"thumbnail":info.get("thumbnail"),"duration":info.get("duration"),"qualities":qualities,"audio_only":not bool(qualities),"blocked":False,"notice":None}
    except Exception as exc:return JSONResponse(status_code=400,content={"error":friendly_error(exc)})

@app.post("/download")
async def download_media(background_tasks:BackgroundTasks,url:str=Form(...),mode:str=Form("video"),quality:str=Form("best"),audio_quality:str=Form("best"),video_format:str=Form("mp4"),audio_format:str=Form("original")):
    temp_dir=tempfile.mkdtemp(prefix="panda_dl_")
    try:
        clean=validate_public_url(url);platform=detect_platform(clean);mode="audio" if mode=="audio" else "video";video_format=video_format if video_format in VIDEO_FORMATS else "mp4";audio_format=audio_format if audio_format in AUDIO_FORMATS else "original";audio_quality=audio_quality if audio_quality in AUDIO_BITRATES else "best"
        if platform in {"tidal","deezer"}:raise RuntimeError("Cette source n’est pas disponible pour le téléchargement direct.")
        title="download"
        if platform=="spotify":
            cmd=["python","-m","spotdl","download",clean,"--format","opus","--bitrate","disable","--threads","1","--output",os.path.join(temp_dir,"{track-id}.{output-ext}")];c=cookie_path()
            if c:cmd += ["--cookie-file",c]
            completed=subprocess.run(cmd,cwd=temp_dir,capture_output=True,text=True,timeout=3300,check=False)
            if completed.returncode!=0:raise RuntimeError((completed.stderr or completed.stdout or "spotDL a échoué.")[-1800:])
            files=media_files(temp_dir)
            if not files:raise RuntimeError("Aucun fichier audio généré.")
            if len(files)>1:final_file=shutil.make_archive(os.path.join(temp_dir,"spotify-download"),"zip",temp_dir);filename="spotify-download.zip";media_type="application/zip"
            else:
                source=files[0];final_file=convert_audio(source,audio_format,audio_quality,temp_dir);ext=os.path.splitext(final_file)[1];filename=pretty_name("spotify-download",ext);media_type=mimetypes.guess_type(final_file)[0] or "application/octet-stream"
        else:
            output=os.path.join(temp_dir,"%(id)s.%(ext)s");opts=base_ydl_options();opts.update({"outtmpl":output,"restrictfilenames":True,"trim_file_name":80,"windowsfilenames":True})
            if mode=="audio":opts["format"]="bestaudio/best"
            else:
                if quality=="best":selector="bv*+ba/b"
                else:
                    try:height=max(144,min(int(quality),4320))
                    except ValueError:height=1080
                    selector=f"bv*[height<={height}]+ba/b[height<={height}]"
                opts["format"]=selector;opts["merge_output_format"]="mkv"
            with yt_dlp.YoutubeDL(opts) as ydl:info=ydl.extract_info(clean,download=True);title=info.get("title") or info.get("id") or "download"
            files=media_files(temp_dir)
            if not files:raise RuntimeError("Aucun fichier final généré.")
            source=max(files,key=os.path.getsize);final_file=convert_audio(source,audio_format,audio_quality,temp_dir) if mode=="audio" else convert_video(source,video_format,temp_dir);ext=os.path.splitext(final_file)[1];filename=pretty_name(title,ext);media_type=mimetypes.guess_type(final_file)[0] or "application/octet-stream"
        background_tasks.add_task(shutil.rmtree,temp_dir,ignore_errors=True);return FileResponse(final_file,media_type=media_type,filename=filename,background=background_tasks)
    except Exception as exc:shutil.rmtree(temp_dir,ignore_errors=True);return JSONResponse(status_code=500,content={"error":friendly_error(exc)})
