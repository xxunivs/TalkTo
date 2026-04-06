"""
최초 1회 TDLib 세션 인증.

실행하면 전화번호 → 인증코드 → 2FA 비밀번호 순서로 입력받아
userbot/session/ 에 세션 파일 생성.
Docker 밖(호스트)에서 실행해야 stdin 입력 가능.
"""
import os
import sys

def main():
    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    phone = os.environ.get("TELEGRAM_PHONE_NUMBER")

    if not all([api_id, api_hash, phone]):
        print("ERROR: .env에 TELEGRAM_API_ID, API_HASH, PHONE_NUMBER 설정 필요")
        print("  source .env 또는 export 후 재실행")
        sys.exit(1)

    print(f"Phone: {phone}")
    print("TDLib 세션 초기화는 아직 구현 전입니다.")
    print("TODO: aiotdlib 또는 python-telegram 으로 세션 생성")
    raise NotImplementedError("TDLib session init pending")


if __name__ == "__main__":
    main()
