#!/usr/bin/env python3
"""
대화 텍스트를 cognee 지식 그래프에 추가하는 스크립트.

사용법:
  # 텍스트 직접 전달
  python3 update_knowledge.py --text "사용자가 말한 내용"

  # 파일에서 읽기
  python3 update_knowledge.py --file /path/to/conversation.txt

  # stdin에서 읽기
  echo "대화 내용" | python3 update_knowledge.py --stdin

데이터셋: telegram-user-<sender_id> (사용자별 격리)
"""

import os
import sys
import argparse
import asyncio
from datetime import datetime, timezone

# cognee-mcp 환경 로드
ENV_PATH = "/home/commit/cognee-mcp/.env"

from dotenv import load_dotenv
load_dotenv(ENV_PATH)
os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")

import litellm
litellm.drop_params = True

import cognee
from cognee.api.v1.search import SearchType

SENDER_ID = "8786432628"
DEFAULT_DATASET = f"telegram-user-{SENDER_ID}"

# cognee 시스템 디렉토리 설정 (MCP와 동일한 DB 사용)
COGNEE_SYSTEM_DIR = os.environ.get("COGNEE_SYSTEM_DIR", "/home/commit/.cognee_system_mcp")
COGNEE_DATA_DIR = os.environ.get("COGNEE_DATA_DIR", "/home/commit/.cognee_data")


def configure_cognee():
    """cognee LLM 및 스토리지 설정"""
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


async def update_knowledge(text: str, dataset_name: str = DEFAULT_DATASET):
    """텍스트를 cognee에 추가하고 지식 그래프를 빌드"""
    configure_cognee()

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    tagged_text = f"[{timestamp}] {text}"

    print(f"[update_knowledge] 데이터셋: {dataset_name}")
    print(f"[update_knowledge] 텍스트 길이: {len(text)} chars")

    # 1. cognee에 텍스트 추가
    await cognee.add(tagged_text, dataset_name=dataset_name)
    print("[update_knowledge] cognee.add() 완료")

    # 2. 지식 그래프 빌드 (엔티티/관계 추출 + 임베딩)
    await cognee.cognify(datasets=[dataset_name])
    print("[update_knowledge] cognee.cognify() 완료")

    return True


def main():
    parser = argparse.ArgumentParser(description="대화 텍스트를 cognee 지식 그래프에 추가")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--text", type=str, help="직접 텍스트 전달")
    group.add_argument("--file", type=str, help="파일에서 텍스트 읽기")
    group.add_argument("--stdin", action="store_true", help="stdin에서 읽기")
    parser.add_argument("--dataset", type=str, default=DEFAULT_DATASET, help="cognee 데이터셋 이름")

    args = parser.parse_args()

    if args.text:
        text = args.text
    elif args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
    elif args.stdin:
        text = sys.stdin.read()

    text = text.strip()
    if not text:
        print("[update_knowledge] 빈 텍스트, 스킵")
        sys.exit(0)

    asyncio.run(update_knowledge(text, dataset_name=args.dataset))
    print("[update_knowledge] 완료!")


if __name__ == "__main__":
    main()
