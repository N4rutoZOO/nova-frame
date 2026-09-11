FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

ENV DENO_INSTALL=/usr/local/deno
RUN curl -fsSL https://deno.land/install.sh | sh
ENV PATH="/usr/local/deno/bin:${PATH}"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV YTDLP_COOKIES_FILE=/secrets/youtube-cookies.txt
ENV PANDA_YTDLP_FRAGMENTS=4
ENV PANDA_YOUTUBE_FRAGMENTS=2
ENV PANDA_INFO_CACHE_TTL=180
ENV PANDA_INFO_CACHE_MAX=128
ENV PANDA_JOB_TTL=3600
ENV PANDA_JOB_WORKERS=1
ENV PANDA_MAX_WORKDIR_BYTES=6442450944

CMD ["sh","-c","python -m uvicorn app_v6_optimized_runtime:app --host 0.0.0.0 --port ${PORT}"]
