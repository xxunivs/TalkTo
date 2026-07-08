# TalkTo APP AI — 프로덕션 컨테이너
# Python 3.11 slim + base 의존성만(서빙 경로가 torch/speaker를 쓰지 않으므로 미포함).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 소스 + 메타데이터 복사 후 설치 (base 의존성은 순수 파이썬 휠이라 컴파일 불필요)
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .

# 비루트 실행
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# 컨테이너 헬스체크 — GET /health
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/health').status==200 else 1)"

# PORT는 호스팅(Cloud Run/Render 등)이 주입할 수 있게 환경변수로 받는다
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]
