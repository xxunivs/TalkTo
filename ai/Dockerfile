# TalkTo APP AI — 프로덕션 컨테이너
# base 의존성 + 화자식별(ECAPA) 스택 포함. 화자식별은 CPU torch/speechbrain + ffmpeg
# (torchaudio가 m4a/mp3 디코딩에 사용)로 동작. 참조 목소리 샘플이 오면 대상자 화자를 확정.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SPEAKER_MODEL_DIR=/tmp/ecapa_model

WORKDIR /app

# ffmpeg: torchaudio가 m4a/mp3 등 디코딩에 사용 (화자식별 오디오 로딩)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app

# CPU 전용 torch/torchaudio를 먼저 설치(기본 PyPI는 CUDA 빌드라 용량 큼) → 그다음 앱+speaker extra
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir ".[speaker]"

# 비루트 실행
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# 컨테이너 헬스체크 — GET /health
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/health').status==200 else 1)"

# PORT는 호스팅(Cloud Run/Render 등)이 주입할 수 있게 환경변수로 받는다
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]
