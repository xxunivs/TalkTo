FROM python:3.11-slim

# TDLib + tgvoip native deps
RUN apt-get update && apt-get install -y \
    build-essential cmake gperf libssl-dev zlib1g-dev \
    libopus-dev libsodium-dev ffmpeg git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

CMD ["python", "main.py"]
