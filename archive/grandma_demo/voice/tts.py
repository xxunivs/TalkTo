"""
TTS (Text-to-Speech): 텍스트를 할머니 목소리로 변환.
ElevenLabs API 사용.
"""
import os
import sys
import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "lTmcD2Kp43w1lHsMQmq7")


def synthesize(text: str, output_path: str = "output.mp3") -> dict:
    """
    텍스트를 할머니 목소리로 변환해서 음성 파일로 저장.

    매개변수:
        text: 변환할 텍스트
        output_path: 저장할 파일 경로

    반환값:
        {"success": True, "path": "저장된 파일 경로"}
        또는
        {"success": False, "error": "에러 메시지"}
    """
    try:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"

        headers = {
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
        }

        data = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
            },
        }

        response = requests.post(url, json=data, headers=headers)

        if response.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(response.content)
            return {"success": True, "path": output_path}
        else:
            return {"success": False, "error": f"ElevenLabs API 오류: {response.status_code}"}

    except Exception as e:
        return {"success": False, "error": f"TTS 변환 실패: {str(e)}"}


if __name__ == "__main__":
    test_text = "어. 오랜만이네. 밥은 잘 챙겨 먹고 다니니? 할머니는 요즘 허리가 좀 아파서 고생이다. 근데 너는 회사에서 잘 지내고 있지? 도시락 싸가지고 다닌다며? 잘하는구나. 건강 잘 챙겨라. 사랑한다."
    output = "data/tts_test.mp3"
    print(f"TTS 변환 중: '{test_text}'")
    result = synthesize(test_text, output)
    if result["success"]:
        print(f"완료! 파일: {result['path']}")
        print("재생하려면: open data/tts_test.mp3")
    else:
        print(f"실패: {result['error']}")
