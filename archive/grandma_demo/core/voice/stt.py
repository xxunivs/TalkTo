import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def transcribe(audio_file_path: str) -> dict:
    try:
        with open(audio_file_path, "rb") as audio_file:
            response = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="ko",
            )
        return {"success": True, "text": response.text}
    except FileNotFoundError:
        return {"success": False, "error": "음성 파일을 찾을 수 없습니다."}
    except Exception as e:
        return {"success": False, "error": f"STT 변환 실패: {str(e)}"}

if __name__ == "__main__":
    test_file = "data/voice_raw/할머니 목소리 8.wav"
    print(f"STT 변환 중: {test_file}")
    result = transcribe(test_file)
    if result["success"]:
        print(f"결과: {result['text']}")
    else:
        print(f"실패: {result['error']}")
