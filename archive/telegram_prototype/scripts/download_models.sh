#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODEL_DIR="$BASE_DIR/data/models"

mkdir -p "$MODEL_DIR/kokoro" "$MODEL_DIR/whisper" "$MODEL_DIR/qwen"

echo "=== Faster-Whisper small model ==="
if [ ! -f "$MODEL_DIR/whisper/model.bin" ]; then
    pip install faster-whisper 2>/dev/null || true
    python3 -c "
from faster_whisper import WhisperModel
print('Downloading whisper small model (int8)...')
WhisperModel('small', device='cpu', compute_type='int8', download_root='$MODEL_DIR/whisper')
print('Done.')
"
else
    echo "Already exists, skip."
fi

echo ""
echo "=== Kokoro TTS ==="
echo "Kokoro 모델은 kokoro-tts 패키지 설치 시 자동 다운로드됩니다."
echo "수동 배치가 필요하면 $MODEL_DIR/kokoro/ 에 모델 파일을 넣으세요."

echo ""
echo "=== Qwen LLM (GGUF) ==="
if [ ! -f "$MODEL_DIR/qwen/model.gguf" ]; then
    echo "Qwen GGUF 모델을 $MODEL_DIR/qwen/ 에 다운로드하세요."
    echo "예: huggingface-cli download Qwen/Qwen2.5-3B-Instruct-GGUF --local-dir $MODEL_DIR/qwen/"
    echo "(16GB RAM CPU 환경에서는 3B Q4_K_M 권장)"
else
    echo "Already exists, skip."
fi

echo ""
echo "모델 디렉토리:"
du -sh "$MODEL_DIR"/* 2>/dev/null || echo "(비어있음)"
