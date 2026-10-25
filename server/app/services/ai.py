"""AI 서버(TalkTo_PersonaAI_AI) 호출. 요청·응답 모양은 그 레포의 app/schema 를 따른다.

AI 서버는 모르는 필드를 거부한다(extra=forbid). 여기에 필드를 더하려면 AI 레포를 먼저 고친다.
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# 고객에게 보이는 문장. AI 서버가 돌려준 원문(키·주소·영어 오류)은 로그에만 남긴다.
AI_TROUBLE = "잠시 문제가 생겼어요. 잠시 뒤 다시 해 주세요."
# AI 회사(OpenAI) 쪽이 막혔을 때(크레딧·키). 저장한 것은 그대로 있다.
AI_DOWN = "지금 AI 연결이 잠시 멈춰 있어요. 곧 고칠게요. 저장한 내용은 그대로 있어요."

# 목소리 회사(Fish Audio) 쪽이 막혔을 때. 글 대화는 그대로 되고, 쓰던 목소리도 그대로 있다.
VOICE_DOWN = "지금은 목소리 만들기가 잠시 멈춰 있어요. 곧 고칠게요. 쓰던 목소리는 그대로예요."
# AI 서버가 정해 둔 시간 안에 답하지 않았을 때(jobs.TIMED_OUT 와 같은 문장)
AI_TIMED_OUT = "생각보다 오래 걸려서 멈췄어요. 잠시 뒤 다시 해 주세요."
# AI 서버와 연결이 끊겼을 때(AI 서버를 다시 켜는 중 등). 녹음 탓이 아니다.
AI_UNREACHABLE = "AI 연결이 잠시 끊겨 멈췄어요. 잠시 뒤 다시 해 주세요."
# 이 두 경우의 AIServerError.code. 목소리 만들기가 이 오류로 끊기면 회사에 목소리가 남았을 수 있다.
TIMEOUT_CODE = "ai_timeout"
UNREACHABLE_CODE = "ai_unreachable"
TRANSPORT_CODES = {TIMEOUT_CODE, UNREACHABLE_CODE}
# AI 서버가 빈 대답을 돌려줬을 때(저장하면 그 대화방의 다음 대화가 모두 막힌다)
EMPTY_REPLY_CODE = "ai_empty_reply"

# AI 서버가 알려 주는 AI 회사 문제(AI 레포 app/error.py 와 같은 코드)
PROVIDER_CODES = {"AI_PROVIDER_QUOTA", "AI_PROVIDER_AUTH", "AI_PROVIDER_BUSY"}
# 목소리 회사(Fish Audio) 문제. AI 회사 문제와 따로 센다(목소리가 막혀도 대화는 된다).
VOICE_PROVIDER_CODES = {
    "VOICE_PROVIDER_KEY",
    "VOICE_PROVIDER_CREDITS",
    "VOICE_PROVIDER_RATE_LIMIT",
    "VOICE_PROVIDER_UNAVAILABLE",
}
# 목소리 회사를 부르는 경로. 성공하면 목소리 회사(Fish) 문제가 풀린 것으로 보는데, Fish 에 간
# 호출만 센다: 만들기는 늘 Fish 로 가고, 읽기·지우기는 목소리 id 가 fish_ 일 때만 Fish 로 간다
# (예전 ElevenLabs·MiniMax 목소리가 성공해도 Fish 가 나은 것은 아니다).
VOICE_PATHS = ("/v1/voice/clone", "/v1/voice/speech", "/v1/voice/delete")
FISH_VOICE_PREFIX = "fish_"
# 대화에서 말로 보낸 녹음을 글로 옮기는 데 기다리는 시간(초). 녹음은 몇 분까지만 받는다
# (conversations.voice_message). 그 뒤 기억 찾기·대답이 CloudFront 시간(120초) 안에 끝나야 한다.
TRANSCRIBE_TIMEOUT = 60
# 말로 답하기(3분까지) 받아쓰기를 기다리는 시간(초). CloudFront 시간(120초) 안에 끝낸다.
ANSWER_TRANSCRIBE_TIMEOUT = 90
# AI 서버가 '말소리가 없다'고 답할 때의 코드(AI 레포 app/error.py NoSpeechError)
NO_SPEECH_CODE = "NO_SPEECH"
# 기억 검색 질문에 붙이는 앞 대화 수. AI 서버가 질문을 다시 쓸 때 보는 수와 같다
# (AI 레포 d_build_memory_embedding.REWRITE_HISTORY).
QUERY_HISTORY = 4

# 마지막으로 본 AI 회사 문제와 마지막 성공(관리자 화면 경고). 서버가 다시 뜨면 비운다.
provider_state: dict[str, Any] = {"code": None, "at": None, "ok_at": None}
# 목소리 회사 문제도 같은 모양으로 따로 둔다.
voice_state: dict[str, Any] = {"code": None, "at": None, "ok_at": None}


def _trouble(state: dict[str, Any]) -> dict[str, Any] | None:
    code, at, ok_at = state["code"], state["at"], state["ok_at"]
    if code is None or (ok_at is not None and ok_at > at):
        return None
    return {"code": code, "at": at}


def provider_trouble() -> dict[str, Any] | None:
    """아직 풀리지 않은 AI 회사 문제(그 뒤로 성공한 호출이 없으면). 없으면 None."""
    return _trouble(provider_state)


def voice_provider_trouble() -> dict[str, Any] | None:
    """아직 풀리지 않은 목소리 회사 문제(그 뒤로 Fish 에 간 호출이 성공하지 않았으면). 없으면 None.

    예전 목소리(ElevenLabs·MiniMax)로 읽기·지우기가 성공해도 풀린 것으로 보지 않는다(_reached_fish).
    """
    return _trouble(voice_state)


def _reached_fish(path: str, body: dict | None) -> bool:
    if path == "/v1/voice/clone":
        return True
    voice_id = (body or {}).get("voiceId")
    return path in VOICE_PATHS and str(voice_id or "").startswith(FISH_VOICE_PREFIX)


def _note_ok(path: str = "", body: dict | None = None) -> None:
    at = datetime.now(UTC)
    if path not in VOICE_PATHS:  # 목소리 호출은 AI 회사(OpenAI)를 부르지 않는다
        provider_state["ok_at"] = at
    if _reached_fish(path, body):
        voice_state["ok_at"] = at


class AIServerNotConfigured(RuntimeError):
    pass


@dataclass
class ChatReply:
    """대화 대답 한 번(AIService.reply)."""

    content: str
    memory_ids: list[str]
    latency_ms: int
    emotion: str | None = None
    speech_text: str | None = None
    # 기억 빈틈 주제(flagGaps 를 보냈고 AI 서버가 memoryGap 을 줬을 때만, 주제를 모르면 빈 글)
    memory_gap: str | None = None


class NoSpeechError(RuntimeError):
    """말로 답한 녹음에서 말소리를 찾지 못했다(AI 서버 422 NO_SPEECH). 다시 말해 달라고 한다."""


class AIServerError(RuntimeError):
    """AI 서버가 오류로 답했다. str(error) 는 고객에게 보여도 되는 문장이다."""

    def __init__(
        self, status_code: int, message: str = AI_TROUBLE, code: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _transport_error(error: httpx.HTTPError, path: str) -> "AIServerError":
    """AI 서버에 닿지 못했거나 시간이 넘었다(httpx). 고객에게 보일 문장의 AIServerError 로."""
    logger.warning("AI server %s on %s", type(error).__name__, path)
    if isinstance(error, httpx.TimeoutException):
        return AIServerError(504, AI_TIMED_OUT, TIMEOUT_CODE)
    return AIServerError(503, AI_UNREACHABLE, UNREACHABLE_CODE)


# 그분 동의 녹음의 저장소 접두(app/services/subject_consent.py). 이 녹음은 어떤 해외 회사로도
# 보내지 않는다(받아쓰기·목소리 만들기에 쓰지 않는다 — 2026-10-22 법률 자문 · SPEC §5.9)
SUBJECT_CONSENT_MARK = "/subject_consent/"


def _guard_subject_consent(path: str, kwargs: dict[str, Any]) -> None:
    """AI 서버로 보내는 본문(JSON·폼·파일 이름)에 그분 동의 녹음 주소가 들어 있으면 멈춘다."""
    parts = [path]
    if kwargs.get("json") is not None:
        parts.append(json.dumps(kwargs["json"], ensure_ascii=False, default=str))
    if kwargs.get("data"):
        parts.append(json.dumps(kwargs["data"], ensure_ascii=False, default=str))
    files = kwargs.get("files") or {}
    # httpx 는 {칸: (파일 이름, 내용, 형식)} 과 [(칸, (파일 이름, …))] 둘 다 받는다
    for item in files.values() if isinstance(files, dict) else (pair[1] for pair in files):
        if isinstance(item, tuple) and item:
            parts.append(str(item[0]))
    if any(SUBJECT_CONSENT_MARK in part for part in parts):
        raise RuntimeError("subject consent audio must never leave TalkTo")


async def _send(client: httpx.AsyncClient, path: str, **kwargs) -> httpx.Response:
    """AI 서버로 보낸다. 연결·시간 문제는 AIServerError 로 바꾼다(원문은 로그에만).

    보내기 전에 그분 동의 녹음이 섞이지 않았는지 본다(_guard_subject_consent)."""
    _guard_subject_consent(path, kwargs)
    settings = get_settings()
    try:
        return await client.post(f"{settings.ai_server_url.rstrip('/')}{path}", **kwargs)
    except httpx.HTTPError as error:
        raise _transport_error(error, path) from error


class AIService:
    def _headers(self) -> dict[str, str]:
        settings = get_settings()
        return {"X-AI-Server-Token": settings.ai_server_token} if settings.ai_server_token else {}

    async def _post(
        self, path: str, json: dict, *, long: bool = False, alert: bool = True
    ) -> httpx.Response:
        """페르소나 만들기에 쓰는 호출. AI 서버가 없으면 가짜 답 대신 분명히 실패한다.

        alert=False 는 시험 삼아 부르는 호출(가져오기의 목소리 미리 들어보기)이다. 실패해도 성공해도
        회사 문제 표시(provider_state·voice_state)와 관리자 알림을 건드리지 않는다.
        """
        settings = get_settings()
        if not settings.ai_server_url:
            raise AIServerNotConfigured("AI_SERVER_URL 이 설정되지 않았습니다.")
        timeout = (
            settings.ai_server_long_timeout_seconds if long else settings.ai_server_timeout_seconds
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await _send(client, path, headers=self._headers(), json=json)
        if response.status_code >= 400:
            raise _server_error(response, path, alert=alert)
        if alert:
            _note_ok(path, json)
        return response

    async def chat(
        self,
        *,
        message: str,
        history: list[dict[str, str]],
        memories: list[dict[str, Any]],
        persona: dict[str, Any],
        session_id: str | None = None,
        speaker_hint: str | None = None,
        timeout: float | None = None,
        memory_total: int | None = None,
    ) -> tuple[str, list[str], int, str | None, str | None]:
        """(대답, 쓴 기억 id, 걸린 ms, 음성 감정, 목소리로 읽을 글). reply() 의 앞 다섯 값."""
        got = await self.reply(
            message=message,
            history=history,
            memories=memories,
            persona=persona,
            session_id=session_id,
            speaker_hint=speaker_hint,
            timeout=timeout,
            memory_total=memory_total,
        )
        return got.content, got.memory_ids, got.latency_ms, got.emotion, got.speech_text

    async def reply(
        self,
        *,
        message: str,
        history: list[dict[str, str]],
        memories: list[dict[str, Any]],
        persona: dict[str, Any],
        session_id: str | None = None,
        speaker_hint: str | None = None,
        timeout: float | None = None,
        flag_gaps: bool = False,
        style_examples: list[dict[str, str]] | None = None,
        memory_total: int | None = None,
    ) -> ChatReply:
        """대화 대답 한 번(/v1/persona/responses). chat() 과 같고 기억 빈틈(memoryGap)까지 돌려준다.

        flag_gaps: 기억 빈틈 표시를 부탁한다(flagGaps — '더 알려 주실 기억'). AI 서버가 대답 끝
        표시를 떼어 memoryGap {topic} 으로 돌려준다. style_examples: 가족이 고쳐 준 말투
        [{original, corrected}](최근 10개). 둘 다 없으면 보내지 않아 요청이 예전과 같다.

        감정은 AI 서버가 VOICE_EMOTION=inline(MiniMax)·FISH_EMOTION=cue(Fish) 일 때만 온다.
        목소리로 읽을 글(speechText)은 FISH_EMOTION=inline 이고 연기 표시가 남았을 때만 온다.
        대답과 글자는 같고 연기 표시([웃으며] 등)만 들어 있다. 없으면(예전 AI 서버 포함) None 이고
        대답을 그대로 읽는다.
        speaker_hint 는 함께 쓰는 가족이 말할 때만 보낸다(그 가족의 이름 — 'AI: 지금 말하는 사람').
        주인이 말할 때는 보내지 않아 요청이 예전과 같다.
        memory_total: 그분 기억 총수(memoryTotal, 0 도 보낸다). memories 는 검색 상위 몇 개뿐이라
        AI 서버가 이 값으로 기억이 적은 페르소나를 가린다('기억이 적을 때' 지시·사전 판정 건너뜀).
        None 이면 보내지 않는다(AI 서버가 받은 기억 수로 짐작한다).
        timeout 은 이 호출에 남은 시간(초). 기본 시간보다 짧을 때만 쓴다(CloudFront 가 끊기 전에
        끝내려고).
        빈 대답은 오류로 본다(EMPTY_REPLY_CODE): 저장하면 다음 대화마다 지난 대화에 빈 글이 실려
        AI 서버가 거절해, 그 대화방이 다시는 대답을 받지 못한다.
        """
        settings = get_settings()
        if not settings.ai_server_url:
            return ChatReply(
                f"[개발용 응답] 대화 상대({persona['name']})가 받은 메시지: {message}",
                [item["id"] for item in memories[:3]],
                0,
            )
        started = monotonic()
        limit = settings.ai_server_timeout_seconds
        if timeout is not None:
            limit = max(1.0, min(limit, timeout))
        async with httpx.AsyncClient(timeout=limit) as client:
            response = await _send(
                client,
                "/v1/persona/responses",
                headers=self._headers(),
                json={
                    "message": message,
                    "history": history,
                    "memories": memories,
                    # AI 서버 중간계층이 대화방 단위로 이어 본다(이미 한 챙김·최근 턴)
                    **({"sessionId": session_id} if session_id else {}),
                    **({"speakerHint": speaker_hint} if speaker_hint else {}),
                    **({"flagGaps": True} if flag_gaps else {}),
                    **({"styleExamples": style_examples} if style_examples else {}),
                    # 예전 AI 서버는 모르는 칸을 거절한다(extra=forbid) — AI 서버와 함께 배포한다
                    **({"memoryTotal": memory_total} if memory_total is not None else {}),
                    "persona": {
                        "subjectId": persona["subject_id"],
                        "instructions": self._persona_instructions(persona),
                        "voiceId": persona.get("voice_id"),
                        # 감정 안전선용. 모르면 AI 서버가 고인으로 본다.
                        "deceased": persona.get("deceased"),
                        # 만 19세 미만 아이의 대화 상대면 미성년 안전 규칙을 켠다. 예전 AI 서버는
                        # 모르는 칸을 거절해서(extra=forbid) true 일 때만 싣는다
                        **({"minor": True} if persona.get("minor") else {}),
                        # 가져온 지침은 AI 서버 중간계층을 거치지 않는다(지침 + 참고 기억만)
                        **({"fixedPrompt": True} if persona.get("fixed_prompt") else {}),
                    },
                },
            )
            if response.status_code >= 400:
                raise _server_error(response, "/v1/persona/responses")
            payload = response.json()
        if not str(payload.get("content") or "").strip():
            logger.warning("AI server returned an empty reply")
            raise AIServerError(502, AI_TROUBLE, EMPTY_REPLY_CODE)
        _note_ok()
        gap = payload.get("memoryGap")
        return ChatReply(
            payload["content"],
            payload.get("retrievedMemoryIds", []),
            int((monotonic() - started) * 1000),
            payload.get("emotion"),
            payload.get("speechText") or None,
            # 부탁했을 때만 읽는다. 주제를 모르면 빈 글(빈틈은 있었다)
            str(gap.get("topic") or "")[:60] if flag_gaps and isinstance(gap, dict) else None,
        )

    async def gap_question(self, **body) -> dict:
        """POST /v1/persona/gap-question — 기억 빈틈 후보 → 가족에게 물을 질문(new·merge·skip).

        대화가 끝난 뒤 작업(gaps.after_turn)만 부른다. 대답 경로 밖이다.
        """
        return (await self._post("/v1/persona/gap-question", body)).json()

    async def transcribe(self, filename: str, content: bytes, content_type: str) -> str:
        """대화에서 말로 보낸 녹음을 글로. 오류는 다른 호출처럼 AIServerError(고객에게 보일 문장,
        AI 회사 문제 표시·알림)로 바꾼다. 시간은 TRANSCRIBE_TIMEOUT 까지만 기다린다(그 뒤에 기억
        찾기·대답까지 CloudFront 가 끊기 전(120초)에 끝내야 한다)."""
        settings = get_settings()
        if not settings.ai_server_url:
            return f"[개발용 음성 전사: {filename}]"
        path = "/v1/voice/transcriptions"
        async with httpx.AsyncClient(timeout=TRANSCRIBE_TIMEOUT) as client:
            response = await _send(
                client,
                path,
                headers=self._headers(),
                files={"audio_file": (filename, content, content_type)},
                data={"language": "ko"},
            )
        if response.status_code >= 400:
            raise _server_error(response, path)
        _note_ok(path)
        return response.json()["text"]

    async def transcribe_answer(self, filename: str, content: bytes, content_type: str) -> str:
        """말로 답하기(질문 답·기억 채우기·고쳐 쓰기 칸): 녹음 → 글(ElevenLabs Scribe, 한국어).

        AI 서버는 받아쓴 뒤 ElevenLabs 의 받아쓰기 기록을 바로 지우고, 녹음은 어디에도 남기지
        않는다. 말소리가 없으면 NoSpeechError, 그 밖의 오류는 AIServerError(고객에게 보일 문장).
        """
        settings = get_settings()
        if not settings.ai_server_url:
            return f"[개발용 음성 전사: {filename}]"
        path = "/v1/voice/answer-transcriptions"
        async with httpx.AsyncClient(timeout=ANSWER_TRANSCRIBE_TIMEOUT) as client:
            response = await _send(
                client,
                path,
                headers=self._headers(),
                files={"audio_file": (filename, content, content_type)},
            )
        if response.status_code == 422 and _error_code(response) == NO_SPEECH_CODE:
            raise NoSpeechError()
        if response.status_code >= 400:
            raise _server_error(response, path)
        _note_ok(path)
        return str(response.json().get("text") or "")

    # ── 페르소나 만들기 ──

    async def transcribe_recording(self, **body) -> dict:
        """POST /v1/analysis/transcriptions — 녹음 URL → 화자별 전사 구간."""
        return (await self._post("/v1/analysis/transcriptions", body, long=True)).json()

    async def suggest_subject_speaker(self, **body) -> str | None:
        response = await self._post("/v1/analysis/subject-speaker", body, long=True)
        return response.json().get("subjectSpeakerLabel")

    async def group_voices(self, **body) -> dict:
        """POST /v1/analysis/voice-groups — 녹음 사이에서 같은 사람 목소리를 묶는다."""
        return (await self._post("/v1/analysis/voice-groups", body, long=True)).json()

    async def analyze_recording(self, **body) -> dict:
        """POST /v1/analysis/recording — 기억·말투·민감·요약을 한 번에."""
        return (await self._post("/v1/analysis/recording", body, long=True)).json()

    async def reflect(self, **body) -> list[str]:
        response = await self._post("/v1/persona/reflection", body, long=True)
        return [item["insight"] for item in response.json().get("reflections", [])]

    async def assemble(self, **body) -> str:
        return (await self._post("/v1/persona/assembly", body)).json()["instructions"]

    async def embed(self, *, job_id: str, items: list[dict]) -> dict[str, list[float]]:
        vectors, _model, _dimensions = await self.embed_detailed(job_id=job_id, items=items)
        return vectors

    async def embed_detailed(
        self, *, job_id: str, items: list[dict]
    ) -> tuple[dict[str, list[float]], str | None, int | None]:
        """(기억 id → 벡터, 모델, 차원). 모델·차원은 AI 서버가 알려 준 값(첫 줄)이다.

        페르소나 가져오기가 차원을 확인할 때 쓴다(차원이 어긋나면 기억이 하나도 찾히지 않는다).
        """
        response = await self._post("/v1/embeddings", {"jobId": job_id, "items": items}, long=True)
        rows = response.json()["embeddings"]
        first = rows[0] if rows else {}
        return (
            {row["memorySegmentId"]: row["embedding"] for row in rows},
            first.get("model"),
            first.get("dimensions"),
        )

    async def embed_query(
        self, text: str, history: list[dict[str, str]] | None = None
    ) -> list[float]:
        vector, _model, _dimensions = await self.embed_query_detailed(text, history=history)
        return vector

    async def embed_query_detailed(
        self, text: str, *, history: list[dict[str, str]] | None = None
    ) -> tuple[list[float], str | None, int | None]:
        """(질문 벡터, 모델, 차원).

        history: 이 질문 앞의 대화(시간순 role·content). 최근 QUERY_HISTORY 개만 보낸다.
        AI 서버가 QUERY_REWRITE=on 이면 "얼마나 넣어?"처럼 앞 대화 없이는 뜻을 모르는 말을
        채워 그 문장으로 찾는다(꺼져 있으면 받기만 하고 쓰지 않는다). 앞 대화가 없으면
        보내지 않아 요청이 예전과 같다.
        """
        body: dict[str, Any] = {"text": text}
        recent = [
            {"role": m["role"], "content": m["content"]}
            for m in history or []
            if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
        ][-QUERY_HISTORY:]
        if recent:
            body["history"] = recent
        payload = (await self._post("/v1/embeddings/query", body)).json()
        return payload["embedding"], payload.get("model"), payload.get("dimensions")

    async def clone_voice(
        self, *, name: str, samples: list[dict], provider: str | None = None
    ) -> tuple[str, str]:
        """(voice_id, 만든 곳). AI 서버는 늘 Fish drama-3 로 만든다(voice_id 는 'fish_' 로 시작).

        AI 서버가 provider 를 돌려주지 않으면 보낸 값, 그것도 없으면 fish 로 본다.
        """
        body: dict[str, Any] = {"name": name, "samples": samples}
        if provider:
            body["provider"] = provider
        payload = (await self._post("/v1/voice/clone", body, long=True)).json()
        return payload["voiceId"], payload.get("provider") or provider or "fish"

    async def delete_voice(self, *, voice_id: str, provider: str | None = None) -> None:
        """목소리를 만든 회사에서 지운다(fish · elevenlabs · minimax).

        AI 서버는 목소리 id 로도 회사를 고른다(fish_ → Fish, TalkTo → MiniMax, 나머지 ElevenLabs).
        """
        body = {"voiceId": voice_id}
        if provider:
            body["provider"] = provider
        await self._post("/v1/voice/delete", body)

    async def speech(
        self, *, text: str, voice_id: str, emotion: str | None = None, alert: bool = True
    ) -> bytes:
        """대답을 그분 목소리로. 회사는 AI 서버가 목소리 id 로 고른다(보내는 모양은 그대로).

        alert=False: 시험 삼아 읽어 보기(다른 계정의 목소리일 수 있다). 실패해도 회사 문제로
        알리지 않는다.
        """
        body = {"text": text, "voiceId": voice_id}
        if emotion:
            body["emotion"] = emotion
        response = await self._post("/v1/voice/speech", body, alert=alert)
        return response.content

    @staticmethod
    def _persona_instructions(persona: dict[str, Any]) -> str:
        # 만들기 흐름으로 조립한 지침이 있으면 그것을 쓴다
        if persona.get("instructions"):
            return persona["instructions"]
        sections = [f"이름: {persona['name']}"]
        if persona.get("description"):
            sections.append(f"설명: {persona['description']}")
        if persona.get("speaking_style"):
            sections.append(f"말투: {persona['speaking_style']}")
        return "\n".join(sections)


def _error_code(response: httpx.Response) -> str | None:
    """AI 서버 오류 본문의 error.code(없으면 None)."""
    try:
        payload = response.json()
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    return error.get("code") if isinstance(error, dict) else None


def _server_error(response: httpx.Response, path: str, *, alert: bool = True) -> AIServerError:
    """AI 서버 오류 → 고객에게 보일 문장의 AIServerError.

    AI 회사(OpenAI)·목소리 회사(Fish Audio) 문제면 관리자 경고를 따로 남긴다.
    alert=False(시험 호출)면 경고·알림 없이 오류만 돌려준다.
    """
    code = None
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
    except ValueError:
        pass
    message = _error_message(response, path)
    if not alert:
        return AIServerError(response.status_code, message, code)
    if code in PROVIDER_CODES:
        provider_state["code"], provider_state["at"] = code, datetime.now(UTC)
        logger.error("AI provider unavailable (%s) on %s", code, path)
        from app.services import notify

        notify.ai_provider(code)
        message = AI_DOWN
    elif code in VOICE_PROVIDER_CODES:
        voice_state["code"], voice_state["at"] = code, datetime.now(UTC)
        logger.error("voice provider unavailable (%s) on %s", code, path)
        from app.services import notify

        notify.voice_provider(code)
        message = VOICE_DOWN
    return AIServerError(response.status_code, message, code)


def _error_message(response: httpx.Response, path: str = "") -> str:
    """고객에게 보일 한 문장. AI 서버가 돌려준 원문은 로그에만 남긴다."""
    try:
        payload = response.json()
    except ValueError:
        detail: Any = response.text[:300]
    else:
        detail = payload
        if isinstance(payload, dict):
            detail = (
                payload.get("message") or payload.get("detail") or payload.get("error") or payload
            )
        if isinstance(detail, dict):
            detail = detail.get("message") or detail
    logger.warning("AI server error %s %s: %s", path, response.status_code, str(detail)[:120])
    return AI_TROUBLE


ai_service = AIService()
