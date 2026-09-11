import app_v6_worker_runtime as worker

app = worker.app
core = worker.core

# Keep the worker/backend untouched and apply the UI layer last.
worker.VERSION = "6.8-ui"

UI_CSS = r'''
<style id="panda-v68-ui">
:root{
  --panda-accent:#93ff62;
  --panda-accent2:#6ea7ff;
  --panda-card:rgba(25,29,38,.82);
  --panda-border:rgba(255,255,255,.10);
  --panda-shadow:0 24px 70px rgba(0,0,0,.38);
}
html{background:#101218;scroll-behavior:smooth}
body{
  min-height:100dvh;
  background:
    radial-gradient(circle at 12% 0%,rgba(110,167,255,.16),transparent 34%),
    radial-gradient(circle at 88% 8%,rgba(147,255,98,.10),transparent 28%),
    linear-gradient(180deg,#202532 0%,#161922 44%,#0f1117 100%);
}
button,a,input,select{-webkit-tap-highlight-color:transparent}
button,.btn,.modebtn,.presetbtn,.smallbtn{touch-action:manipulation}
button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{
  outline:2px solid var(--panda-accent);
  outline-offset:2px;
}
.page{width:min(1240px,calc(100% - 28px));padding:20px 0 54px}
.top{padding:4px 20px 12px;font-size:10px;letter-spacing:.12em}
.brand{display:flex;align-items:center;gap:2px}
.brand b{color:#fff}
.online{
  padding:7px 10px;border:1px solid rgba(123,219,168,.18);border-radius:999px;
  background:rgba(13,18,17,.36);backdrop-filter:blur(14px);
}
.device{
  padding:12px;border-radius:32px;
  background:linear-gradient(145deg,rgba(78,85,101,.86),rgba(33,38,49,.96) 35%,rgba(24,28,36,.98));
  border:1px solid rgba(255,255,255,.15);box-shadow:var(--panda-shadow),inset 0 1px 0 rgba(255,255,255,.12)
}
.inner{padding:13px;border-radius:24px;background:linear-gradient(180deg,rgba(25,29,38,.98),rgba(15,18,24,.98))}
.display{
  height:318px;border-radius:18px;border-color:rgba(255,255,255,.13);
  background:linear-gradient(180deg,#090c12,#10141c);
}
.grid{opacity:.72}
.hero-title{top:44px;font-size:30px;letter-spacing:-1.8px;color:#697383}
.hero-title b{color:#c3cad5}
.hero-title i{color:var(--panda-accent)}
.urlrow{left:22px;right:22px;bottom:20px;grid-template-columns:minmax(0,1fr) 150px;gap:9px}
.urlbox{
  height:56px;border-radius:14px;background:rgba(5,8,12,.88);border-color:rgba(255,255,255,.15);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.04)
}
.urlbox:focus-within{border-color:rgba(147,255,98,.55);box-shadow:0 0 0 3px rgba(147,255,98,.08)}
.urlbox input{font-size:14px;color:#f1f4f8}
.urlbox input::placeholder{color:#697282}
.btn{border-color:rgba(255,255,255,.14);background:linear-gradient(180deg,#343b49,#242a34);transition:transform .12s ease,filter .12s ease,border-color .12s ease}
.btn:hover{filter:brightness(1.12);border-color:rgba(147,255,98,.28)}
.btn:active,.modebtn:active,.presetbtn:active,.smallbtn:active{transform:scale(.985)}
#analyse,#startJob{background:linear-gradient(135deg,#87f55c,#62c94f);border-color:#9dff78;color:#0b1609;text-shadow:none;box-shadow:0 10px 28px rgba(91,207,75,.17)}
#analyse:disabled,#startJob:disabled{background:linear-gradient(#343a45,#292f39);color:#818a98;border-color:#454c59;box-shadow:none}
.result{inset:17px 17px 86px;grid-template-columns:230px minmax(0,1fr);border-radius:15px;background:rgba(7,10,15,.91);border-color:rgba(255,255,255,.11);box-shadow:0 14px 36px rgba(0,0,0,.28)}
.thumb{border-color:rgba(255,255,255,.12);box-shadow:0 10px 24px rgba(0,0,0,.22)}
.media-title{font-size:18px;color:#f2f4f8}
.platform,.stat{border-color:rgba(255,255,255,.08);background:rgba(255,255,255,.04)}
.message{margin:10px 1px 0;border-radius:12px}
.controls{gap:9px;margin-top:12px}
.module,.track-editor,.playlist-editor,.jobbox{
  border-color:var(--panda-border);background:linear-gradient(155deg,rgba(44,50,63,.88),rgba(25,30,39,.94));
  box-shadow:inset 0 1px 0 rgba(255,255,255,.05),0 9px 22px rgba(0,0,0,.15)
}
.module{min-height:184px;border-radius:17px}
.module-title,.editor-title,.jobstage{letter-spacing:.04em;color:#e9edf4}
.led{border-color:rgba(255,255,255,.06)}
.field{border-color:rgba(255,255,255,.08);background:rgba(7,10,15,.64)}
.modebtn,.presetbtn,.smallbtn{border-color:rgba(255,255,255,.09);background:rgba(16,20,27,.64)}
.modebtn.active{color:#cbe0ff;border-color:rgba(110,167,255,.54);background:linear-gradient(180deg,rgba(81,115,166,.24),rgba(30,42,60,.35))}
.summary{border-color:rgba(255,255,255,.07);background:rgba(8,11,16,.48)}
.playlist-list{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.playlist-item,.playlist-row{border-radius:12px!important}
.track,.playlist-item,.playlist-row{border-color:rgba(255,255,255,.08)!important;background:rgba(10,13,18,.60)!important}
.jobbox{position:relative;overflow:hidden}
.jobbox:before{content:"";position:absolute;inset:0 auto 0 0;width:3px;background:linear-gradient(var(--panda-accent2),var(--panda-accent));opacity:.8}
.progress{height:11px;border-color:rgba(255,255,255,.08)}
.progressbar{background:linear-gradient(90deg,#6ea7ff,#8f67ff,#93ff62)}
.download-ready.show{background:linear-gradient(135deg,#8eff62,#69d951);border-color:#9aff77;color:#0b1609}
.history{padding-bottom:4px}
@media (min-width:901px){
  .controls{grid-template-columns:1fr 1fr 1fr 1.12fr}
  .module{transition:transform .15s ease,border-color .15s ease}
  .module:hover{transform:translateY(-1px);border-color:rgba(255,255,255,.15)}
}
@media (max-width:900px){
  .page{width:min(100% - 18px,780px);padding-top:14px}
  .top{padding:3px 9px 10px}
  .device{border-radius:26px;padding:9px}
  .inner{border-radius:20px;padding:9px}
  .display{height:292px}
  .controls{grid-template-columns:repeat(2,minmax(0,1fr))}
  .playlist-list,.tracklist{grid-template-columns:1fr}
}
@media (max-width:560px){
  body{background:linear-gradient(180deg,#1c202b,#11141b 52%,#0c0e13)}
  .page{width:100%;padding:8px 6px calc(104px + env(safe-area-inset-bottom))}
  .top{padding:3px 7px 8px;font-size:8px}
  .online{padding:6px 8px;letter-spacing:.08em}
  .online:before{width:6px;height:6px}
  .device{padding:6px;border-radius:20px;box-shadow:0 18px 46px rgba(0,0,0,.38)}
  .inner{padding:6px;border-radius:16px}
  .display{height:268px;border-radius:14px}
  .hero-title{top:25px;font-size:21px;letter-spacing:-1px}
  .urlrow{left:8px;right:8px;bottom:8px;grid-template-columns:1fr;gap:7px}
  .urlbox{height:50px;border-radius:12px;padding:0 12px}
  .urlbox input{font-size:16px}
  #analyse{height:47px;border-radius:12px}
  .result{inset:8px 7px 109px;grid-template-columns:96px minmax(0,1fr);gap:9px;padding:8px;border-radius:12px}
  .media-title{font-size:12px;line-height:1.25;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
  .media-sub{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .stats{gap:4px}.stat{padding:4px 5px}
  .controls{grid-template-columns:1fr;gap:7px;margin-top:8px}
  .module{min-height:0;border-radius:14px;padding:11px}
  .module-head{margin-bottom:9px}
  .modebuttons{grid-template-columns:1fr 1fr;gap:7px}
  .modebtn{height:46px}
  .field select{font-size:12px;min-height:24px}
  .preset-row{grid-template-columns:repeat(2,minmax(0,1fr))}
  .presetbtn{height:42px}
  .bottom-download{height:50px}
  .track-editor,.playlist-editor,.jobbox{border-radius:14px;padding:11px}
  .editor-head{gap:9px}
  .editor-tools{width:100%}
  .smallbtn{min-height:38px;flex:1}
  .split-toggle{min-height:38px}
  .track{grid-template-columns:22px 30px minmax(0,1fr) auto;padding:10px 8px}
  .playlist-list{grid-template-columns:1fr!important}
  .jobactions{
    left:6px;right:6px;bottom:calc(6px + env(safe-area-inset-bottom));padding:7px;
    border-radius:14px;background:rgba(13,16,22,.92);border-color:rgba(255,255,255,.12)
  }
  .jobactions .btn,.jobactions a{min-height:46px}
}
@media (max-width:370px){
  .top{font-size:7px}
  .display{height:260px}
  .result{grid-template-columns:82px minmax(0,1fr)}
  .platform{display:none}
  .preset-row{grid-template-columns:1fr 1fr}
}
@media (prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
</style>
'''

UI_JS = r'''
<script id="panda-v68-mobile">
(() => {
  const input = document.getElementById('url');
  if (input) {
    input.setAttribute('inputmode','url');
    input.setAttribute('enterkeyhint','go');
    input.setAttribute('autocapitalize','off');
    input.setAttribute('autocorrect','off');
    input.setAttribute('spellcheck','false');
    input.setAttribute('aria-label','URL vidéo ou audio');
  }
  const analyse = document.getElementById('analyse');
  if (analyse) analyse.setAttribute('aria-label','Analyser le lien');
  const start = document.getElementById('startJob');
  if (start) start.textContent = 'TÉLÉCHARGER';
  document.documentElement.classList.toggle('touch-ui', matchMedia('(pointer:coarse)').matches);
})();
</script>
'''

core.HTML = core.HTML.replace("V6.7 · WORKER AUTH", "V6.8 · WEB / MOBILE")
core.HTML = core.HTML.replace("V6 JOB ENGINE", "WORKER AUTH · ONLINE")
core.HTML = core.HTML.replace("</head>", UI_CSS + "</head>")
core.HTML = core.HTML.replace("</body>", UI_JS + "</body>")
