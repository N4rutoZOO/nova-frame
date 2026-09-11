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
import sys
import tempfile

import yt_dlp

app = FastAPI(title="panda.download.com")

COOKIE_SECRET = os.getenv("YTDLP_COOKIES_FILE", "/secrets/youtube-cookies.txt")
USER_AGENT = os.getenv("YTDLP_USER_AGENT", "").strip()
_COOKIE_TMP = None

HTML = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#202432">
<title>panda.download.com</title>
<style>
:root{
 --page:#202432;--page2:#151821;--shell:#353b49;--shell2:#252b36;
 --panel:#11151d;--panel2:#1a1f29;--line:#454c5b;--text:#d8dce6;
 --muted:#7d8596;--purple:#7956ff;--pink:#dc5fe4;--orange:#ff9a32;
 --blue:#68a4ff;--danger:#ff929d
}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--page2);color:var(--text)}
body{min-height:100vh;padding:30px 14px 55px;background:radial-gradient(circle at 50% -18%,rgba(103,111,145,.30),transparent 43%),linear-gradient(180deg,#313548 0%,#232735 40%,#151821 100%)}
button,input,select{font:inherit}
.page{width:min(1080px,100%);margin:auto}
.topline{display:grid;grid-template-columns:220px 1fr 220px;align-items:center;gap:18px;padding:5px 42px 18px;color:#858c9d;font-size:12px;font-weight:850;letter-spacing:.08em;text-transform:uppercase}
.brand{color:#aeb4c2;white-space:nowrap}.brand b{color:#d1d5de}
.preset{height:28px;border:1px solid #596071;border-radius:999px;display:flex;align-items:center;justify-content:center;gap:12px;background:rgba(19,22,30,.25);color:#a6acb8}
.dotbtn{width:18px;height:18px;border-radius:50%;border:1px solid #596071;display:grid;place-items:center;font-size:9px}
.tools{display:flex;justify-content:flex-end;gap:8px}.tools span{width:26px;height:26px;border-radius:50%;display:grid;place-items:center;color:#7d8495}
.device{position:relative;border-radius:43px;padding:18px;background:linear-gradient(145deg,#4c5262,#363c4b 20%,#282e3a 55%,#20252f);border:1px solid #6d7484;box-shadow:0 30px 74px rgba(0,0,0,.52),inset 0 1px 0 rgba(255,255,255,.18),inset 0 -2px 0 rgba(0,0,0,.42)}
.inner{position:relative;border-radius:31px;padding:18px 42px 22px;background:linear-gradient(180deg,#1d222c,#171b23);border:1px solid #12161d;box-shadow:inset 0 0 0 1px rgba(255,255,255,.035),inset 0 14px 30px rgba(0,0,0,.18)}
.meter{position:absolute;top:290px;width:25px;height:166px;border-radius:14px;background:#11151d;border:1px solid #414857;box-shadow:inset 0 0 9px rgba(0,0,0,.75);z-index:4}
.meter.left{left:10px}.meter.right{right:10px}
.meter:before{content:"";position:absolute;left:10px;top:13px;bottom:13px;width:3px;border-radius:4px;background:linear-gradient(to top,#59677f 0 40%,#c9cfda 41% 44%,#48505f 45%);opacity:.8}
.meter:after{content:attr(data-label);position:absolute;left:50%;bottom:-18px;transform:translateX(-50%);font-size:8px;color:#727a8b;font-weight:800}
.display{height:302px;position:relative;overflow:hidden;border-radius:22px;background:linear-gradient(to right,transparent 0 13%,rgba(119,127,147,.12) 13.1% 13.25%,transparent 13.4% 31%,rgba(119,127,147,.12) 31.1% 31.25%,transparent 31.4% 50%,rgba(119,127,147,.12) 50.1% 50.25%,transparent 50.4% 69%,rgba(119,127,147,.12) 69.1% 69.25%,transparent 69.4% 87%,rgba(119,127,147,.12) 87.1% 87.25%,transparent 87.4%),linear-gradient(180deg,#0c1118,#11151d);border:1px solid #4c5362;box-shadow:inset 0 0 0 2px rgba(0,0,0,.48),inset 0 8px 24px rgba(0,0,0,.38)}
.freqs{position:absolute;top:12px;left:21px;right:21px;display:flex;justify-content:space-between;font-size:9px;color:#5f6778;z-index:5}
.spectrum{position:absolute;inset:46px 0 0;opacity:.35;background:repeating-linear-gradient(90deg,transparent 0 16px,rgba(115,123,143,.26) 17px,transparent 19px);clip-path:polygon(0 85%,4% 66%,8% 75%,12% 53%,16% 69%,20% 45%,25% 78%,30% 56%,35% 68%,40% 37%,45% 73%,50% 50%,55% 61%,60% 29%,65% 72%,70% 44%,75% 66%,80% 25%,85% 71%,90% 41%,95% 59%,100% 35%,100% 100%,0 100%)}
.wave1,.wave2{position:absolute;bottom:-20px;height:205px;border-radius:50% 50% 0 0/75% 75% 0 0;pointer-events:none}
.wave1{left:-15px;width:48%;background:radial-gradient(ellipse at 48% 70%,rgba(183,77,255,.9),rgba(84,57,255,.8) 48%,rgba(84,57,255,.10) 72%,transparent 73%);transform:rotate(-7deg)}
.wave2{left:28%;width:55%;background:radial-gradient(ellipse at 44% 72%,rgba(224,91,220,.88),rgba(255,125,68,.92) 52%,rgba(255,145,45,.10) 74%,transparent 75%);transform:rotate(3deg)}
.display-title{position:absolute;top:63px;left:0;right:0;text-align:center;z-index:4;font-size:23px;font-weight:950;letter-spacing:-1.6px;color:#697181;text-shadow:0 1px 0 #10131a}.display-title b{color:#8a92a2}.display-title i{font-style:normal;color:var(--orange)}
.urlrow{position:absolute;left:26px;right:26px;bottom:22px;z-index:8;display:grid;grid-template-columns:1fr 126px;gap:10px}
.urlbox{height:50px;border-radius:12px;background:rgba(8,11,16,.82);border:1px solid #505767;display:flex;align-items:center;padding:0 14px;box-shadow:inset 0 1px 6px rgba(0,0,0,.6)}
.urlbox input{width:100%;border:0;outline:0;background:transparent;color:#cbd1dc;font-size:13px}.urlbox input::placeholder{color:#596171}
.analyse{border:1px solid #596171;border-radius:12px;color:#c0c6d1;background:linear-gradient(#363c49,#252b36);font-size:11px;font-weight:900;letter-spacing:.05em;cursor:pointer;box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 4px 10px rgba(0,0,0,.24)}
.analyse:hover{filter:brightness(1.12)}.analyse:disabled{opacity:.45;cursor:not-allowed}
.result{position:absolute;inset:34px 20px 78px;z-index:7;display:none;grid-template-columns:210px 1fr;gap:18px;align-items:center;padding:14px;border-radius:16px;background:rgba(8,11,16,.88);border:1px solid #3b4250;backdrop-filter:blur(12px)}
.result.show{display:grid;animation:appear .2s ease}@keyframes appear{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
.thumb{aspect-ratio:16/9;border-radius:11px;overflow:hidden;background:#202530;position:relative;border:1px solid #474e5d}.thumb img{width:100%;height:100%;object-fit:cover;display:block}.duration{position:absolute;right:6px;bottom:6px;padding:4px 6px;background:rgba(0,0,0,.74);border-radius:6px;font-size:9px}
.media-title{font-size:16px;font-weight:900;line-height:1.3;color:#d8dce5}.media-sub{margin-top:7px;color:#7f8797;font-size:11px}.platform{display:inline-block;margin-top:10px;padding:5px 8px;border-radius:7px;background:#232936;border:1px solid #434a5a;color:#929aaa;font-size:9px;font-weight:900;letter-spacing:.08em}
.message{display:none;margin:12px 2px 0;padding:10px 12px;border-radius:10px;font-size:11px;line-height:1.4}.message.status{color:#8992a2}.message.error{color:var(--danger);background:rgba(255,105,120,.07);border:1px solid rgba(255,105,120,.18)}.message.notice{color:#aeb5c1;background:#1b202a;border:1px solid #323946}
.controls{display:grid;grid-template-columns:1.04fr 1.04fr .9fr .9fr;gap:11px;margin-top:18px}
.module{min-height:196px;border-radius:21px;padding:15px 14px 13px;background:linear-gradient(155deg,#303644,#252b37 48%,#1f242e);border:1px solid #404756;box-shadow:inset 0 1px 0 rgba(255,255,255,.07),inset 0 -1px 0 rgba(0,0,0,.45),0 8px 18px rgba(0,0,0,.2)}
.module-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:13px}.module-title{font-size:13px;font-weight:950;color:#c1c6d0}.power{width:22px;height:22px;border-radius:50%;border:1px solid #151922;background:linear-gradient(#303745,#1b202a);display:grid;place-items:center;box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 2px 5px rgba(0,0,0,.4)}.power:after{content:"";width:7px;height:7px;border-radius:50%;background:var(--blue);box-shadow:0 0 9px rgba(104,164,255,.75)}.orange .power:after{background:var(--orange);box-shadow:0 0 9px rgba(255,154,50,.7)}.purple .power:after{background:var(--pink);box-shadow:0 0 9px rgba(220,95,228,.7)}
.knobrow{display:grid;grid-template-columns:92px 1fr;gap:10px;align-items:center}.knob{width:82px;height:82px;border-radius:50%;position:relative;margin:auto;background:radial-gradient(circle at 42% 38%,#3c4352 0 8%,#303644 31%,#222833 66%,#181c25 100%);border:1px solid #505767;box-shadow:inset 0 1px 1px rgba(255,255,255,.1),inset 0 -6px 12px rgba(0,0,0,.35),0 6px 12px rgba(0,0,0,.35)}
.knob:before{content:"";position:absolute;top:15px;left:50%;width:3px;height:22px;border-radius:3px;background:var(--blue);transform:translateX(-50%) rotate(62deg);transform-origin:50% 27px;box-shadow:0 0 7px rgba(104,164,255,.45)}.orange .knob:before{background:var(--orange);box-shadow:0 0 7px rgba(255,154,50,.45)}.purple .knob:before{background:var(--pink);box-shadow:0 0 7px rgba(220,95,228,.5)}
.screen{min-height:78px;border-radius:12px;background:#11151d;border:1px solid #343b49;box-shadow:inset 0 0 10px rgba(0,0,0,.65);padding:9px 10px;display:flex;flex-direction:column;justify-content:center}.screen small{font-size:8px;color:#6c7484;font-weight:900;letter-spacing:.08em;margin-bottom:5px}.screen select{width:100%;border:0;outline:0;background:transparent;color:#7fb0ff;font-size:12px;font-weight:900}.orange .screen select{color:#ffac49}.screen select option{background:#151922;color:#d7dbe5}.screen strong{font-size:12px;color:#bdc3cf}
.modebuttons{display:grid;grid-template-columns:1fr;gap:8px}.modebtn{height:47px;border-radius:13px;border:1px solid #424958;background:linear-gradient(#323846,#252b36);color:#8e96a6;font-size:10px;font-weight:900;cursor:pointer}.modebtn.active{color:#9fc0ff;border-color:#5d7db0;box-shadow:inset 0 0 0 1px rgba(104,164,255,.12),0 0 12px rgba(104,164,255,.08)}
.audio-display{height:92px;border-radius:12px;background:#11151d;border:1px solid #343b49;display:grid;place-items:center;position:relative;overflow:hidden;box-shadow:inset 0 0 10px rgba(0,0,0,.65)}.bars{display:flex;align-items:center;gap:2px;height:55px}.bars i{width:3px;border-radius:3px;background:linear-gradient(#d959e9,#8058ff);box-shadow:0 0 5px rgba(175,78,255,.3)}.bars i:nth-child(1){height:18px}.bars i:nth-child(2){height:30px}.bars i:nth-child(3){height:45px}.bars i:nth-child(4){height:24px}.bars i:nth-child(5){height:52px}.bars i:nth-child(6){height:36px}.bars i:nth-child(7){height:44px}.bars i:nth-child(8){height:22px}.bars i:nth-child(9){height:49px}.bars i:nth-child(10){height:31px}
.download{width:100%;height:48px;margin-top:11px;border:1px solid #5a6170;border-radius:13px;color:#d4d8e1;background:linear-gradient(#373e4c,#252b36);font-size:10px;font-weight:950;letter-spacing:.04em;cursor:pointer;box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 4px 12px rgba(0,0,0,.24)}.download:hover{filter:brightness(1.12)}.download:disabled{opacity:.42;cursor:not-allowed}
.mini{margin-top:12px;display:grid;grid-template-columns:1fr 1fr;gap:7px}.mini span{height:28px;border-radius:9px;border:1px solid #383f4d;background:#202631;color:#7e8696;display:grid;place-items:center;font-size:8px;font-weight:850}
.footer{text-align:center;color:#596171;font-size:9px;letter-spacing:.08em;margin-top:15px}
@media(max-width:840px){.topline{grid-template-columns:1fr;padding:0 14px 14px}.preset,.tools{display:none}.controls{grid-template-columns:1fr 1fr}.inner{padding:15px 34px 20px}.meter{display:none}}
@media(max-width:610px){body{padding:12px 8px 32px}.device{border-radius:28px;padding:10px}.inner{border-radius:22px;padding:10px}.display{height:330px;border-radius:16px}.urlrow{left:12px;right:12px;grid-template-columns:1fr;bottom:12px}.analyse{height:45px}.result{inset:18px 10px 118px;grid-template-columns:1fr}.thumb{display:none}.controls{grid-template-columns:1fr}.module{min-height:auto}.footer{margin-top:10px}}
</style>
</head>
<body>
<div class="page">
  <div class="topline">
    <div class="brand"><b>PANDA</b> DOWNLOAD</div>
    <div class="preset"><span class="dotbtn">↑</span><span>panda.download.com</span><span class="dotbtn">↓</span></div>
    <div class="tools"><span>‹</span><span>›</span><span>⚙</span></div>
  </div>

  <section class="device">
    <div class="meter left" data-label="IN"></div>
    <div class="meter right" data-label="OUT"></div>
    <div class="inner">
      <div class="display">
        <div class="freqs"><span>10</span><span>20</span><span>40</span><span>80</span><span>160</span><span>320</span></div>
        <div class="spectrum"></div><div class="wave1"></div><div class="wave2"></div>
        <div class="display-title"><b>PANDA</b>DOWNLOAD<i>:</i></div>

        <div id="result" class="result">
          <div class="thumb"><img id="thumb" alt=""><div id="duration" class="duration"></div></div>
          <div><div id="title" class="media-title"></div><div id="uploader" class="media-sub"></div><div id="platform" class="platform"></div></div>
        </div>

        <div class="urlrow">
          <div class="urlbox"><input id="url" autocomplete="off" placeholder="Colle une URL vidéo ou audio…"></div>
          <button id="analyse" class="analyse">ANALYSER</button>
        </div>
      </div>

      <div id="status" class="message status">Analyse en cours…</div>
      <div id="error" class="message error"></div>
      <div id="notice" class="message notice"></div>

      <div class="controls">
        <div class="module">
          <div class="module-head"><div class="module-title">MODE</div><div class="power"></div></div>
          <div class="modebuttons">
            <button id="videoMode" class="modebtn active" type="button">VIDÉO</button>
            <button id="audioMode" class="modebtn" type="button">AUDIO ONLY</button>
          </div>
          <div class="mini"><span>MP4</span><span>BEST</span></div>
        </div>

        <div class="module orange">
          <div class="module-head"><div class="module-title">QUALITY</div><div class="power"></div></div>
          <div class="knobrow">
            <div class="knob"></div>
            <div class="screen"><small>RESOLUTION</small><select id="quality"><option value="best">BEST</option></select></div>
          </div>
          <div class="mini"><span>VIDEO</span><span>AUTO</span></div>
        </div>

        <div class="module orange">
          <div class="module-head"><div class="module-title">AUDIO</div><div class="power"></div></div>
          <div class="knobrow">
            <div class="knob"></div>
            <div class="screen"><small>SOURCE</small><strong>BEST AVAILABLE</strong></div>
          </div>
          <div class="mini"><span>META</span><span>FFMPEG</span></div>
        </div>

        <div class="module purple">
          <div class="module-head"><div class="module-title">OUTPUT</div><div class="power"></div></div>
          <div class="audio-display"><div class="bars"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div></div>
          <form method="POST" action="/download" onsubmit="syncDownload()">
            <input type="hidden" name="url" id="durl"><input type="hidden" name="mode" id="dmode" value="video"><input type="hidden" name="quality" id="dquality" value="best">
            <button id="download" class="download">DOWNLOAD</button>
          </form>
        </div>
      </div>
    </div>
  </section>
  <div class="footer">panda.download.com</div>
</div>
<script>
const $=s=>document.querySelector(s);
const url=$('#url'),analyse=$('#analyse'),result=$('#result'),status=$('#status'),error=$('#error'),notice=$('#notice'),quality=$('#quality'),download=$('#download'),videoMode=$('#videoMode'),audioMode=$('#audioMode');
let mode='video',audioOnly=false;
function dur(s){if(!s)return'';const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),x=Math.floor(s%60);return h?`${h}:${String(m).padStart(2,'0')}:${String(x).padStart(2,'0')}`:`${m}:${String(x).padStart(2,'0')}`}
function fillQ(values){quality.innerHTML='<option value="best">BEST</option>';[...new Set(values||[])].sort((a,b)=>b-a).forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v>=2160?`${v}p · 4K`:v>=1440?`${v}p · 2K`:v>=1080?`${v}p · FHD`:v>=720?`${v}p · HD`:`${v}p`;quality.appendChild(o)})}
function setMode(next){if(audioOnly&&next==='video')return;mode=next;videoMode.classList.toggle('active',mode==='video');audioMode.classList.toggle('active',mode==='audio');videoMode.disabled=audioOnly;quality.disabled=mode==='audio';download.textContent=mode==='audio'?'DOWNLOAD AUDIO':'DOWNLOAD VIDEO'}
videoMode.onclick=()=>setMode('video');audioMode.onclick=()=>setMode('audio');
function syncDownload(){$('#durl').value=url.value.trim();$('#dmode').value=mode;$('#dquality').value=quality.value}
async function run(){
 error.style.display=notice.style.display=status.style.display='none';result.classList.remove('show');
 const value=url.value.trim();if(!value){error.textContent='Colle une URL valide.';error.style.display='block';return}
 status.style.display='block';analyse.disabled=true;
 try{
   const f=new FormData();f.append('url',value);const r=await fetch('/info',{method:'POST',body:f});const d=await r.json();if(!r.ok)throw new Error(d.error||'Analyse impossible.');
   $('#thumb').src=d.thumbnail||'';$('#title').textContent=d.title||'Média';$('#uploader').textContent=d.uploader||'';$('#duration').textContent=dur(d.duration);$('#platform').textContent=(d.platform||'MEDIA').toUpperCase();fillQ(d.qualities);
   audioOnly=!!d.audio_only;setMode(audioOnly?'audio':'video');download.disabled=!!d.blocked;if(d.notice){notice.textContent=d.notice;notice.style.display='block'}result.classList.add('show');
 }catch(e){error.textContent=e.message;error.style.display='block'}finally{status.style.display='none';analyse.disabled=false}
}
analyse.onclick=run;url.addEventListener('keydown',e=>{if(e.key==='Enter')run()});
</script>
</body></html>"""

def cookie_path():
    global _COOKIE_TMP
    if _COOKIE_TMP and os.path.isfile(_COOKIE_TMP):
        return _COOKIE_TMP
    if COOKIE_SECRET and os.path.isfile(COOKIE_SECRET):
        target = "/tmp/panda-youtube-cookies.txt"
        try:
            shutil.copyfile(COOKIE_SECRET, target)
            os.chmod(target, 0o600)
            _COOKIE_TMP = target
            return target
        except OSError:
            return None
    return None

def base_ydl_options():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 3,
        "fragment_retries": 3,
        "restrictfilenames": True,
        "trim_file_name": 80,
        "windowsfilenames": True,
    }
    c = cookie_path()
    if c:
        opts["cookiefile"] = c
    if USER_AGENT:
        opts["http_headers"] = {"User-Agent": USER_AGENT}
    return opts

def friendly_error(exc):
    text = str(exc)
    low = text.lower()
    if "[errno 22]" in low or "invalid argument" in low:
        return "Le média a provoqué un nom ou un fichier temporaire invalide. Le moteur a été sécurisé avec des noms internes courts; réessaie après le dernier déploiement."
    if "sign in to confirm" in low and "bot" in low:
        return "YouTube refuse la session actuelle. Mets à jour les cookies YouTube puis redéploie."
    if "cookies" in low and "youtube" in low:
        return "Authentification YouTube requise. Mets à jour le secret youtube-cookies."
    return text[-1800:]

def validate_public_url(raw_url: str) -> str:
    value = raw_url.strip()
    p = urlparse(value)
    if p.scheme not in {"http", "https"} or not p.hostname:
        raise ValueError("URL HTTP/HTTPS invalide.")
    host = p.hostname.lower()
    if host in {"localhost", "localhost.localdomain"}:
        raise ValueError("Hôte local refusé.")
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                raise ValueError("Adresse réseau privée/interne refusée.")
    except socket.gaierror as exc:
        raise ValueError("Nom de domaine introuvable.") from exc
    return value

def detect_platform(raw_url: str) -> str:
    host = (urlparse(raw_url).hostname or "").lower()
    if host.endswith("spotify.com"): return "spotify"
    if host.endswith("tidal.com"): return "tidal"
    if host.endswith("deezer.com"): return "deezer"
    if "instagram.com" in host: return "instagram"
    if "tiktok.com" in host: return "tiktok"
    if host == "youtu.be" or "youtube.com" in host: return "youtube"
    if "soundcloud.com" in host: return "soundcloud"
    if "vimeo.com" in host: return "vimeo"
    if host.endswith("x.com") or "twitter.com" in host: return "x"
    if "facebook.com" in host or host.endswith("fb.watch"): return "facebook"
    return "generic"

def media_files(temp_dir: str):
    return [p for p in glob.glob(os.path.join(temp_dir, "**", "*"), recursive=True)
            if os.path.isfile(p) and not p.endswith((".part", ".ytdl", ".temp", ".json", ".spotdl"))]

def pretty_name(title, ext):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", title or "download").strip(" ._")
    name = name[:110] or "download"
    return name + ext

@app.get("/", response_class=HTMLResponse)
async def home():
    return HTML

@app.get("/health")
async def health():
    return {"status":"ok","service":"panda-download","youtube_cookies":bool(cookie_path()),"deno":shutil.which("deno") is not None,"ui":"plugin-v2"}

@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)

@app.post("/info")
async def media_info(url: str = Form(...)):
    try:
        clean = validate_public_url(url)
        platform = detect_platform(clean)
        if platform == "spotify":
            return {"platform":"spotify","title":"Spotify","uploader":"spotDL","thumbnail":"","duration":None,"qualities":[],"audio_only":True,"blocked":False,"notice":None}
        if platform in {"tidal","deezer"}:
            return {"platform":platform,"title":platform.upper(),"uploader":"","thumbnail":"","duration":None,"qualities":[],"audio_only":True,"blocked":True,"notice":"Cette source n’est pas disponible pour le téléchargement direct."}
        opts = base_ydl_options()
        opts["skip_download"] = True
        opts["outtmpl"] = "%(id)s.%(ext)s"
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(clean, download=False)
        if not info:
            raise RuntimeError("Aucun média détecté.")
        qualities = sorted({int(f["height"]) for f in info.get("formats",[]) if f.get("height") and f.get("vcodec") != "none"}, reverse=True)
        return {
            "platform": platform if platform != "generic" else (info.get("extractor_key") or "media"),
            "title": info.get("title"),"uploader":info.get("uploader") or info.get("channel") or info.get("artist"),
            "thumbnail":info.get("thumbnail"),"duration":info.get("duration"),"qualities":qualities,
            "audio_only":not bool(qualities),"blocked":False,"notice":None
        }
    except Exception as exc:
        return JSONResponse(status_code=400,content={"error":friendly_error(exc)})

@app.post("/download")
async def download_media(background_tasks: BackgroundTasks,url: str = Form(...),mode: str = Form("video"),quality: str = Form("best")):
    temp_dir = tempfile.mkdtemp(prefix="panda_dl_")
    try:
        clean = validate_public_url(url)
        platform = detect_platform(clean)
        if platform in {"tidal","deezer"}:
            raise RuntimeError("Cette source n’est pas disponible pour le téléchargement direct.")

        title = "download"
        if platform == "spotify":
            cmd = [sys.executable,"-m","spotdl","download",clean,"--format","opus","--bitrate","disable","--threads","1",
                   "--output",os.path.join(temp_dir,"{track-id}.{output-ext}")]
            c = cookie_path()
            if c:
                cmd += ["--cookie-file", c]
            completed = subprocess.run(cmd,cwd=temp_dir,capture_output=True,text=True,timeout=3300,check=False)
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or completed.stdout or "spotDL a échoué.")[-1800:])
            files = media_files(temp_dir)
            if not files: raise RuntimeError("Aucun fichier audio généré.")
            if len(files) == 1:
                final_file = files[0]; filename = os.path.basename(final_file); media_type = mimetypes.guess_type(final_file)[0] or "application/octet-stream"
            else:
                final_file = shutil.make_archive(os.path.join(temp_dir,"spotify-download"),"zip",temp_dir)
                filename = "spotify-download.zip"; media_type = "application/zip"
        else:
            output = os.path.join(temp_dir, "%(id)s.%(ext)s")
            opts = base_ydl_options()
            opts.update({"outtmpl":output,"restrictfilenames":True,"trim_file_name":80,"windowsfilenames":True})
            if mode == "audio":
                opts["format"] = "bestaudio/best"
                opts["postprocessors"] = [{"key":"FFmpegMetadata","add_metadata":True}]
            else:
                if quality == "best":
                    selector = "bv*+ba/b"
                else:
                    try: height=max(144,min(int(quality),4320))
                    except ValueError: height=1080
                    selector=f"bv*[height<={height}]+ba/b[height<={height}]"
                opts["format"] = selector
                opts["merge_output_format"] = "mp4"
                opts["postprocessors"] = [{"key":"FFmpegMetadata","add_metadata":True}]
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(clean, download=True)
                title = info.get("title") or info.get("id") or "download"
            files = media_files(temp_dir)
            if not files: raise RuntimeError("Aucun fichier final généré.")
            if mode == "video":
                mp4=[p for p in files if p.lower().endswith(".mp4")]
                if not mp4: raise RuntimeError("Aucun MP4 final généré.")
                final_file=max(mp4,key=os.path.getsize)
            else:
                final_file=max(files,key=os.path.getsize)
            ext=os.path.splitext(final_file)[1]
            filename=pretty_name(title,ext)
            media_type=mimetypes.guess_type(final_file)[0] or "application/octet-stream"

        background_tasks.add_task(shutil.rmtree,temp_dir,ignore_errors=True)
        return FileResponse(final_file,media_type=media_type,filename=filename,background=background_tasks)
    except Exception as exc:
        shutil.rmtree(temp_dir,ignore_errors=True)
        return JSONResponse(status_code=500,content={"error":friendly_error(exc)})
