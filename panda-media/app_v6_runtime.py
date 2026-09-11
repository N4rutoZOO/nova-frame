import mimetypes
import os
import shutil
import subprocess

import app_v6 as core

app = core.app
_original_worker = core.worker

# UI hotfixes without duplicating the full V6 frontend.
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


def spotify_worker(job_id: str, payload: dict):
    workdir = core.tempfile.mkdtemp(prefix=f"panda_spotify_{job_id[:8]}_")
    core.update_job(job_id, workdir=workdir, status="running", stage="downloading", progress=8, message="Analyse Spotify")
    try:
        if payload.get("split_tracks"):
            raise RuntimeError("Le mode chapitres/tracks n’est pas utilisé pour Spotify : les playlists/albums sont déjà séparés en pistes.")

        audio_format = payload.get("audio_format") if payload.get("audio_format") in core.AUDIO_FORMATS else "original"
        audio_quality = payload.get("audio_quality") if payload.get("audio_quality") in core.AUDIO_BITRATES else "best"
        target_format = "opus" if audio_format == "original" else audio_format
        if target_format not in {"mp3", "m4a", "opus", "flac", "wav", "ogg"}:
            target_format = "opus"
        bitrate = "disable" if audio_quality == "best" or target_format in {"flac", "wav"} else f"{audio_quality}k"

        cmd = [
            os.sys.executable, "-m", "spotdl", "download", payload["url"],
            "--format", target_format,
            "--bitrate", bitrate,
            "--threads", "1",
            "--output", os.path.join(workdir, "{list-position} - {artists} - {title}.{output-ext}"),
        ]
        cookies = core.cookie_path()
        if cookies:
            cmd += ["--cookie-file", cookies]

        core.update_job(job_id, stage="downloading", progress=20, message="Recherche des correspondances audio")
        proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=3300, check=False)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "spotDL a échoué")[-1800:])
        if core.is_cancelled(job_id):
            raise InterruptedError("Job annulé")

        files = core.media_files(workdir)
        if not files:
            raise RuntimeError("Aucun fichier Spotify généré.")
        core.update_job(job_id, stage="packaging", progress=90, message="Préparation du téléchargement")

        if len(files) == 1:
            final_file = files[0]
            filename = os.path.basename(final_file)
        else:
            archive_base = os.path.join(workdir, "spotify-download")
            final_file = shutil.make_archive(archive_base, "zip", workdir)
            filename = "spotify-download.zip"

        core.update_job(
            job_id,
            status="ready",
            stage="ready",
            progress=100,
            message="Prêt à télécharger",
            result_path=final_file,
            filename=filename,
            media_type=mimetypes.guess_type(final_file)[0] or "application/octet-stream",
            result_size=os.path.getsize(final_file),
        )
    except InterruptedError:
        shutil.rmtree(workdir, ignore_errors=True)
        core.update_job(job_id, status="cancelled", stage="cancelled", progress=0, message="Job annulé", result_path=None)
    except Exception as exc:
        core.update_job(job_id, status="error", stage="error", progress=0, message=core.friendly_error(exc), error=core.friendly_error(exc))


def worker(job_id: str, payload: dict):
    try:
        platform = core.detect_platform(core.validate_public_url(payload["url"]))
    except Exception:
        return _original_worker(job_id, payload)
    if platform == "spotify":
        return spotify_worker(job_id, payload)
    return _original_worker(job_id, payload)


# create_job resolves the module global named `worker` at request time.
core.worker = worker
