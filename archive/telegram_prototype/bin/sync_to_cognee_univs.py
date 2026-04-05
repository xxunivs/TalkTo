#!/usr/bin/env python3
"""
UnivsFirst_agent 세션 로그에서 사용자 메시지를 추출하여
cognee-univs 지식 그래프(dataset_univs)에 자동 저장.

cron으로 2분마다 실행.
세션 로그의 user role 메시지 중 sender_id=8786432628인 것만 수집.
"""

import os
import sys
import json
import asyncio
import fcntl
import re
from datetime import datetime, timezone
from pathlib import Path

ENV_PATH = "/home/commit/cognee-mcp/.env.univs"
from dotenv import load_dotenv
load_dotenv(ENV_PATH)
os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")

import litellm
litellm.drop_params = True

import cognee

SENDER_ID = "8786432628"
DATASET_NAME = "dataset_univs"

SESSIONS_DIR = Path("/home/commit/.openclaw/agents/UnivsFirst_agent/sessions")
STATE_DIR = Path("/home/commit/.openclaw/workspaces/UnivsFirst_agent/state")
OFFSET_FILE = STATE_DIR / "sync-cognee-univs-offset.json"
LOCK_FILE = STATE_DIR / "sync-cognee-univs.lock"

COGNEE_SYSTEM_DIR = os.environ.get("COGNEE_SYSTEM_DIR", "/home/commit/.cognee_system_univs")
COGNEE_DATA_DIR = os.environ.get("COGNEE_DATA_DIR", "/home/commit/.cognee_data_univs")


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


def load_offset() -> dict:
    if OFFSET_FILE.exists():
        try:
            return json.loads(OFFSET_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    return {"processed_ids": [], "last_session": None, "last_line": 0}


def save_offset(state: dict):
    state["updatedAt"] = datetime.now(timezone.utc).isoformat()
    # Keep only last 500 processed IDs to prevent unbounded growth
    state["processed_ids"] = state.get("processed_ids", [])[-500:]
    OFFSET_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def extract_user_text(content_blocks: list) -> str:
    """사용자 메시지에서 실제 텍스트만 추출 (metadata 제거)"""
    texts = []
    for block in content_blocks:
        if block.get("type") != "text":
            continue
        raw = block["text"]
        # metadata JSON 블록 이후의 실제 메시지만 추출
        # 패턴: metadata blocks 끝나고 나오는 실제 텍스트
        parts = re.split(r"```\s*\n", raw)
        if len(parts) >= 3:
            # metadata json blocks 이후 텍스트
            after_meta = parts[-1].strip()
            if after_meta:
                texts.append(after_meta)
        else:
            texts.append(raw.strip())
    return "\n".join(texts)


def get_active_session_file() -> Path | None:
    """가장 최근 세션 jsonl 파일 찾기"""
    jsonl_files = list(SESSIONS_DIR.glob("*.jsonl"))
    if not jsonl_files:
        return None
    return max(jsonl_files, key=lambda f: f.stat().st_mtime)


async def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(0)

    session_file = get_active_session_file()
    if not session_file:
        return

    offset = load_offset()
    processed_ids = set(offset.get("processed_ids", []))
    last_session = offset.get("last_session")
    last_line = offset.get("last_line", 0)

    # 세션 파일이 바뀌었으면 처음부터
    current_session = session_file.name
    if current_session != last_session:
        last_line = 0

    # 세션 로그 읽기
    user_messages = []
    new_ids = []
    line_num = 0

    with open(session_file, "r", encoding="utf-8") as f:
        for line in f:
            line_num += 1
            if line_num <= last_line:
                continue
            try:
                entry = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            if entry.get("type") != "message":
                continue

            msg = entry.get("message", {})
            if msg.get("role") != "user":
                continue

            msg_id = entry.get("id", "")
            if msg_id in processed_ids:
                continue

            # sender_id 확인
            content = msg.get("content", [])
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]

            full_text = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
            if SENDER_ID not in full_text:
                continue

            user_text = extract_user_text(content)
            if not user_text or len(user_text) < 2:
                continue

            # 단순 인사/명령 필터링
            if user_text.strip() in ("ㅎㅇ", "ㅎ", "ㅇ", "ㅇㅇ", "ㅋ", "ㅋㅋ", "ㅋㅋㅋ", "ok", "ㅎㅎ"):
                new_ids.append(msg_id)
                continue

            user_messages.append(user_text)
            new_ids.append(msg_id)

    if not user_messages and not new_ids:
        return

    if user_messages:
        configure_cognee()
        combined = "\n---\n".join(user_messages)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        tagged = f"[{timestamp}] 사용자(우주 김) 텔레그램 대화:\n{combined}"

        print(f"[sync_cognee_univs] {len(user_messages)}개 메시지 → dataset_univs 저장 중...")
        await cognee.add(tagged, dataset_name=DATASET_NAME)
        await cognee.cognify(datasets=[DATASET_NAME])
        print(f"[sync_cognee_univs] 완료!")

    # offset 업데이트
    all_ids = list(processed_ids | set(new_ids))
    save_offset({
        "processed_ids": all_ids,
        "last_session": current_session,
        "last_line": line_num,
    })


if __name__ == "__main__":
    asyncio.run(main())
