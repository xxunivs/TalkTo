"""
봇 토큰 검증 + TARGET_CHAT_ID 확인 스크립트.

사용법:
    source .env
    python3 scripts/init_bot.py
"""
import os
import sys
import httpx


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        print("ERROR: BOT_TOKEN 환경변수 필요")
        print("  export BOT_TOKEN=...")
        sys.exit(1)

    # Verify bot
    r = httpx.get(f"https://api.telegram.org/bot{token}/getMe")
    r.raise_for_status()
    bot_info = r.json()["result"]
    print(f"봇 확인: @{bot_info['username']} (id={bot_info['id']})")

    # Get recent updates to find chat_id
    r = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", params={"limit": 10})
    r.raise_for_status()
    updates = r.json().get("result", [])

    if updates:
        print("\n최근 메시지를 보낸 사용자:")
        seen = set()
        for u in updates:
            msg = u.get("message", {})
            chat = msg.get("chat", {})
            chat_id = chat.get("id")
            name = chat.get("first_name", "") + " " + chat.get("last_name", "")
            if chat_id and chat_id not in seen:
                seen.add(chat_id)
                print(f"  chat_id={chat_id}  name={name.strip()}")
        print("\n위 chat_id 중 하나를 .env의 TARGET_CHAT_ID에 설정하세요.")
    else:
        print("\n봇에게 아무 메시지나 보낸 뒤 다시 실행하세요.")
        print("(봇에게 /start 를 보내면 됩니다)")


if __name__ == "__main__":
    main()
