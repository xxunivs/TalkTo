FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ffmpeg: 올린 영상에서 소리만 뽑고, 목소리 미리듣기 클립을 자른다
RUN apt-get update     && apt-get install -y --no-install-recommends ffmpeg     && rm -rf /var/lib/apt/lists/*

RUN groupadd --system talkto \
    && useradd --system --gid talkto --create-home talkto

# 의존성을 먼저 깐다(운영에서 쓰던 버전 그대로, requirements.lock). 코드만 바뀌면 이 층은 다시 쓴다.
COPY pyproject.toml README.md requirements.lock ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.lock

COPY app ./app
RUN python -m pip install --no-deps .

RUN mkdir -p /data/media \
    && chown -R talkto:talkto /app /data

USER talkto

EXPOSE 8000

# CloudFront 뒤에서 돈다: 원래 주소·https 정보를 X-Forwarded-* 로 받는다
# keep-alive 는 CloudFront 쪽 연결 유지 시간(최대 60초)보다 길게 둔다(서버가 먼저 끊어 502 가 나지 않게)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 75"]
