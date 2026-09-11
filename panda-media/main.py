from fastapi import FastAPI, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from urllib.parse import urlparse
import glob, ipaddress, mimetypes, os, shutil, socket, subprocess, sys, tempfile
import yt_dlp

app = FastAPI(title='PANDA MEDIA')

HTML = r'''<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PANDA MEDIA</title>
<style>
:root{--bg:#070807;--card:#0f110f;--card2:#141714;--line:#252a25;--text:#f3f6ef;--muted:#858d80;--accent:#baff39;--accent2:#dcff8a;--danger:#ff7c7c}*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}body{background:radial-gradient(circle at 50% -20%,rgba(186,255,57,.16),transparent 34%),linear-gradient(#070807,#090a09 45%,#070807);min-height:100vh}.shell{width:min(1120px,calc(100% - 28px));margin:auto}.topbar{height:72px;border-bottom:1px solid rgba(255,255,255,.06);display:flex;align-items:center;background:rgba(7,8,7,.82);backdrop-filter:blur(18px);position:sticky;top:0;z-index:20}.nav{display:flex;align-items:center;justify-content:space-between}.brand{display:flex;align-items:center;gap:11px;font-weight:950;letter-spacing:-1px}.mark{width:40px;height:40px;border-radius:12px;background:var(--accent);color:#050605;display:grid;place-items:center;font-weight:1000;box-shadow:0 0 28px rgba(186,255,57,.18)}.brand em{font-style:normal;color:var(--accent)}.pill{font-size:11px;padding:8px 11px;border:1px solid var(--line);border-radius:999px;color:#9ca496;background:#101210}.hero{padding:64px 0 32px;text-align:center}.eyebrow{display:inline-flex;align-items:center;gap:8px;color:var(--accent);font-size:11px;font-weight:850;border:1px solid rgba(186,255,57,.22);background:rgba(186,255,57,.05);border-radius:999px;padding:8px 11px}.eyebrow:before{content:"";width:7px;height:7px;background:var(--accent);border-radius:50%;box-shadow:0 0 12px var(--accent)}h1{font-size:clamp(46px,7.5vw,78px);line-height:.92;letter-spacing:-5px;margin:22px 0 14px}.hero p{margin:0 auto;max-width:760px;color:var(--muted);font-size:16px;line-height:1.65}.app{margin-top:22px;border:1px solid var(--line);border-radius:26px;background:rgba(15,17,15,.94);overflow:hidden;box-shadow:0 40px 120px rgba(0,0,0,.45)}.app-head{height:48px;border-bottom:1px solid rgba(255,255,255,.05);display:flex;align-items:center;gap:7px;padding:0 18px;background:#0b0c0b}.dot{width:8px;height:8px;border-radius:50%;background:#333833}.app-head small{margin-left:8px;color:#596057;font-family:ui-monospace,Menlo,monospace}.content{padding:26px}.label{font-size:11px;letter-spacing:.12em;color:#7f877a;font-weight:850;margin-bottom:10px}.search{display:grid;grid-template-columns:1fr 140px;gap:10px}.input-wrap{height:58px;border:1px solid #2a2f29;border-radius:16px;background:#101210;display:flex;align-items:center;padding:0 16px;transition:.18s}.input-wrap:focus-within{border-color:rgba(186,255,57,.75);box-shadow:0 0 0 4px rgba(186,255,57,.06)}input,select{width:100%;border:0;outline:0;background:transparent;color:#fff;font:inherit}input::placeholder{color:#50564d}.primary{border:0;border-radius:16px;background:var(--accent);color:#050605;font-weight:950;cursor:pointer;transition:.18s}.primary:hover{transform:translateY(-1px);box-shadow:0 13px 34px rgba(186,255,57,.15)}.primary:disabled{opacity:.45;cursor:default;transform:none}.status,.error,.notice{display:none;margin-top:14px;font-size:13px}.status{color:#8d9588}.error{padding:12px 14px;border:1px solid rgba(255,100,100,.2);background:rgba(255,100,100,.05);border-radius:12px;color:#ff9d9d}.notice{padding:12px 14px;border:1px solid rgba(186,255,57,.15);background:rgba(186,255,57,.04);border-radius:12px;color:#b2c293}.result{display:none;margin-top:22px}.media{display:grid;grid-template-columns:290px 1fr;gap:20px;border:1px solid #242824;background:#0b0d0b;border-radius:18px;padding:15px}.thumb{aspect-ratio:16/9;border-radius:13px;overflow:hidden;background:#171a17;position:relative}.thumb img{width:100%;height:100%;object-fit:cover;display:block}.duration{position:absolute;right:7px;bottom:7px;padding:5px 7px;background:rgba(0,0,0,.78);border-radius:7px;font-size:11px}.meta{display:flex;flex-direction:column;justify-content:center}.title{font-size:20px;font-weight:900;line-height:1.3}.sub{color:var(--muted);font-size:13px;margin-top:7px}.platform{margin-top:10px;width:max-content;font-size:10px;font-weight:900;letter-spacing:.08em;border-radius:999px;padding:7px 9px;border:1px solid rgba(186,255,57,.18);color:var(--accent);background:rgba(186,255,57,.04)}.controls{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:18px}.control{border:1px solid #262b26;background:#111311;border-radius:14px;padding:13px}.control span{display:block;color:#697067;font-size:9px;font-weight:900;letter-spacing:.08em;margin-bottom:6px}.control select{font-size:13px;font-weight:800}.control strong{font-size:13px;color:var(--accent)}.download{width:100%;height:58px;margin-top:14px;border:0;border-radius:16px;background:var(--accent);color:#050605;font-weight:950;font-size:15px;cursor:pointer}.download:disabled{opacity:.4;cursor:not-allowed}.chips{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin-top:18px}.chip{border:1px solid #202420;border-radius:12px;background:#0b0d0b;color:#6c7469;text-align:center;padding:10px;font-size:10px;font-weight:800}.chip.ok{color:#a8c56a;border-color:rgba(186,255,57,.13)}.legal{margin:18px 0 50px;text-align:center;color:#4f554c;font-size:11px;line-height:1.6}
@media(max-width:720px){.pill{display:none}.hero{padding-top:42px}h1{letter-spacing:-3px}.content{padding:18px}.search{grid-template-columns:1fr}.primary{height:54px}.media{grid-template-columns:1fr}.controls,.chips{grid-template-columns:1fr 1fr}}
@media(max-width:440px){.controls,.chips{grid-template-columns:1fr}.shell{width:min(100% - 18px,1120px)}}
</style></head><body>
<div class="topbar"><div class="shell nav"><div class="brand"><div class="mark">P</div>PANDA <em>MEDIA</em></div><div class="pill">UNIVERSAL MEDIA TOOL</div></div></div>
<main class="shell"><section class="hero"><div class="eyebrow">ENGINE ONLINE</div><h1>PASTE. DETECT.<br><span style="color:var(--accent)">DOWNLOAD.</span></h1><p>Une seule URL. PANDA MEDIA détecte la plateforme, affiche les qualités réellement disponibles et adapte automatiquement les options vidéo ou audio.</p></section>
<section class="app"><div class="app-head"><div class="dot"></div><div class="dot"></div><div class="dot"></div><small>panda-media / auto-detect</small></div><div class="content">
<div class="label">URL DU MÉDIA</div><div class="search"><div class="input-wrap"><input id="url" placeholder="https://youtube.com/...  ·  Instagram  ·  TikTok  ·  Spotify ..." autocomplete="off"></div><button class="primary" id="analyse">ANALYSER</button></div>
<div class="status" id="status">Analyse de la source…</div><div class="error" id="error"></div><div class="notice" id="notice"></div>
<div class="result" id="result"><div class="media"><div class="thumb"><img id="thumb" alt=""><div class="duration" id="duration"></div></div><div class="meta"><div class="title" id="title"></div><div class="sub" id="uploader"></div><div class="platform" id="platform"></div>
<div class="controls"><div class="control"><span>MODE</span><select id="mode"><option value="video">VIDÉO</option><option value="audio">AUDIO ONLY · BEST</option></select></div><div class="control"><span>QUALITÉ VIDÉO</span><select id="quality"><option value="best">BEST AVAILABLE</option></select></div><div class="control"><span>AUDIO</span><strong>BEST AVAILABLE</strong></div></div></div></div>
<form method="POST" action="/download" onsubmit="syncDownload()"><input type="hidden" name="url" id="durl"><input type="hidden" name="mode" id="dmode"><input type="hidden" name="quality" id="dquality"><button class="download" id="download">↓ TÉLÉCHARGER</button></form></div>
<div class="chips"><div class="chip ok">YOUTUBE</div><div class="chip ok">INSTAGRAM</div><div class="chip ok">TIKTOK</div><div class="chip ok">SPOTIFY*</div><div class="chip ok">SOUNDCLOUD</div><div class="chip ok">VIMEO</div><div class="chip ok">X / TWITTER</div><div class="chip ok">FACEBOOK</div></div>
</div></section><div class="legal">* Spotify utilise les métadonnées Spotify et une correspondance audio via spotDL/YouTube. Les flux protégés de services comme TIDAL ou Deezer ne sont pas contournés. Utilise le service uniquement pour les médias que tu es autorisé à télécharger.</div></main>
<script>
const $=s=>document.querySelector(s),url=$('#url'),analyse=$('#analyse'),status=$('#status'),error=$('#error'),notice=$('#notice'),result=$('#result'),mode=$('#mode'),quality=$('#quality'),download=$('#download');
function dur(s){if(!s)return'';const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),x=Math.floor(s%60);return h?`${h}:${String(m).padStart(2,'0')}:${String(x).padStart(2,'0')}`:`${m}:${String(x).padStart(2,'0')}`}
function qualities(a){quality.innerHTML='<option value="best">BEST AVAILABLE</option>';[...new Set(a||[])].sort((a,b)=>b-a).forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v>=2160?`${v}p · 4K`:v>=1440?`${v}p · 2K`:v>=1080?`${v}p · FULL HD`:v>=720?`${v}p · HD`:`${v}p`;quality.appendChild(o)})}
function syncMode(){const a=mode.value==='audio';quality.disabled=a;download.textContent=a?'↓ TÉLÉCHARGER L’AUDIO':'↓ TÉLÉCHARGER LA VIDÉO'} mode.addEventListener('change',syncMode);
function syncDownload(){$('#durl').value=url.value.trim();$('#dmode').value=mode.value;$('#dquality').value=quality.value}
async function run(){error.style.display=notice.style.display=result.style.display='none';const v=url.value.trim();if(!v){error.textContent='Colle une URL.';error.style.display='block';return}status.style.display='block';analyse.disabled=true;try{const f=new FormData();f.append('url',v);const r=await fetch('/info',{method:'POST',body:f});const d=await r.json();if(!r.ok)throw new Error(d.error||'Analyse impossible.');$('#thumb').src=d.thumbnail||'';$('#title').textContent=d.title||'Média';$('#uploader').textContent=d.uploader||'';$('#duration').textContent=dur(d.duration);$('#platform').textContent=(d.platform||'GENERIC').toUpperCase();qualities(d.qualities);mode.value=d.audio_only?'audio':'video';mode.disabled=!!d.audio_only;syncMode();if(d.notice){notice.textContent=d.notice;notice.style.display='block'}download.disabled=!!d.blocked;result.style.display='block';}catch(e){error.textContent=e.message;error.style.display='block'}finally{status.style.display='none';analyse.disabled=false}}
analyse.addEventListener('click',run);url.addEventListener('keydown',e=>{if(e.key==='Enter')run()});syncMode();
</script></body></html>'''

def validate_public_url(raw_url:str)->str:
    value=raw_url.strip(); p=urlparse(value)
    if p.scheme not in {'http','https'} or not p.hostname: raise ValueError('URL HTTP/HTTPS invalide.')
    host=p.hostname.lower()
    if host in {'localhost','localhost.localdomain'}: raise ValueError('Hôte local refusé.')
    try:
        for info in socket.getaddrinfo(host,None):
            ip=ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified: raise ValueError('Adresse réseau privée/interne refusée.')
    except socket.gaierror as exc: raise ValueError('Nom de domaine introuvable.') from exc
    return value

def detect_platform(raw_url:str)->str:
    host=(urlparse(raw_url).hostname or '').lower()
    if host.endswith('spotify.com'): return 'spotify'
    if host.endswith('tidal.com'): return 'tidal'
    if host.endswith('deezer.com'): return 'deezer'
    if 'instagram.com' in host: return 'instagram'
    if 'tiktok.com' in host: return 'tiktok'
    if host=='youtu.be' or 'youtube.com' in host: return 'youtube'
    if 'soundcloud.com' in host: return 'soundcloud'
    if 'vimeo.com' in host: return 'vimeo'
    if host.endswith('x.com') or 'twitter.com' in host: return 'x'
    if 'facebook.com' in host or host.endswith('fb.watch'): return 'facebook'
    return 'generic'

@app.get('/',response_class=HTMLResponse)
async def home(): return HTML
@app.get('/health')
async def health(): return {'status':'ok','service':'panda-media'}
@app.get('/favicon.ico')
async def favicon(): return Response(status_code=204)

@app.post('/info')
async def media_info(url:str=Form(...)):
    try:
        clean=validate_public_url(url); platform=detect_platform(clean)
        if platform=='spotify':
            return {'platform':'spotify','title':'Spotify','uploader':'Audio matching via spotDL / YouTube','thumbnail':'','duration':None,'qualities':[],'audio_only':True,'blocked':False,'notice':'Spotify : métadonnées Spotify + correspondance audio via YouTube. Le flux Spotify n’est pas extrait directement.'}
        if platform in {'tidal','deezer'}:
            return {'platform':platform,'title':platform.upper(),'uploader':'Streaming protégé','thumbnail':'','duration':None,'qualities':[],'audio_only':True,'blocked':True,'notice':'Téléchargement direct des morceaux complets protégés non activé.'}
        opts={'quiet':True,'no_warnings':True,'skip_download':True,'noplaylist':True}
        with yt_dlp.YoutubeDL(opts) as ydl: info=ydl.extract_info(clean,download=False)
        qualities=sorted({int(f['height']) for f in info.get('formats',[]) if f.get('height') and f.get('vcodec')!='none'},reverse=True)
        has_video=bool(qualities)
        return {'platform':platform if platform!='generic' else (info.get('extractor_key') or 'generic'),'title':info.get('title'),'uploader':info.get('uploader') or info.get('channel') or info.get('artist'),'thumbnail':info.get('thumbnail'),'duration':info.get('duration'),'qualities':qualities,'audio_only':not has_video,'blocked':False,'notice':None}
    except Exception as exc: return JSONResponse(status_code=400,content={'error':str(exc)})

def pick_file(temp_dir):
    files=[p for p in glob.glob(os.path.join(temp_dir,'**','*'),recursive=True) if os.path.isfile(p) and not p.endswith(('.part','.ytdl','.temp','.json','.spotdl'))]
    if not files: raise RuntimeError('Aucun fichier final généré.')
    return max(files,key=os.path.getsize)

@app.post('/download')
async def download_media(background_tasks:BackgroundTasks,url:str=Form(...),mode:str=Form('video'),quality:str=Form('best')):
    temp_dir=tempfile.mkdtemp(prefix='pandamedia_')
    try:
        clean=validate_public_url(url); platform=detect_platform(clean)
        if platform in {'tidal','deezer'}: raise RuntimeError('Téléchargement direct TIDAL/Deezer non activé pour les flux protégés.')
        if platform=='spotify':
            cmd=[sys.executable,'-m','spotdl','download',clean,'--format','opus','--bitrate','disable','--threads','1','--output',os.path.join(temp_dir,'{artists} - {title}.{output-ext}')]
            done=subprocess.run(cmd,cwd=temp_dir,capture_output=True,text=True,timeout=3300,check=False)
            if done.returncode!=0: raise RuntimeError((done.stderr or done.stdout or 'spotDL a échoué.')[-1800:])
            files=[p for p in glob.glob(os.path.join(temp_dir,'**','*'),recursive=True) if os.path.isfile(p) and not p.endswith(('.spotdl','.json','.part','.temp'))]
            if not files: raise RuntimeError('Aucun fichier Spotify/spotDL généré.')
            if len(files)==1: final_file=files[0]; filename=os.path.basename(final_file); media_type=mimetypes.guess_type(final_file)[0] or 'application/octet-stream'
            else: final_file=shutil.make_archive(os.path.join(temp_dir,'spotify-download'),'zip',temp_dir); filename='spotify-download.zip'; media_type='application/zip'
        else:
            output=os.path.join(temp_dir,'%(title).180B.%(ext)s')
            if mode=='audio': opts={'format':'bestaudio/best','outtmpl':output,'noplaylist':True,'postprocessors':[{'key':'FFmpegMetadata','add_metadata':True}]}
            else:
                if quality=='best': selector='bv*+ba/b'
                else:
                    try: height=max(144,min(int(quality),4320))
                    except ValueError: height=1080
                    selector=f'bv*[height<={height}]+ba/b[height<={height}]'
                opts={'format':selector,'merge_output_format':'mp4','outtmpl':output,'noplaylist':True,'postprocessors':[{'key':'FFmpegMetadata','add_metadata':True}]}
            with yt_dlp.YoutubeDL(opts) as ydl: ydl.extract_info(clean,download=True)
            final_file=pick_file(temp_dir); filename=os.path.basename(final_file); media_type=mimetypes.guess_type(final_file)[0] or 'application/octet-stream'
        background_tasks.add_task(shutil.rmtree,temp_dir,ignore_errors=True)
        return FileResponse(final_file,media_type=media_type,filename=filename,background=background_tasks)
    except Exception as exc:
        shutil.rmtree(temp_dir,ignore_errors=True)
        return JSONResponse(status_code=500,content={'error':str(exc)})
