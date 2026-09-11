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
ENV PANDA_FRAGMENT_CONCURRENCY=6
ENV PANDA_MAX_PENDING_JOBS=6
ENV PANDA_SPOTDL_THREADS=2
ENV PANDA_FFMPEG_PRESET=veryfast

CMD ["sh","-c","exec python -m uvicorn app_v7_runtime:app --host 0.0.0.0 --port ${PORT} --workers 1 --loop uvloop --http httptools --timeout-keep-alive 20 --proxy-headers --forwarded-allow-ips='*'"]
