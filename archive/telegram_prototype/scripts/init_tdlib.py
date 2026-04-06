"""
최초 1회 Pyrogram 세션 인증.

실행하면 전화번호 → 인증코드 → 2FA 비밀번호 순서로 입력받아
userbot/session/ 에 세션 파일 생성.
Docker 밖(호스트)에서 실행해야 stdin 입력 가능.

사용법:
    source .env
    python3 scripts/init_tdlib.py
"""
import os
import sys
from pathlib import Path

from pyrogram import Client


def main():
    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    phone = os.environ.get("TELEGRAM_PHONE_NUMBER")

    if not all([api_id, api_hash, phone]):
        print("ERROR: 환경변수 설정 필요")
        print("  export TELEGRAM_API_ID=...")
        print("  export TELEGRAM_API_HASH=...")
        print("  export TELEGRAM_PHONE_NUMBER=...")
        print("또는: source .env")
        sys.exit(1)

    session_dir = Path(__file__).parent.parent / "userbot" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    print(f"Phone: {phone}")
    print(f"Session dir: {session_dir}")
    print("텔레그램 인증코드 입력이 필요합니다.\n")

    client = Client(
        name="talkto_userbot",
        api_id=int(api_id),
        api_hash=api_hash,
        phone_number=phone,
        workdir=str(session_dir),
    )

    with client:
        me = client.get_me()
        print(f"\n인증 성공! user_id={me.id}, name={me.first_name}")
        print(f"세션 파일: {session_dir}/talkto_userbot.session")


if __name__ == "__main__":
    main()
