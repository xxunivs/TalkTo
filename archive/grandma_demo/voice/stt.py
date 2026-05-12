"""
STT (Speech-to-Text): 음성 파일을 텍스트로 변환.
OpenAI Whisper API 사용.
"""
import os
import sys
from dotenv import load_dotenv
from openai import OpenAI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def transcribe(audio_file_path: str) -> dict:
    """
    음성 파일을 텍스트로 변환.

    매개변수:
        audio_file_path: 음성 파일 경로 (wav, mp3, m4a 등)

    반환값:
        {"success": True, "text": "변환된 텍스트"}
        또는
        {"success": False, "error": "에러 메시지"}
    """
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
