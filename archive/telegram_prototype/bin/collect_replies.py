#!/usr/bin/env python3
"""
텔레그램에서 사용자 답변을 수집하여 cognee 지식 그래프에 저장.
cron으로 2분마다 실행.
"""

import os
import sys
import json
import asyncio
import fcntl
from datetime import datetime, timezone
from pathlib import Path

ENV_PATH = "/home/commit/cognee-mcp/.env"
PROJECT_ENV = Path(__file__).resolve().parent.parent / ".env"
from dotenv import load_dotenv
load_dotenv(ENV_PATH)
load_dotenv(PROJECT_ENV)
os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")

import litellm
litellm.drop_params = True

import cognee
import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]
SENDER_ID = 8786432628
DATASET_NAME = f"telegram-user-{SENDER_ID}"

STATE_DIR = Path("/home/commit/.openclaw/workspaces/UnivsFirst_agent/state")
OFFSET_FILE = STATE_DIR / "collect-replies-offset.json"
LOCK_FILE = STATE_DIR / "collect-replies.lock"

COGNEE_SYSTEM_DIR = os.environ.get("COGNEE_SYSTEM_DIR", "/home/commit/.cognee_system_mcp")
COGNEE_DATA_DIR = os.environ.get("COGNEE_DATA_DIR", "/home/commit/.cognee_data")


def configure_cognee():
    llm_config = {
        "llm_api_key": os.environ.get("LLM_API_KEY"),
        "llm_provider": os.environ.get("LLM_PROVIDER", "openai"),
        "llm_model": os.environ.get("LLM_MODEL"),
        "llm_instructor_mode": os.environ.get("LLM_INSTRUCTOR_MODE", "json_mode"),
    }
    endpoint = os.environ.get("LLM_ENDPOINT")
    if endpoint:
        llm_config["llm_endpoint"] = endpoint
    cognee.config.set_llm_config(llm_config)
    cognee.config.system_root_directory(COGNEE_SYSTEM_DIR)
    cognee.config.data_root_directory(COGNEE_DATA_DIR)


def load_offset() -> int:
    if OFFSET_FILE.exists():
        try:
            return json.loads(OFFSET_FILE.read_text()).get("offset", 0)
        except (json.JSONDecodeError, KeyError):
            pass
    return 0


def save_offset(offset: int):
    OFFSET_FILE.write_text(json.dumps({"offset": offset, "updatedAt": datetime.now(timezone.utc).isoformat()}))


def fetch_updates(offset: int) -> list[dict]:
    """텔레그램에서 새 메시지 가져오기"""
    resp = requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
        params={"offset": offset, "timeout": 0, "allowed_updates": json.dumps(["message"])},
        timeout=10
    )
    resp.raise_for_status()
    return resp.json().get("result", [])


async def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(0)

    offset = load_offset()
    updates = fetch_updates(offset + 1 if offset else 0)

    # 사용자 메시지만 필터링 (봇 메시지 제외)
    user_messages = []
    max_update_id = offset
    for u in updates:
        uid = u.get("update_id", 0)
        if uid > max_update_id:
            max_update_id = uid
        msg = u.get("message", {})
        sender = msg.get("from", {})
        if sender.get("id") == SENDER_ID and not sender.get("is_bot") and msg.get("text"):
            user_messages.append(msg["text"])

    if not user_messages:
        if max_update_id > offset:
            save_offset(max_update_id)
        return

    # cognee에 저장
    configure_cognee()
    combined = "\n".join(user_messages)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    tagged = f"[{timestamp}] 사용자 답변: {combined}"

    print(f"[collect_replies] {len(user_messages)}개 메시지 수집, cognee에 저장 중...")
    await cognee.add(tagged, dataset_name=DATASET_NAME)
    await cognee.cognify(datasets=[DATASET_NAME])
    print(f"[collect_replies] 완료!")

    save_offset(max_update_id)


if __name__ == "__main__":
    asyncio.run(main())
