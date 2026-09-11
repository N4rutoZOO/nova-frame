from fastapi import FastAPI, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from urllib.parse import urlparse
import glob
import ipaddress
import mimetypes
import os
import shutil
import socket
import subprocess
import sys
import tempfile

import yt_dlp

app = FastAPI(title="panda.download.com")

COOKIE_FILE = os.getenv("YTDLP_COOKIES_FILE", "/secrets/youtube-cookies.txt")
USER_AGENT = os.getenv("YTDLP_USER_AGENT", "").strip()

HTML = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#090b09">
<title>panda.download.com</title>
<style>
:root{
  --bg:#070807;--panel:#0d100d;--panel2:#121612;--line:#252a25;
  --text:#f5f7f2;--muted:#778071;--accent:#baff39;--accent2:#dfff94;
  --danger:#ff9090;--radius:18px
}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%;background:var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
body{min-height:100vh;background:
 radial-gradient(circle at 50% -15%,rgba(186,255,57,.15),transparent 34%),
 linear-gradient(180deg,#070807 0%,#090b09 48%,#070807 100%)}
button,input,select{font:inherit}
.wrap{width:min(1040px,calc(100% - 28px));margin:auto}
.top{height:72px;border-bottom:1px solid rgba(255,255,255,.06);display:flex;align-items:center;background:rgba(7,8,7,.84);backdrop-filter:blur(18px);position:sticky;top:0;z-index:20}
.nav{display:flex;align-items:center;justify-content:space-between}
.brand{display:flex;align-items:center;gap:11px;font-weight:950;letter-spacing:-1px;font-size:18px}
.mark{width:40px;height:40px;border-radius:12px;background:var(--accent);color:#050605;display:grid;place-items:center;font-weight:1000;box-shadow:0 0 30px rgba(186,255,57,.18)}
.online{display:flex;align-items:center;gap:7px;color:#879080;font-size:11px;font-weight:750}
.online:before{content:"";width:7px;height:7px;border-radius:50%;background:var(--accent);box-shadow:0 0 12px var(--accent)}
.hero{text-align:center;padding:64px 0 30px}
.hero h1{font-size:clamp(42px,7.3vw,76px);line-height:.94;letter-spacing:-5px;margin:0 0 16px}
.hero h1 span{color:var(--accent)}
.hero p{max-width:650px;margin:auto;color:var(--muted);line-height:1.65;font-size:15px}
.card{border:1px solid var(--line);border-radius:26px;background:rgba(13,16,13,.96);overflow:hidden;box-shadow:0 40px 120px rgba(0,0,0,.45)}
.chrome{height:46px;border-bottom:1px solid rgba(255,255,255,.05);display:flex;align-items:center;gap:7px;padding:0 18px;background:#0a0c0a}
.chrome i{width:8px;height:8px;border-radius:50%;background:#343934}
.content{padding:28px}
.label{display:block;margin-bottom:10px;color:#7c8577;font-size:10px;font-weight:900;letter-spacing:.13em}
.search{display:grid;grid-template-columns:1fr 142px;gap:10px}
.input{height:60px;border:1px solid #2b312a;border-radius:16px;background:#101310;display:flex;align-items:center;padding:0 17px;transition:.18s}
.input:focus-within{border-color:rgba(186,255,57,.72);box-shadow:0 0 0 4px rgba(186,255,57,.06)}
.input input{width:100%;border:0;outline:0;background:transparent;color:#fff;font-size:15px}
.input input::placeholder{color:#4e554b}
.primary,.download{border:0;border-radius:16px;background:var(--accent);color:#050605;font-weight:950;cursor:pointer;transition:.18s}
.primary:hover,.download:hover{transform:translateY(-1px);box-shadow:0 14px 34px rgba(186,255,57,.15)}
.primary:disabled,.download:disabled{opacity:.42;cursor:not-allowed;transform:none;box-shadow:none}
.status,.error,.notice{display:none;margin-top:14px;font-size:13px}
.status{color:#8f9888;align-items:center;gap:9px}
.spinner{width:14px;height:14px;border:2px solid #313731;border-top-color:var(--accent);border-radius:50%;animation:spin .75s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.error{padding:13px 14px;border:1px solid rgba(255,120,120,.2);background:rgba(255,100,100,.05);border-radius:13px;color:var(--danger);line-height:1.45}
.notice{padding:13px 14px;border:1px solid rgba(186,255,57,.16);background:rgba(186,255,57,.04);border-radius:13px;color:#b7c699;line-height:1.45}
.result{display:none;margin-top:22px;animation:fade .22s ease}
@keyframes fade{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}
.media{display:grid;grid-template-columns:300px 1fr;gap:20px;border:1px solid #242924;border-radius:19px;background:#0a0c0a;padding:15px}
.thumb{aspect-ratio:16/9;border-radius:14px;overflow:hidden;background:#171a17;position:relative}
.thumb img{width:100%;height:100%;display:block;object-fit:cover}
.duration{position:absolute;right:8px;bottom:8px;background:rgba(0,0,0,.82);padding:5px 7px;border-radius:7px;font-size:11px;font-weight:800}
.meta{display:flex;flex-direction:column;justify-content:center;min-width:0}
.title{font-size:20px;font-weight:900;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.sub{margin-top:7px;color:var(--muted);font-size:13px}
.platform{width:max-content;margin-top:10px;padding:6px 9px;border-radius:999px;border:1px solid rgba(186,255,57,.18);color:var(--accent);background:rgba(186,255,57,.04);font-size:10px;font-weight:900;letter-spacing:.08em}
.section-title{margin:20px 0 10px;font-size:10px;font-weight:900;letter-spacing:.13em;color:#70796b}
.mode-switch{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.mode-btn{height:50px;border-radius:14px;border:1px solid #282e28;background:#101310;color:#8b9485;font-weight:850;cursor:pointer;transition:.16s}
.mode-btn.active{border-color:rgba(186,255,57,.55);background:rgba(186,255,57,.08);color:var(--accent)}
.options{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}
.option{border:1px solid #272d27;background:#101310;border-radius:14px;padding:12px 13px}
.option span{display:block;color:#697266;font-size:9px;font-weight:900;letter-spacing:.09em;margin-bottom:7px}
.option select{width:100%;border:0;outline:0;background:transparent;color:#fff;font-size:13px;font-weight:800}
.option select option{background:#101310}
.option strong{display:block;color:var(--accent);font-size:13px}
.download{width:100%;height:60px;margin-top:14px;font-size:15px}
.footer{height:70px}
@media(max-width:720px){
  .hero{padding-top:42px}.hero h1{letter-spacing:-3px}.content{padding:18px}
  .search{grid-template-columns:1fr}.primary{height:54px}.media{grid-template-columns:1fr}
}
@media(max-width:480px){.wrap{width:min(100% - 18px,1040px)}.options{grid-template-columns:1fr}.online{display:none}}
</style>
</head>
<body>
<header class="top"><div class="wrap nav"><div class="brand"><div class="mark">P</div>panda.download.com</div><div class="online">ONLINE</div></div></header>
<main class="wrap">
  <section class="hero">
    <h1>VIDEO & AUDIO.<br><span>SIMPLIFIÉ.</span></h1>
    <p>Colle une URL, analyse les formats disponibles, choisis la qualité puis télécharge.</p>
  </section>

  <section class="card">
    <div class="chrome"><i></i><i></i><i></i></div>
    <div class="content">
      <label class="label">LIEN DU MÉDIA</label>
      <div class="search">
        <div class="input"><input id="url" autocomplete="off" placeholder="https://www.youtube.com/watch?v=..."></div>
        <button class="primary" id="analyse">ANALYSER</button>
      </div>

      <div class="status" id="status"><div class="spinner"></div><span>Analyse en cours…</span></div>
      <div class="error" id="error"></div>
      <div class="notice" id="notice"></div>

      <div class="result" id="result">
        <div class="media">
          <div class="thumb"><img id="thumb" alt=""><div class="duration" id="duration"></div></div>
          <div class="meta">
            <div class="title" id="title"></div>
            <div class="sub" id="uploader"></div>
            <div class="platform" id="platform"></div>
          </div>
        </div>

        <div class="section-title">TYPE DE TÉLÉCHARGEMENT</div>
        <div class="mode-switch">
          <button type="button" class="mode-btn active" id="videoMode">VIDÉO</button>
          <button type="button" class="mode-btn" id="audioMode">AUDIO · BEST</button>
        </div>

        <div class="options">
          <div class="option">
            <span>QUALITÉ VIDÉO</span>
            <select id="quality"><option value="best">MEILLEURE DISPONIBLE</option></select>
          </div>
          <div class="option">
            <span>QUALITÉ AUDIO</span>
            <strong>MEILLEURE DISPONIBLE</strong>
          </div>
        </div>

        <form method="POST" action="/download" onsubmit="syncDownload()">
          <input type="hidden" name="url" id="durl">
          <input type="hidden" name="mode" id="dmode" value="video">
          <input type="hidden" name="quality" id="dquality" value="best">
          <button class="download" id="download">↓ TÉLÉCHARGER LA VIDÉO</button>
        </form>
      </div>
    </div>
  </section>
  <div class="footer"></div>
</main>

<script>
const $=s=>document.querySelector(s);
const url=$('#url'),analyse=$('#analyse'),status=$('#status'),error=$('#error'),notice=$('#notice'),
result=$('#result'),quality=$('#quality'),download=$('#download'),videoMode=$('#videoMode'),audioMode=$('#audioMode');
let mode='video', audioOnly=false;

function fmtDuration(s){
  if(!s)return '';
  const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),x=Math.floor(s%60);
  return h?`${h}:${String(m).padStart(2,'0')}:${String(x).padStart(2,'0')}`:`${m}:${String(x).padStart(2,'0')}`;
}
function fillQualities(values){
  quality.innerHTML='<option value="best">MEILLEURE DISPONIBLE</option>';
  [...new Set(values||[])].sort((a,b)=>b-a).forEach(v=>{
    const o=document.createElement('option'); o.value=v;
    o.textContent=v>=2160?`${v}p · 4K`:v>=1440?`${v}p · 2K`:v>=1080?`${v}p · FULL HD`:v>=720?`${v}p · HD`:`${v}p`;
    quality.appendChild(o);
  });
}
function setMode(next){
  if(audioOnly && next==='video') return;
  mode=next;
  videoMode.classList.toggle('active',mode==='video');
  audioMode.classList.toggle('active',mode==='audio');
  videoMode.disabled=audioOnly;
  quality.disabled=mode==='audio';
  download.textContent=mode==='audio'?'↓ TÉLÉCHARGER L’AUDIO':'↓ TÉLÉCHARGER LA VIDÉO';
}
videoMode.addEventListener('click',()=>setMode('video'));
audioMode.addEventListener('click',()=>setMode('audio'));

function syncDownload(){
  $('#durl').value=url.value.trim();
  $('#dmode').value=mode;
  $('#dquality').value=quality.value;
}
async function run(){
  error.style.display=notice.style.display=result.style.display='none';
  const value=url.value.trim();
  if(!value){error.textContent='Colle une URL valide.';error.style.display='block';return}
  status.style.display='flex'; analyse.disabled=true;
  try{
    const form=new FormData(); form.append('url',value);
    const response=await fetch('/info',{method:'POST',body:form});
    const data=await response.json();
    if(!response.ok) throw new Error(data.error||'Analyse impossible.');
    $('#thumb').src=data.thumbnail||'';
    $('#title').textContent=data.title||'Média';
    $('#uploader').textContent=data.uploader||'';
    $('#duration').textContent=fmtDuration(data.duration);
    $('#platform').textContent=(data.platform||'MEDIA').toUpperCase();
    fillQualities(data.qualities);
    audioOnly=!!data.audio_only;
    setMode(audioOnly?'audio':'video');
    download.disabled=!!data.blocked;
    if(data.notice){notice.textContent=data.notice;notice.style.display='block'}
    result.style.display='block';
  }catch(e){
    error.textContent=e.message; error.style.display='block';
  }finally{
    status.style.display='none'; analyse.disabled=false;
  }
}
analyse.addEventListener('click',run);
url.addEventListener('keydown',e=>{if(e.key==='Enter')run()});
</script>
</body>
</html>"""


def cookie_path():
    return COOKIE_FILE if COOKIE_FILE and os.path.isfile(COOKIE_FILE) else None


def base_ydl_options():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 3,
        "fragment_retries": 3,
        "sleep_interval_requests": 1,
    }
    path = cookie_path()
    if path:
        opts["cookiefile"] = path
    if USER_AGENT:
        opts["http_headers"] = {"User-Agent": USER_AGENT}
    return opts


def friendly_error(exc):
    text = str(exc)
    lower = text.lower()
    if "sign in to confirm you’re not a bot" in lower or "sign in to confirm you're not a bot" in lower:
        if cookie_path():
            return (
                "YouTube refuse la session actuelle. Les cookies YouTube configurés sont probablement "
                "expirés ou ne correspondent plus à la session. Remplace le secret youtube-cookies par "
                "un export cookies.txt récent puis redéploie."
            )
        return (
            "YouTube demande une session authentifiée. Le serveur Cloud n’a pas de cookies YouTube. "
            "Configure le secret youtube-cookies puis relance le déploiement."
        )
    if "cookies" in lower and "youtube" in lower:
        return "Authentification YouTube requise. Mets à jour le secret youtube-cookies."
    return text[-1800:]


def validate_public_url(raw_url: str) -> str:
    value = raw_url.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL HTTP/HTTPS invalide.")
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"}:
        raise ValueError("Hôte local refusé.")
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise ValueError("Adresse réseau privée/interne refusée.")
    except socket.gaierror as exc:
        raise ValueError("Nom de domaine introuvable.") from exc
    return value


def detect_platform(raw_url: str) -> str:
    host = (urlparse(raw_url).hostname or "").lower()
    if host.endswith("spotify.com"):
        return "spotify"
    if host.endswith("tidal.com"):
        return "tidal"
    if host.endswith("deezer.com"):
        return "deezer"
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    if host == "youtu.be" or "youtube.com" in host:
        return "youtube"
    if "soundcloud.com" in host:
        return "soundcloud"
    if "vimeo.com" in host:
        return "vimeo"
    if host.endswith("x.com") or "twitter.com" in host:
        return "x"
    if "facebook.com" in host or host.endswith("fb.watch"):
        return "facebook"
    return "generic"


@app.get("/", response_class=HTMLResponse)
async def home():
    return HTML


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "panda-download",
        "youtube_cookies": bool(cookie_path()),
        "deno": shutil.which("deno") is not None,
    }


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


@app.post("/info")
async def media_info(url: str = Form(...)):
    try:
        clean = validate_public_url(url)
        platform = detect_platform(clean)

        if platform == "spotify":
            return {
                "platform": "spotify",
                "title": "Spotify",
                "uploader": "Audio matching via spotDL",
                "thumbnail": "",
                "duration": None,
                "qualities": [],
                "audio_only": True,
                "blocked": False,
                "notice": None,
            }

        if platform in {"tidal", "deezer"}:
            return {
                "platform": platform,
                "title": platform.upper(),
                "uploader": "",
                "thumbnail": "",
                "duration": None,
                "qualities": [],
                "audio_only": True,
                "blocked": True,
                "notice": "Cette source ne peut pas être téléchargée directement par ce service.",
            }

        opts = base_ydl_options()
        opts["skip_download"] = True

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(clean, download=False)

        if not info:
            raise RuntimeError("Aucun média détecté.")

        qualities = sorted(
            {
                int(f["height"])
                for f in info.get("formats", [])
                if f.get("height") and f.get("vcodec") != "none"
            },
            reverse=True,
        )
        has_video = bool(qualities)

        return {
            "platform": platform if platform != "generic" else (info.get("extractor_key") or "media"),
            "title": info.get("title"),
            "uploader": info.get("uploader") or info.get("channel") or info.get("artist"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "qualities": qualities,
            "audio_only": not has_video,
            "blocked": False,
            "notice": None,
        }

    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": friendly_error(exc)})


def media_files(temp_dir: str):
    return [
        p
        for p in glob.glob(os.path.join(temp_dir, "**", "*"), recursive=True)
        if os.path.isfile(p)
        and not p.endswith((".part", ".ytdl", ".temp", ".json", ".spotdl"))
    ]


@app.post("/download")
async def download_media(
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    mode: str = Form("video"),
    quality: str = Form("best"),
):
    temp_dir = tempfile.mkdtemp(prefix="panda_download_")
    try:
        clean = validate_public_url(url)
        platform = detect_platform(clean)

        if platform in {"tidal", "deezer"}:
            raise RuntimeError("Cette source n’est pas disponible pour le téléchargement direct.")

        if platform == "spotify":
            cmd = [
                sys.executable,
                "-m",
                "spotdl",
                "download",
                clean,
                "--format",
                "opus",
                "--bitrate",
                "disable",
                "--threads",
                "1",
                "--output",
                os.path.join(temp_dir, "{artists} - {title}.{output-ext}"),
            ]
            completed = subprocess.run(
                cmd,
                cwd=temp_dir,
                capture_output=True,
                text=True,
                timeout=3300,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or completed.stdout or "spotDL a échoué.")[-1800:])

            files = media_files(temp_dir)
            if not files:
                raise RuntimeError("Aucun fichier audio généré.")

            if len(files) == 1:
                final_file = files[0]
                filename = os.path.basename(final_file)
                media_type = mimetypes.guess_type(final_file)[0] or "application/octet-stream"
            else:
                archive_base = os.path.join(temp_dir, "spotify-download")
                final_file = shutil.make_archive(archive_base, "zip", temp_dir)
                filename = "spotify-download.zip"
                media_type = "application/zip"

        else:
            output = os.path.join(temp_dir, "%(title).180B.%(ext)s")
            opts = base_ydl_options()
            opts["outtmpl"] = output

            if mode == "audio":
                opts["format"] = "bestaudio/best"
                opts["postprocessors"] = [{"key": "FFmpegMetadata", "add_metadata": True}]
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
                opts["merge_output_format"] = "mp4"
                opts["postprocessors"] = [{"key": "FFmpegMetadata", "add_metadata": True}]

            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(clean, download=True)

            files = media_files(temp_dir)
            if not files:
                raise RuntimeError("Aucun fichier final généré.")

            if mode == "video":
                mp4 = [p for p in files if p.lower().endswith(".mp4")]
                if not mp4:
                    raise RuntimeError("Aucun MP4 final généré.")
                final_file = max(mp4, key=os.path.getsize)
            else:
                final_file = max(files, key=os.path.getsize)

            filename = os.path.basename(final_file)
            media_type = mimetypes.guess_type(final_file)[0] or "application/octet-stream"

        background_tasks.add_task(shutil.rmtree, temp_dir, ignore_errors=True)
        return FileResponse(
            path=final_file,
            media_type=media_type,
            filename=filename,
            background=background_tasks,
        )

    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return JSONResponse(status_code=500, content={"error": friendly_error(exc)})
