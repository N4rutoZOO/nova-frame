from fastapi import FastAPI, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
import mimetypes
import os
import shutil
import tempfile

import yt_dlp
import app_v4 as v4

app = FastAPI(title="panda.download.com")


@app.get("/", response_class=HTMLResponse)
async def home():
    return v4.HTML


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "panda-download",
        "youtube_cookies": bool(v4.cookie_path()),
        "deno": shutil.which("deno") is not None,
        "ui": "plugin-v4",
        "chapter_split": True,
    }


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


@app.post("/info")
async def media_info(url: str = Form(...)):
    return await v4.media_info(url)


@app.post("/download")
async def download_media(
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    mode: str = Form("video"),
    quality: str = Form("best"),
    audio_quality: str = Form("best"),
    video_format: str = Form("mp4"),
    audio_format: str = Form("original"),
    split_tracks_flag: str = Form("0", alias="split_tracks"),
):
    temp_dir = tempfile.mkdtemp(prefix="panda_v4_")
    try:
        clean = v4.validate_public_url(url)
        platform = v4.detect_platform(clean)
        mode = "audio" if mode == "audio" else "video"
        video_format = video_format if video_format in v4.VIDEO_FORMATS else "mp4"
        audio_format = audio_format if audio_format in v4.AUDIO_FORMATS else "original"
        audio_quality = audio_quality if audio_quality in v4.AUDIO_BITRATES else "best"
        do_split = mode == "audio" and split_tracks_flag == "1"

        if platform in {"tidal", "deezer"}:
            raise RuntimeError("Cette source n’est pas disponible pour le téléchargement direct.")
        if platform == "spotify":
            if do_split:
                raise RuntimeError("Le découpage par chapitres n’est pas disponible pour Spotify.")
            raise RuntimeError("Spotify reste géré par le mode audio standard; utilise une URL vidéo avec chapitres pour le mode tracks.")

        output = os.path.join(temp_dir, "%(id)s.%(ext)s")
        opts = v4.base_ydl_options()
        opts.update({
            "outtmpl": output,
            "restrictfilenames": True,
            "trim_file_name": 80,
            "windowsfilenames": True,
        })

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
        files = v4.media_files(temp_dir)
        if not files:
            raise RuntimeError("Aucun fichier final généré.")
        source = max(files, key=os.path.getsize)

        if mode == "video":
            final_file = v4.convert_video(source, video_format, temp_dir)
            ext = os.path.splitext(final_file)[1]
            filename = v4.pretty_name(title, ext)
            media_type = mimetypes.guess_type(final_file)[0] or "application/octet-stream"
        else:
            audio_file = v4.convert_audio(source, audio_format, audio_quality, temp_dir)
            if do_split:
                chapters = v4.extract_chapters(info)
                if not chapters:
                    raise RuntimeError("Aucun chapitre ou timestamp exploitable n’a été détecté dans cette vidéo.")
                tracks_dir = os.path.join(temp_dir, "tracks")
                v4.split_tracks(audio_file, chapters, tracks_dir)
                final_file = shutil.make_archive(os.path.join(temp_dir, "chapter-tracks"), "zip", tracks_dir)
                filename = v4.pretty_name(f"{title} - tracks", ".zip")
                media_type = "application/zip"
            else:
                final_file = audio_file
                ext = os.path.splitext(final_file)[1]
                filename = v4.pretty_name(title, ext)
                media_type = mimetypes.guess_type(final_file)[0] or "application/octet-stream"

        background_tasks.add_task(shutil.rmtree, temp_dir, ignore_errors=True)
        return FileResponse(final_file, media_type=media_type, filename=filename, background=background_tasks)
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return JSONResponse(status_code=500, content={"error": v4.friendly_error(exc)})
