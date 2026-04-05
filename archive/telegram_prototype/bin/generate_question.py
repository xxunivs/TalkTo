#!/usr/bin/env python3
"""
cognee 지식 그래프 기반 동적 질문 생성 + 텔레그램 전송 스크립트.

10분마다 cron으로 실행:
1. cognee에서 사용자 페르소나(지식 그래프) 추출
2. LLM에 컨텍스트 전달 → 새로운 질문 생성
3. 텔레그램 Bot API로 전송
4. 전송된 질문을 히스토리에 기록 (중복 방지)
"""

import os
import sys
import json
import asyncio
import fcntl
from datetime import datetime, timezone
from pathlib import Path

# cognee-mcp 환경 로드
ENV_PATH = "/home/commit/cognee-mcp/.env.univs"
OPENCLAW_ENV = "/home/commit/.openclaw/.env"
PROJECT_ENV = Path(__file__).resolve().parent.parent / ".env"

from dotenv import load_dotenv
load_dotenv(ENV_PATH)
load_dotenv(OPENCLAW_ENV)
load_dotenv(PROJECT_ENV)
os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")

import litellm
litellm.drop_params = True

import cognee
from cognee.api.v1.search import SearchType
import requests

# --- 설정 ---
SENDER_ID = "8786432628"
DATASET_NAME = "dataset_univs"
CHAT_ID = SENDER_ID
BOT_TOKEN = os.environ["BOT_TOKEN"]

STATE_DIR = Path("/home/commit/.openclaw/workspaces/UnivsFirst_agent/state")
STATE_FILE = STATE_DIR / "dynamic-question.json"
HISTORY_FILE = STATE_DIR / "question-history.json"
LOCK_FILE = STATE_DIR / "dynamic-question.lock"

# cognee 디렉토리
COGNEE_SYSTEM_DIR = os.environ.get("COGNEE_SYSTEM_DIR", "/home/commit/.cognee_system_mcp")
COGNEE_DATA_DIR = os.environ.get("COGNEE_DATA_DIR", "/home/commit/.cognee_data")

# LLM 설정 (Together AI)
LLM_API_KEY = os.environ.get("LLM_API_KEY")
LLM_MODEL = os.environ.get("LLM_MODEL", "together_ai/Qwen/Qwen3-235B-A22B-Instruct-2507-FP8")
LLM_ENDPOINT = os.environ.get("LLM_ENDPOINT", "https://api.together.xyz/v1")

# 최대 질문 히스토리 (중복 방지용)
MAX_HISTORY = 100

# 지식 그래프가 비어있을 때 사용할 시드 질문
SEED_QUESTIONS = [
    "안녕하세요! 저는 당신에 대해 알아가고 싶어요. 먼저, 요즘 가장 관심 있는 분야가 뭔지 알려주실 수 있나요?",
    "반가워요! 자기소개를 해주실 수 있나요? 어떤 일을 하고 계신지, 취미는 뭔지 궁금해요.",
    "안녕하세요! 오늘 하루는 어떠셨나요? 특별한 일이 있었나요?",
]


def configure_cognee():
    llm_config = {
        "llm_api_key": LLM_API_KEY,
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


def load_history() -> list[str]:
    """이전에 보낸 질문 목록 로드"""
    if HISTORY_FILE.exists():
        try:
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            return data.get("questions", [])
        except (json.JSONDecodeError, KeyError):
            pass
    return []


def save_history(questions: list[str]):
    """질문 히스토리 저장 (최근 MAX_HISTORY개만 유지)"""
    trimmed = questions[-MAX_HISTORY:]
    HISTORY_FILE.write_text(
        json.dumps({"questions": trimmed, "updatedAt": datetime.now(timezone.utc).isoformat()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            pass
    return {"count": 0}


def save_state(state: dict):
    state["updatedAt"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


async def extract_persona() -> str:
    """cognee에서 사용자 페르소나 정보 추출"""
    configure_cognee()

    queries = [
        "이 사용자는 어떤 사람인가? 관심사, 성격, 직업, 취미 등을 요약해줘.",
        "사용자가 최근에 이야기한 주제들은 무엇인가?",
    ]

    persona_parts = []
    for query in queries:
        try:
            results = await cognee.search(
                query,
                query_type=SearchType.GRAPH_COMPLETION,
                datasets=[DATASET_NAME],
            )
            if results:
                for r in results[:3]:
                    text = str(r).strip()
                    if text:
                        persona_parts.append(text)
        except Exception as e:
            print(f"[generate_question] cognee search 오류: {e}", file=sys.stderr)

    return "\n".join(persona_parts) if persona_parts else ""


def generate_question_with_llm(persona: str, history: list[str]) -> str:
    """LLM을 사용하여 동적 질문 생성"""
    recent_history = history[-10:]  # 최근 10개 질문만 참조
    history_text = "\n".join(f"- {q}" for q in recent_history) if recent_history else "(아직 없음)"

    prompt = f"""당신은 사용자에 대해 깊이 알아가고 싶어하는 따뜻하고 호기심 많은 AI 친구입니다.

## 사용자에 대해 알고 있는 정보 (지식 그래프에서 추출):
{persona if persona else "(아직 정보 없음 - 기본적인 질문부터 시작하세요)"}

## 이전에 이미 한 질문들 (중복 금지):
{history_text}

## 규칙:
1. 위에서 이미 한 질문과 비슷한 질문은 절대 하지 마세요.
2. 사용자의 관심사나 경험을 바탕으로, 더 깊이 있는 후속 질문을 하세요.
3. 지식 그래프에 정보가 적으면 기본적인 것부터 물어보세요 (취미, 관심사, 일상 등).
4. 질문은 한국어로, 자연스럽고 친근한 톤으로 작성하세요.
5. 질문은 딱 하나만, 2-3문장 이내로 해주세요.
6. 앞에 이모지를 하나 넣어주세요.

질문만 출력하세요. 다른 설명은 필요 없습니다."""

    try:
        response = litellm.completion(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            api_key=LLM_API_KEY,
            api_base=LLM_ENDPOINT,
            max_tokens=256,
            temperature=0.8,
        )
        question = response.choices[0].message.content.strip()
        # <think> 태그 제거 (Qwen3 reasoning 모델)
        if "<think>" in question:
            import re
            question = re.sub(r"<think>.*?</think>", "", question, flags=re.DOTALL).strip()
        return question
    except Exception as e:
        print(f"[generate_question] LLM 호출 오류: {e}", file=sys.stderr)
        return ""


def send_telegram(text: str) -> bool:
    """텔레그램으로 메시지 전송"""
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[generate_question] 텔레그램 전송 오류: {e}", file=sys.stderr)
        return False


async def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # flock으로 중복 실행 방지
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(0)

    state = load_state()
    history = load_history()
    count = state.get("count", 0) + 1

    # 1. cognee에서 페르소나 추출
    print(f"[generate_question] #{count} - 페르소나 추출 중...")
    persona = await extract_persona()
    print(f"[generate_question] 페르소나 길이: {len(persona)} chars")

    # 2. 질문 생성
    if not persona and count <= len(SEED_QUESTIONS):
        # 지식 그래프가 비어있으면 시드 질문 사용
        question = SEED_QUESTIONS[count - 1]
        print(f"[generate_question] 시드 질문 사용 #{count}")
    else:
        question = generate_question_with_llm(persona, history)
        if not question:
            print("[generate_question] LLM 질문 생성 실패, 스킵", file=sys.stderr)
            sys.exit(1)
    print(f"[generate_question] 생성된 질문: {question[:80]}...")

    # 3. 텔레그램 전송
    if send_telegram(question):
        print(f"[generate_question] 전송 완료!")
        history.append(question)
        save_history(history)
        state["count"] = count
        state["lastQuestion"] = question
        save_state(state)
    else:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
