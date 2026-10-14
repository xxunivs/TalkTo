# TalkTo APP AI — 프로덕션 컨테이너
# base 의존성 + 화자식별(ECAPA) 스택 포함. 화자식별은 CPU torch/speechbrain 으로 동작한다(GPU 불필요).
# 참조 목소리 샘플이 오면 대상자 화자를 확정하고, 녹음 여러 개의 같은 목소리를 묶는다.
# linux/amd64·linux/arm64(Graviton) 모두 받아 둔 wheel 로만 깐다(컴파일러 없음).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ffmpeg·ffprobe: 녹음 자르기·소리 뽑기·목소리 재료 만들기(코드가 직접 부른다)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# 의존성은 운영 이미지에서 뽑은 고정 버전(requirements.lock)으로 깐다. torch·torchaudio 는
# CPU 전용 빌드(+cpu)라 pytorch 색인을 함께 본다. lock 만 먼저 복사해 README·코드가 바뀌어도
# 이 층(torch)과 아래 ECAPA 층은 캐시를 그대로 쓴다.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock \
    --extra-index-url https://download.pytorch.org/whl/cpu

# 목소리 묶기(ECAPA) 모델을 이미지에 미리 받아 둔다. 안 그러면 컨테이너마다 첫 요청 때 인터넷에서
# 89MB 를 받고, 인터넷이 막혀 있으면 조용히 '녹음별로만 나누기'로 떨어진다(2026-10 배포 점검).
# 받은 폴더에 파일이 있으면 speechbrain 이 인터넷 없이 그대로 쓴다. 버전은 로컬에서 쓴 것과 같게 고정.
# speechbrain 은 label_encoder 를 <폴더>/label_encoder.ckpt 로 찾는데 저장소에는 .txt 만 있다.
# 없으면 Hub 로 가려다 오프라인이라 실패한다. 같은 내용을 .ckpt 이름으로 복사해 둔다.
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('speechbrain/spkrec-ecapa-voxceleb', revision='0f99f2d0ebe89ac095bcc5903c4dd8f72b367286', local_dir='/opt/ecapa')" \
 && cp /opt/ecapa/label_encoder.txt /opt/ecapa/label_encoder.ckpt \
 && chmod -R a+rX /opt/ecapa
ENV SPEAKER_MODEL_DIR=/opt/ecapa \
    HF_HUB_OFFLINE=1 \
    OMP_NUM_THREADS=1

COPY pyproject.toml README.md ./
COPY infra ./infra
COPY pipeline ./pipeline
COPY orchestrator ./orchestrator
COPY prompts ./prompts
COPY main.py ./

# 의존성은 위에서 다 깔았다. 앱만 올린다.
RUN pip install --no-cache-dir --no-deps .

# 비루트 실행
RUN useradd --create-home --uid 10001 appuser
USER appuser

# 빌드 때 앱 코드로 ECAPA 를 실제로 한 번 돌려 본다(오프라인·앱 사용자 권한 그대로).
# torch/torchaudio/speechbrain 조합이 깨졌거나 모델 파일이 빠졌으면 조용히 '녹음별로만 나누기'로
# 떨어지는 대신 이미지 빌드가 실패한다. 앱 사용자로 돌려 root 소유 파일을 남기지 않는다.
RUN python -c "import torch; from pathlib import Path; \
from pipeline.s1_transcription.b_speaker_id import shared_embedder; \
emb = shared_embedder(Path('/opt/ecapa')).embed_waveform(torch.randn(1, 16000) * 0.1); \
assert isinstance(emb, list) and len(emb) > 0, 'empty ECAPA embedding'; \
print('ECAPA smoke test ok, dim', len(emb))"

# 배포(ECS)는 PORT=8001 을 넣는다(BE 가 8000). PORT 가 없으면 아래 CMD 기본값 8000 으로 뜬다.
EXPOSE 8001

# 컨테이너 헬스체크 — GET /health
# (AWS ECS 는 이미지 안의 HEALTHCHECK 를 보지 않는다. 작업 정의 healthCheck 에 같은 명령을 넣는다)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/health').status==200 else 1)"

# PORT는 호스팅(Cloud Run/Render 등)이 주입할 수 있게 환경변수로 받는다.
# 기본 1개: 목소리 묶기 한 번에 1GB 가까이 써서 4GB 컨테이너면 1개가 안전하다(늘리려면 8GB).
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-1}"]
