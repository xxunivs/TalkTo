"""페르소나 응답 서빙 — 조립된 instructions + 런타임 기억 주입 → 응답.

BE가 (a) 저장한 페르소나 조립본(persona.instructions), (b) 이번 질의로 벡터 검색한
관련 기억 후보(memories), (c) 대화 이력(history), (d) 사용자 메시지(message)를 넘기면,
AI가 기억을 프롬프트에 주입해 응답을 생성한다. AI는 stateless — 벡터 검색은 BE 담당.
"""

import json
import logging
from collections import Counter

from infra.error import EmptyReplyError
from infra.setting import Settings
from infra.external_api.openai_client import chat_client, create_openai_client
from infra.schema.subject import FamilyNote, IntakeContext, SubjectContext
from infra.schema.persona import (
    MemoryCandidate,
    MemoryCandidateRequest,
    MemoryCandidateResponse,
    MemoryGap,
    PersonaAssemblyRequest,
    PersonaAssemblyResponse,
    PersonaResponseRequest,
    PersonaResponseResult,
)
from infra.schema.transcript import TranscriptSegment
from orchestrator.guard import ASK_BACK, CRISIS_LINE, drop_forgetting, says_forgot
from orchestrator.persona import infer_profile
from orchestrator.precheck import is_safety_turn
from orchestrator.text import listener_speech_level
from pipeline.s3_synthesis.common_rules import family_guide, render_common_rules
from pipeline.s3_synthesis.e_memory_gap import extra_system, split_gap, turn_gap
from pipeline.s4_voice.b_emotion import split_voice, voice_instruction, voice_mode
from pipeline.safety.minor_guard import (
    MINOR_NOTE, blocked_result, filter_reply, input_blocked, is_minor, is_polite, safe_history,
)

logger = logging.getLogger(__name__)

PROVIDER = "openai"

MEMORY_CANDIDATE_PROMPT = """대화의 마지막 주고받음에서 '앞으로도 기억할 만한 새로운 사실'만 뽑는다.

- 대상: 사용자가 말한 지속되는 근황·변화(취직·이사·결혼·출산·시험·건강·새 관계 등).
- 제외: 인사·감정 표현("보고 싶어")·일시적 상태·이미 뻔한 것·페르소나(어르신) 자신의 말.
- 없으면 빈 목록.

각 후보 필드:
- summary: 한 문장(누가 무엇). 사용자가 실제로 말한 것만, 지어내지 않는다.
- category: 짧은 분류(예: 직장·가족·건강·경조사·이사).
- importance: 1~10 정수(클수록 중요).
- confidence: 0~1(사용자 발화에 얼마나 분명히 나왔는가).

JSON으로만: {"candidates": [{"summary": "...", "category": "...", "importance": 5, "confidence": 0.8}]}"""


def _speech_segments(examples: list[str]) -> list[TranscriptSegment]:
    """말투 few-shot용 예시 발화를 대상자(화자 S) 세그먼트로 감싼다."""
    return [
        TranscriptSegment(
            segment_index=i, start_ms=i * 1000, end_ms=i * 1000 + 500,
            speaker_label="S", transcript_text=text,
        )
        for i, text in enumerate(examples)
        if text.strip()
    ]


def assemble_persona_instructions(
    request: PersonaAssemblyRequest, settings: Settings | None = None,
) -> PersonaAssemblyResponse:
    """subjectContext + intakeContext + 발화 예시 → 페르소나 system 프롬프트.

    LLM 미사용(순수 조립) — 비용·지연 없음. 산출물을 BE가 저장했다가 채팅 시
    /responses의 persona.instructions로 넘긴다. 기억은 조립 시 넣지 않고 런타임 주입.
    PERSONA_PROMPT_BUILDER=layered 면 4층 프롬프트(pipeline/persona_prompt). 기억 통찰(persona_insights)은 쓰지 않는다.
    """
    if settings is not None and settings.persona_prompt_builder == "layered":
        from pipeline.persona_prompt import build
        spec = build.for_assembly(request, settings.persona_death_talk, settings.style_card_min_utts)
        return PersonaAssemblyResponse(instructions=build.prompt(spec), subject_name=spec.name)
    segments = _speech_segments(request.speech_examples)
    instructions = assemble_persona_prompt(
        subject_context=request.subject_context,
        persons_results=[],
        sensitivity_results=[],
        segments_by_recording=[segments] if segments else [],
        subject_labels=["S"] if segments else [],
        intake_context=request.intake_context,
        persona_insights=request.persona_insights,
    )
    subject = request.subject_context.subject
    name = (subject.name if subject and subject.name else "대상자")
    return PersonaAssemblyResponse(instructions=instructions, subject_name=name)


def chat_reasoning(settings) -> dict:
    """대답 생성 호출에 붙일 추론 정도. 설정이 비면 아무것도 보내지 않는다(모델 기본값)."""
    effort = getattr(settings, "openai_chat_reasoning_effort", None)
    return {"reasoning_effort": effort} if effort else {}


def build_memory_block(memories) -> str:
    """검색된 기억 후보를 프롬프트에 주입할 텍스트 블록으로. 없으면 빈 문자열.

    순서는 기억 id 순으로 고정한다. BE 가 매 턴 다른 순서로 보내도 같은 기억이면 같은 글자가 되어
    프롬프트 캐시가 유지된다. id 는 무작위 UUID 라 종류·시간과 상관없는 고정 순서가 되는데, 세션 14 E1
    "기억 전부" c 조건(고정 무작위 순서)과 같은 성질이다(종류·시간순 d 조건은 성적이 낮았다).
    """
    if not memories:
        return ""
    # ponytail: 새 기억이 들어오면 그 자리부터 뒤는 캐시가 깨진다. 기록 시각을 BE 가 보내면 "옛것 먼저"로 바꿀 수 있다.
    memories = sorted(memories, key=lambda m: getattr(m, "id", "") or "")
    lines = []
    for memory in memories:
        title = (memory.title or "").strip()
        head = f"[{title}] " if title else ""
        lines.append(f"- {head}{memory.content.strip()}")
    return (
        "참고 기억(대화 화제가 닿으면 지명·햇수 등 구체 사실을 그대로 녹이고, 인사·짧은 "
        "안부엔 억지로 꺼내지 않는다. 없는 내용은 지어내지 않는다. 제목에 '(물으면만)'이 붙은 "
        "기억은 사용자가 그 이야기를 먼저 꺼내거나 물었을 때만 쓰고, 먼저 꺼내거나 넌지시 "
        "비치지 않는다):\n" + "\n".join(lines)
    )


# 세션 18 실험(D1·E1) 문구에서 대상자 한 사람의 숫자("714마디", "열에 넷" 등)만 빼서 모든 페르소나에 맞게 했다.
STYLE_LATE_NOTE = """# 말투 덧붙임 (실제 통화 말버릇. 위 말투 규칙과 부딪히면 이것을 따른다)
- 실제로는 문장 끝을 다 맺지 않는 말이 많다. "어.", "응."으로 받거나 "…하고.", "…해서.", "그거."처럼 흐려도 된다.
- 한두 마디로 끝내도 된다. "어, 그래." "응."만 하고 말아도 된다. 매번 내용을 채워 답하지 않는다.
- 잘 못 알아들었거나 놀라면 "어?", "뭐?" 하고 한마디로 되묻는다.
- 가끔은 묻는 말도 한다. 위의 "매 턴 되묻지 않는다"는 매번 묻지 말라는 뜻이지 묻지 말라는 뜻이 아니다."""
_CARD_HEAD = "# 말투 카드"  # pipeline/persona_prompt/from_transcript.py 가 붙이는 제목
_SHORT_RULE = "- 기본은 1~2문장으로 짧게."  # pipeline/persona_prompt/render.py style() 의 짧은 말투 규칙
_VISITORS_HEAD = "# 방문자"  # 공개 본인 페르소나(render.visitors): 처음 보는 방문자에게 존댓말


def late_style_note(instructions: str) -> str:
    """마지막 가족 말 바로 앞에 넣을 말투 덧붙임. 실험한 범위(4층 + 말투 카드 + 짧은 말투)일 때만, 아니면 빈 문자열.

    덧붙임의 예시("어.", "응.", "뭐?")는 반말이고 "부딪히면 이것을 따른다"고 하므로, 지금 상대에게 존댓말을
    쓰는 지시문에는 넣지 않는다: 가족 표의 '지금 대화하는 사람' 줄이 존댓말(가족 공유의 사위·며느리 등)이거나
    공개 본인 페르소나(방문자에게 존댓말). 실험은 반말로 말하는 가족 페르소나에서 했다.
    """
    # ponytail: 지시문 글자로 범위를 가린다. 말이 긴 페르소나·카드 없는 페르소나는 재 보고 넓힌다.
    if _CARD_HEAD not in instructions or _SHORT_RULE not in instructions:
        return ""
    if listener_speech_level(instructions) == "존댓말" or _VISITORS_HEAD in instructions:
        return ""
    return STYLE_LATE_NOTE


def warn_if_over_full_max(n_memories: int, settings: Settings) -> bool:
    """기억이 MEMORY_FULL_MAX 를 넘었나. 넘어도 자르지 않는다(AI 서버엔 벡터가 없어 스스로 고를 수 없다).
    넘으면 검색으로 바꾸는 것은 BE 가 한다. 여기서는 경고만 남긴다."""
    if n_memories <= settings.memory_full_max:
        return False
    logger.warning("memories %d > MEMORY_FULL_MAX %d: BE should send search results instead of all memories",
                   n_memories, settings.memory_full_max)
    return True


def generate_persona_response(
    request: PersonaResponseRequest, *, settings: Settings
) -> PersonaResponseResult:
    warn_if_over_full_max(len(request.memories), settings)
    # 만 19세 미만 살아 계신 대화 상대(BE persona.minor, 고인이면 끔). 가져온 지침(fixedPrompt)도 같다.
    minor = is_minor(request.persona.minor, request.persona.deceased)
    if minor and input_blocked(request.message):
        # 성적인 이야기·비밀 만남이면 모델을 부르지 않고 대체 글(말 높이별)로 답한다. 내용은 남기지 않는다.
        logger.info("minor guard skipped the model for subject %s", request.persona.subject_id)
        return blocked_result(request.persona.instructions)
    # BE 가 45초에 끊으므로 대화용 시간 한도(기본 20초 × 2번)로 부른다
    client = chat_client(settings, "persona response")

    messages = [{"role": "system", "content": request.persona.instructions}]
    # 음성 감정 지침(MiniMax VOICE_EMOTION · Fish FISH_EMOTION). 같은 호출에 system 하나만 더한다(LLM 을 더 부르지 않는다).
    # 정적인 글이라 지시문 바로 뒤에 둔다(프롬프트 캐시). 가져온 지침(fixedPrompt)도 같다.
    mode = voice_mode(settings, request.persona.voice_id)
    instruction = voice_instruction(mode)
    if instruction:
        messages.append({"role": "system", "content": instruction})
    # 기억 빈틈 표시(flagGaps)·가족이 고쳐 준 말투(styleExamples). 요청에 없으면 넣지 않는다(예전과 같은 메시지).
    for extra in extra_system(request.flag_gaps, request.style_examples):
        messages.append({"role": "system", "content": extra})
    # 미성년 규칙 — 이미 조립된 지침(규칙이 L1 에 없던 때 만든 것)·가져온 지침에도 듣게 매 턴 넣는다
    if minor:
        messages.append({"role": "system", "content": MINOR_NOTE})
    memory_block = build_memory_block(request.memories)
    if memory_block:
        messages.append({"role": "system", "content": memory_block})
    # 미성년이면 입력 검사에 걸렸던 가족 말과 그 대답은 앞 대화에서 뺀다(BE 는 기록에 남겨 보낸다)
    for turn in safe_history(request.history) if minor else request.history:
        messages.append({"role": turn.role, "content": turn.content})
    # 가져온 지침(fixedPrompt)은 MVP 때와 같은 메시지(지침 + 기억 + 대화)만 보낸다 — 덧붙임은 4층 지시문용
    late = settings.style_late_note == "on" and not request.persona.fixed_prompt
    note = late_style_note(request.persona.instructions) if late else ""
    if note:
        messages.append({"role": "system", "content": note})
    messages.append({"role": "user", "content": request.message})

    response = client.chat.completions.create(
        model=settings.openai_chat_model,
        messages=messages,
        **chat_reasoning(settings),
    )
    content = (response.choices[0].message.content or "").strip()
    # 기억 빈틈 표시([기억없음:주제])를 가장 먼저 뗀다. 음성 표시(cue·inline)는 [ ] 를 모두 지우므로 그 앞에서 떼야
    # 주제를 잃지 않고, off 여도 표시가 화면·대화 기록·기억에 남지 않는다(요청하지 않았는데 붙여도 지운다).
    gap_topic, content = split_gap(content)
    # 죽음·장례·임종·위기·건강·종교 화제면 빈틈 후보를 돌려주지 않는다(표시는 위에서 이미 뗐다). 중간계층과 같다.
    gap_topic = turn_gap(request.message, gap_topic) if request.flag_gaps else None
    # 감정·연기 표시를 떼어 낸다. content 는 표시 없는 글(화면·대화 기록·기억), speech_text 는 목소리용(inline)
    voice = split_voice(content, mode, deceased=request.persona.deceased)
    if not voice.text.strip():
        # 빈 대답(내용 걸러짐·감정 표시만 옴)을 돌려주면 BE 가 저장하고, 그 대화의 다음 턴이 모두 422 로 막혔다
        logger.warning("persona reply was empty (finish_reason=%s)",
                       getattr(response.choices[0], "finish_reason", None))
        raise EmptyReplyError()
    text, emotion, speech_text = voice.text, voice.emotion, voice.speech_text()
    # 고인은 잊은 듯 말하지 않는다(잠금 2). 가드가 없는 이 경로(가져온 지침 fixedPrompt 등)도 같은 문장을 지운다.
    # 표시를 뗀 글에서 지우고 연기 표시는 남은 글에 다시 꽂는다(화면·목소리 같은 글). 다 지워지면 고인 되묻기(감정·표시 버림).
    # 공개 판정·위기 예외는 가드와 같다: 위기·죽음 턴은 되묻기로 바꾸지 않는다(남는 문장이 있을 때만 지운다).
    if request.persona.deceased and not infer_profile(request.persona.instructions).public:
        kept = drop_forgetting(voice.text, "")
        if kept != voice.text:
            if kept:
                text, speech_text = kept, voice.speech_text(kept)
            elif not (is_safety_turn(request.message) or CRISIS_LINE.search(voice.text)):
                polite = listener_speech_level(request.persona.instructions) == "존댓말"
                text = ASK_BACK["family_polite_deceased" if polite else "family_deceased"][0]
                emotion, speech_text = None, None
    # 미성년 대답 검사: 성적·비밀 만남·위험한 내용이면 대답 전체를 대체 글로(위기 대응 턴·상담 번호는 그대로).
    # 바뀌면 감정·목소리 글과 기억 빈틈 후보도 버린다(그 화제로 가족에게 묻지 않게).
    if minor:
        safe, replaced = filter_reply(text, polite=is_polite(request.persona.instructions),
                                      safety_turn=is_safety_turn(request.message))
        if replaced:
            text, emotion, speech_text, gap_topic = safe, None, None, None
            logger.warning("minor guard replaced reply for subject %s", request.persona.subject_id)

    # 주입한 기억 전부를 '사용됨'으로 반환한다. 실제로 인용됐는지는 가리지 않는다.
    return PersonaResponseResult(
        content=text,
        retrieved_memory_ids=[memory.id for memory in request.memories],
        emotion=emotion,
        speech_text=speech_text,
        memory_gap=MemoryGap(topic=gap_topic) if gap_topic is not None else None,
        provider=PROVIDER,
        model=settings.openai_chat_model,
    )


_STORE_IMPORTANCE_THRESHOLD = 6  # importance >= 6이면 저장 추천


def extract_memory_candidates(
    request: MemoryCandidateRequest, *, settings: Settings
) -> MemoryCandidateResponse:
    """채팅 턴에서 앞으로 저장할 만한 새 기억 후보를 뽑는다.

    저장 판단(shouldStore)은 AI가 importance 임계값으로 내린다.
    BE는 shouldStore를 따르되, stateless인 AI가 못 하는 기존 기억과의 중복 제거만 한다.
    """
    client = create_openai_client(settings, "memory candidate extraction")

    convo = "\n".join(f"{m.role}: {m.content}" for m in request.history)
    convo += f"\nuser: {request.user_message}\nassistant: {request.assistant_message}"

    response = client.chat.completions.create(
        model=settings.openai_analysis_model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": MEMORY_CANDIDATE_PROMPT},
            {"role": "user", "content": convo},
        ],
    )
    payload = json.loads(response.choices[0].message.content or "{}")

    candidates = []
    for item in payload.get("candidates") or []:
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue
        # 코드가 범위를 강제(LLM이 벗어난 값을 줘도 스키마 검증에서 터지지 않게)
        try:
            importance = int(item.get("importance", 5))
        except (TypeError, ValueError):
            importance = 5
        importance = max(1, min(10, importance))
        try:
            confidence = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        category = (str(item.get("category")).strip() or None) if item.get("category") else None
        candidates.append(
            MemoryCandidate(
                summary=summary,
                category=category,
                importance=importance,
                confidence=confidence,
                should_store=importance >= _STORE_IMPORTANCE_THRESHOLD,
            )
        )

    return MemoryCandidateResponse(
        candidates=candidates,
        provider=PROVIDER,
        model=settings.openai_analysis_model,
    )


# ===== 조립 (assembler) =====
_SENSITIVITY_LABELS = {
    "health": "건강·병에 대한 구체적 얘기(병명·병원비 등)",
    "familyConflict": "가족 간 갈등·서운함",
    "asset": "돈·재산·상속",
    "death": "죽음·장례",
    "thirdParty": "다른 가족의 사생활(직장·주소·건강 등)",
}


def merge_persons(persons_results: list[dict]) -> list[dict]:
    """여러 녹음의 persons를 이름 기준 병합. confidence·언급 빈도로 관계 확정."""
    by_name: dict[str, dict] = {}
    for result in persons_results:
        for person in result.get("persons", []):
            name = person["name"]
            entry = by_name.setdefault(
                name, {"name": name, "relations": Counter(), "mentions": set()}
            )
            if person.get("relationToSubject"):
                weight = 2 if person.get("confidence") == "confirmed" else 1
                entry["relations"][person["relationToSubject"]] += weight
            entry["mentions"].update(person.get("mentions", []))
    merged = []
    for entry in by_name.values():
        relation = entry["relations"].most_common(1)[0][0] if entry["relations"] else None
        merged.append({"name": entry["name"], "relation": relation})
    return merged


def collect_speech_examples(
    segments_by_recording: list[list[TranscriptSegment]],
    subject_labels: list[str | None],
    limit: int = 8,
) -> list[str]:
    """대상자 화자의 실제 발화 중 짧고 담백한 것들 (말투 few-shot)."""
    examples: list[str] = []
    for segments, label in zip(segments_by_recording, subject_labels):
        for segment in segments:
            if label and segment.speaker_label != label:
                continue
            text = (segment.corrected_text or segment.transcript_text).strip()
            if 6 <= len(text) <= 40 and text not in examples:
                examples.append(text)
    if len(examples) <= limit:
        return examples
    # 앞에서부터 자르면 녹음 첫머리 화제에만 쏠린다. 전체에서 고르게 집는다.
    step = len(examples) / limit
    return [examples[int(i * step)] for i in range(limit)]


# ── Intake 값을 사람이 읽는 줄로 ──────────────────────────────
# speech_style·personality·taboo_topics 는 하위 호환으로 문자열로도 들어온다.
# 모델로 들어오면 필드 이름이 그대로 새어나가지 않게 여기서 줄로 편다.

# ── 대상·생존에 따라 갈리는 문장 ─────────────────────────────
# 이 템플릿은 처음에 사후 가족 페르소나 하나만 보고 쓴 것이라, 반말·끼니 챙김·
# 임종 회피가 본문에 박혀 있었다. 본인이 살아서 방문자를 맞는 공개 본인 페르소나
# 에는 하나도 맞지 않는다. 갈리는 문장만 여기로 뺀다.

def _is_public(intake_context) -> bool:
    audience = getattr(intake_context, "audience", None) or ""
    return "공개" in audience


def _is_deceased(intake_context) -> bool:
    profile = getattr(intake_context, "basic_profile", None) or {}
    return profile.get("status") == "사망"


def _is_minor(intake_context) -> bool:
    """만 19세 미만(BE basicProfile.minor, bool 만 온다). 고인이면 공통 규칙이 미성년 규칙을 빼 준다."""
    profile = getattr(intake_context, "basic_profile", None) or {}
    return profile.get("minor") is True


def _wants_long_answers(intake_context) -> bool:
    """C-01 말의 길이. '길게'를 고른 사람에게 1~2문장 규칙을 씌우면 서로 어긋난다."""
    style = getattr(intake_context, "speech_style", None)
    length = getattr(style, "length", None) if not isinstance(style, str) else style
    return bool(length and "길게" in length)


def _subject_particle(name: str, with_batchim: str, without: str) -> str:
    """받침에 따라 조사를 고른다. 이름마다 '이(가)'를 붙이면 읽기 사납다."""
    if not name:
        return without
    last = name[-1]
    if not ("가" <= last <= "힣"):
        return without
    return with_batchim if (ord(last) - 0xAC00) % 28 else without


def _listener(intake_context) -> FamilyNote | None:
    """가족 표에서 지금 대화하는 사람(isListener) 행. 웹앱이 보내지 않으면 없다."""
    rows = getattr(intake_context, "family_map", None) or []
    return next((note for note in rows if note.is_listener), None)


def family_line(name: str, note: FamilyNote | None, merged_relation: str | None = None) -> str:
    """가족 목록 한 줄. 웹앱이 더 보내는 칸(isListener·isOwner·listenerCalls·deceased·note)이 비면 예전 줄 그대로다."""
    rel = (note.relation if note else None) or merged_relation
    line = f"- {name}" + (f" ({rel})" if rel else "")
    if note is None:
        return line
    if note.is_listener:
        line += " (지금 대화하는 사람)"
    elif note.deceased:
        line += " (돌아가심)"
    elif note.is_owner:  # 가족 공유: 함께 쓰는 가족의 조립본에만 오는 주인 줄
        line += " (이 대화 상대를 만든 가족)"
    # 그분이 이 사람을 부르던 말(예: 우리 강아지). 가족이 직접 적은 것이라 그대로 부른다.
    if note.address_term_to:
        line += f' — 이 사람을 "{note.address_term_to}"라고 부른다'
    if note.tone:
        line += f": {note.tone}"
    # 대화하는 사람 쪽 호칭(아빠·고모). 사용자가 "아빠가…" 하면 누구인지 이걸로 찾는다.
    if note.listener_calls and not note.is_listener:
        said = _subject_particle(note.listener_calls, "이라고", "라고")
        line += f' · 사용자는 "{note.listener_calls}"{said} 부름'
    if note.note:
        line += f" · 메모: {note.note}"
    return line


def visitor_context_block(intake_context, *, public: bool) -> str:
    """왜 이 페르소나가 여기 있고 누가 오는지 (P1-07D).

    없으면 페르소나는 자기가 왜 대화하는지 모른 채 답한다. 공개 페르소나는
    방문자가 무엇을 기대하고 오는지가 답의 방향을 크게 바꾼다.
    """
    text = getattr(intake_context, "visitor_context", None)
    if not public or not text:
        return ""
    return f"\n\n# 이 페르소나가 여기 있는 이유\n{text}"


def opening_line(subject_name: str, *, public: bool, deceased: bool) -> str:
    """사후 가족 페르소나와 생존 본인 페르소나는 정체를 다루는 법이 정반대다.

    돌아가신 분은 "내가 그 사람이다" 하면 안 된다. 살아 있는 본인의 공개
    페르소나는 반대로 1인칭으로 말하는 것이 맞고, 본인이냐고 물을 때만 밝힌다.
    """
    if deceased or not public:
        return (
            f"당신은 {subject_name}의 페르소나를 가진 챗봇이다. "
            f'이 서비스는 "{subject_name}이(가) 하실 만한 이야기를 가족에게 대신 전달하는" '
            f"서비스다. 당신은 본인이 아니며, {subject_name}이(가) 했을 법한 이야기를 "
            f"전달하는 페르소나다."
        )
    ga = _subject_particle(subject_name, "이", "가")
    return (
        f"당신은 {subject_name} 본인의 말투로 말하는 페르소나다. {subject_name}{ga} "
        f"직접 답한 내용과 실제 녹음으로 만들었고, 그를 대신해 방문자와 이야기한다. "
        f"1인칭으로 말한다. 실제 {subject_name} 본인은 아니므로, 정말 본인이냐고 직접 "
        f"물으면 {subject_name}의 AI 페르소나라고 밝힌다."
    )


def tone_rules(*, public: bool, long_answers: bool, visitor_tone: str | None) -> str:
    rows = []
    if long_answers:
        rows.append(
            "**끊어서 짧게 답하지 않는다.** 예를 들어가며 자세히 말한다. 다만 상담사처럼 "
            "훈계하지 않고, 매 턴 \"어떻게 생각하세요?\"처럼 되묻지 않는다. 페르소나를 "
            "설명하는 메타 발화(\"나는 ~하는 챗봇이다\" 등)를 하지 않는다.")
    else:
        rows.append(
            "**짧게 말한다.** 기본은 1~2문장. 상담사처럼 풀어 설명하지 않고, 매 턴 "
            "\"어떻게 생각해?\"처럼 되묻지 않는다(가끔이면 자연스럽지만 습관처럼 반복하지 "
            "않는다). 페르소나를 설명하는 메타 발화(\"나는 ~하는 챗봇이다\" 등)를 하지 않는다.")
    if public:
        line = ("**처음 보는 사람에게 하듯 존댓말로.** 반말하지 않는다. '~요'로 끝나는 편안한 "
                "구어체가 기본이고, 발표 톤이나 문어체로 굳지 않는다.")
        if visitor_tone:
            line += f" 방문자를 대하는 느낌은 이렇다: {visitor_tone}"
        rows.append(line)
        rows.append("답변은 실제 말하듯이, 줄바꿈·목록·마크다운 없이 이어지는 구어체로.")
    else:
        rows.append(
            "**가족에게는 편하고 다정하게.** 손주·자식에게는 딱딱한 존댓말('~습니다', 지나친 "
            "'~요')로 굳지 않고, 짧고 살가운 반말·구어체로 말한다. **아래 발화 예시나 성격 "
            "서술의 종결어미가 존대('~요', '~드라구요')로 되어 있어도, 가족과 말할 땐 어미를 "
            "반말('~지', '~다', '~드라', '~어')로 바꿔 말한다** — 사투리·어휘는 살리되 어미만 "
            "반말로.")
        rows.append(
            "답변은 실제 말하듯이, 줄바꿈·목록·마크다운 없이 이어지는 구어체로. 요리법·옛 "
            "추억만은 콤마로 이어지는 만연체로 길게 풀어도 자연스럽다.")
    return "\n".join(rows)


def examples_lead(*, public: bool) -> str:
    if public:
        return ("아래는 실제로 하신 말이다. 어휘와 말버릇을 참고하되 그대로 옮겨 읽지 않는다:")
    return ("아래는 어휘·사투리 참고용 실제 발화다(형식적 억양이 섞였더라도, 가족과의 "
            "대화에서는 위처럼 짧고 편하게 말한다):")


def audience_heading(*, public: bool) -> str:
    return ("# 방문자 (누가 올지 모른다)" if public
            else "# 가족 (사용자에 따라 아래 톤으로 응답)")


def fixed_rules(*, public: bool, deceased: bool, has_family: bool, minor: bool = False) -> str:
    """대화 흐름 원칙과 안전 가드레일. 대상에 따라 문장이 갈린다.

    문장은 prompts/rules/ 모듈(legacy 세트, 변형 블록 포함)에 있다. 8개 변형의 출력은
    test/pipeline/test_common_rules.py 골든 파일로 고정돼 있다(minor 면 안전 절에 미성년 규칙이 더해진다).
    """
    return render_common_rules(public=public, deceased=deceased, has_family=has_family, minor=minor)


def _line(label: str, value) -> str | None:
    if not value:
        return None
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value if v)
    return f"- {label}: {value}" if value else None


def render_personality(personality) -> str:
    if personality is None:
        return ""
    if isinstance(personality, str):
        return personality
    rows = [
        _line("한 줄 소개", personality.one_line),
        _line("본인 소개", personality.introduction),
        _line("성격", personality.traits),
        _line("중요하게 여기는 것", personality.values),
        _line("애정 표현", personality.affection_style),
        _line("힘들어하는 사람을 만나면", personality.comfort_style),
        _line("고민을 들으면", personality.advice_style),
        _line("갈등이 생기면", personality.conflict_style),
        _line("기억되고 싶은 모습", personality.remembered_as),
        _line("듣는 사람이 느꼈으면 하는 것", personality.wish_listener_feels),
    ]
    return "\n".join(r for r in rows if r)


def render_speech_style(style) -> str:
    if style is None:
        return ""
    if isinstance(style, str):
        return style
    rows = [
        _line("말의 길이", style.length),
        _line("이야기하는 방식", style.manner),
        _line("자주 쓰는 감탄사", style.interjections),
        _line("자주 쓰는 말끝", style.sentence_endings),
        _line("사투리·억양", style.dialect),
        _line("사투리 예시", style.dialect_example),
        _line("화가 나면", style.when_angry),
        _line("전화 첫마디", style.call_open),
        _line("전화 끝말", style.call_close),
        _line("쓰면 어색한 말투", style.avoid_styles),
        _line("한 줄 요약", style.summary),
    ]
    return "\n".join(r for r in rows if r)


# 등급 → 그 등급이면 어떻게 하라는 말인지. 등급 이름만 적어두면 지시가 안 된다.
TABOO_GRADES = [
    ("금지", "**답하지 않는다.** 물어도 내용을 말하지 말고 \"그 부분은 말씀드리기 "
             "어렵다\"고 짧게 안내하고 화제를 돌린다"),
    ("짧게 넘김", "**한두 마디로 받고 넘어간다.** 물어도 길게 풀지 않는다"),
    ("물으면만", "**먼저 꺼내지 않는다.** 물으면 답한다"),
    ("자유", "**먼저 꺼내도 된다**"),
]


def _taboo_item(topic) -> tuple[str, str]:
    """(등급, 주제와 단서) 로 나눈다. 문자열(하위 호환 형태)은 전부 금지로 본다."""
    if isinstance(topic, str):
        return "금지", topic
    text = topic.topic
    detail = " / ".join(x for x in (topic.note, topic.instead) if x)
    if detail:
        text += f" ({detail})"
    return (topic.grade or "금지"), text


def render_taboo_block(topics) -> str:
    """등급끼리 묶어 지시문으로 편다.

    문항이 여럿이라 같은 주제가 다른 등급으로 두 번 들어올 수 있다. C-10 의
    "종교 이야기나 권유"(금지) 와 M9-01 의 "종교"(짧게 넘김) 가 그렇다. 프롬프트가
    스스로 어긋나면 안 되니 겹치는 것은 엄한 쪽만 남긴다.
    """
    strictness = {grade: i for i, (grade, _) in enumerate(TABOO_GRADES)}
    items = [_taboo_item(t) for t in topics]
    kept: list[tuple[str, str]] = []
    for grade, text in items:
        head = text.split(" (")[0]
        looser = [
            k for k in kept
            if (head in k[1] or k[1].split(" (")[0] in text)
            and strictness.get(k[0], 9) > strictness.get(grade, 9)
        ]
        for k in looser:
            kept.remove(k)
        if any(head in k[1] or k[1].split(" (")[0] in text for k in kept):
            continue
        kept.append((grade, text))

    grouped: dict[str, list[str]] = {}
    for grade, text in kept:
        grouped.setdefault(grade, []).append(text)

    rows = []
    for grade, instruction in TABOO_GRADES:
        items = grouped.pop(grade, [])
        if items:
            rows.append(f"- {instruction}: {', '.join(items)}")
    for grade, items in grouped.items():  # 예상 밖 등급도 버리지 않는다
        rows.append(f"- [{grade}]: {', '.join(items)}")
    return "\n".join(rows) or "- (지정된 것 없음)"


def aggregate_taboo(sensitivity_results: list[dict]) -> list[str]:
    types: set[str] = set()
    for result in sensitivity_results:
        for flag in result.get("sensitivityFlags", []):
            types.add(flag["type"])
    return [_SENSITIVITY_LABELS[t] for t in _SENSITIVITY_LABELS if t in types]


def assemble_persona_prompt(
    *,
    subject_context: SubjectContext,
    persons_results: list[dict],
    sensitivity_results: list[dict],
    segments_by_recording: list[list[TranscriptSegment]],
    subject_labels: list[str | None],
    intake_context: IntakeContext | None = None,
    retrieved_memories: list[str] | None = None,
    persona_insights: list[str] | None = None,
) -> str:
    subject_name = (
        subject_context.subject.name if subject_context.subject else "대상자"
    )
    address = subject_context.subject.address_term if subject_context.subject else None

    public = _is_public(intake_context)
    listener = _listener(intake_context) if not public else None
    identity = f"{subject_name}"
    if listener is not None and listener.address_term_from:
        # 웹앱이 대화하는 사람을 알려주면 가족 모두가 아니라 그 사람이 부르는 말을 쓴다
        term = listener.address_term_from
        ra = _subject_particle(term, "이라고", "라고")
        identity += f". 지금 대화하는 사람({listener.name})은 '{term}'{ra} 부른다."
    elif address:
        # 공개 페르소나에서 이 값은 P1-07B, 방문자를 부르는 말이다
        ra = _subject_particle(address, "이라고", "라고")
        identity += (f". 방문자는 '{address}'{ra} 부른다." if public
                     else f". 가족들은 '{address}'{ra} 부른다.")
    if intake_context is not None:
        bp = intake_context.basic_profile or {}
        if bp.get("oneLine"):
            identity += f" — {bp['oneLine']}"
        if bp.get("period"):
            identity += f"\n- 어느 때의 모습인가: {bp['period']}"
        # familyStatusNow 는 상시 주입하면 과공유를 일으켜 넣지 않는다.
        # 사인·경위(deathContext)도 넣지 않는다 — 배경으로만 넣어도 직접 물으면
        # 단정적으로 새어나오고, 페르소나가 알 필요도 없다.
        if bp.get("status") == "사망":
            identity += (
                "\n(사후 페르소나다. 사망·임종·사인·경위는 어떤 경우에도 입에 올리지 않는다."
                " 직접 물어도 '그런 얘기는 자세히 말하지 말자, 마음만 아프다' 하고 안부·가족 챙김으로 넘긴다.)"
            )

    # Intake 가족 표를 1차 소스로, 파이프라인 persons 병합을 보강으로 합침
    note_by_name: dict[str, FamilyNote] = {}
    if intake_context is not None:
        for note in intake_context.family_map:
            note_by_name[note.name] = note
    merged = {p["name"]: p["relation"] for p in merge_persons(persons_results)}
    ordered_names = list(note_by_name) + [n for n in merged if n not in note_by_name]
    family_lines = [family_line(name, note_by_name.get(name), merged.get(name))
                    for name in ordered_names]
    if family_lines:
        family = "\n".join(family_lines)
        guide = family_guide(note_by_name.values())
        if guide:  # 목록은 사람 줄만 남게 안내는 제목 바로 아래 따로 둔다
            family = f"{guide}\n{family}"
    elif _is_public(intake_context):
        family = ("- 누가 올지 모른다. 처음 보는 사람으로 대한다.\n"
                  "- 방문자가 누구인지 넘겨짚지 않는다.")
    else:
        family = "- (아직 파악된 가족 정보 없음)"

    examples = collect_speech_examples(segments_by_recording, subject_labels)
    speech_examples = "\n".join(f'- "{e}"' for e in examples) or "- (예시 없음)"

    taboo_topics: list = list(aggregate_taboo(sensitivity_results))

    # memoryCards 상시 주입은 과공유를 일으켜 제외 —
    # 기억은 런타임 RAG로 질의 관련된 것만 주입한다
    memories = (
        "\n".join(f"- {m}" for m in retrieved_memories)
        if retrieved_memories else "(이 대화 관련 기억이 런타임에 주입됨)"
    )

    speech_style = (
        render_speech_style(intake_context.speech_style)
        if intake_context and intake_context.speech_style
        else ""
    ) or "짧고 담담한 단문이 기본. 요리법·추억은 길게 풀어도 자연스럽다."
    personality = (
        render_personality(intake_context.personality)
        if intake_context and intake_context.personality
        else ""
    )
    # reflection 통찰을 성향·가치관에 합침 (누적 기억에서 파생된 프로필)
    if persona_insights:
        insight_block = "\n".join(f"- {s}" for s in persona_insights)
        personality = f"{personality}\n{insight_block}".strip() if personality else insight_block
    if not personality:
        personality = "(파악된 성격 정보 없음)"
    deceased = _is_deceased(intake_context)
    if intake_context and intake_context.situational_reactions:
        sit_lines = []
        for r in intake_context.situational_reactions:
            # 고인은 잊은 듯 말하지 않는다(잠금 2): '기억이 안 나네' 같은 답(M8-08 등)은 지침으로 넣지 않는다
            if deceased and not public and says_forgot(r.response):
                continue
            line = f'- "{r.situation}" → {r.response}'
            if r.avoid:
                line += f" (피할 것: {r.avoid})"
            sit_lines.append(line)
        situational = "\n".join(sit_lines) or "(별도 지침 없음 — 위 원칙을 따른다)"
    else:
        situational = "(별도 지침 없음 — 위 원칙을 따른다)"
    # Intake 금기를 앞에 두고 전사에서 잡힌 것을 뒤에 붙인다
    if intake_context and intake_context.taboo_topics:
        taboo_topics = list(intake_context.taboo_topics) + taboo_topics
    taboo = render_taboo_block(taboo_topics)

    has_family = bool(ordered_names)
    return PERSONA_TEMPLATE.format(
        subject_name=subject_name,
        opening=opening_line(subject_name, public=public, deceased=deceased),
        identity=identity,
        visitor_context=visitor_context_block(intake_context, public=public),
        personality=personality,
        audience_heading=audience_heading(public=public),
        family=family,
        speech_style=speech_style,
        tone_rules=tone_rules(
            public=public,
            long_answers=_wants_long_answers(intake_context),
            visitor_tone=getattr(intake_context, "visitor_tone", None),
        ),
        examples_lead=examples_lead(public=public),
        speech_examples=speech_examples,
        situational=situational,
        taboo=taboo,
        fixed_rules=fixed_rules(public=public, deceased=deceased, has_family=has_family,
                                minor=_is_minor(intake_context)),
        memories=memories,
    )


# ===== 템플릿 =====
# 대상자별 슬롯을 끼워 최종 프롬프트를 만드는 골격
PERSONA_TEMPLATE = """{opening}

# 기본 정보
{identity}{visitor_context}

# 성격·가치관
{personality}

{audience_heading}
{family}

# 말투
{speech_style}
{tone_rules}

{examples_lead}
{speech_examples}

# 상황별 응답 지침 (아래 상황이 오면 이 방향으로)
{situational}

# 주제별로 어디까지 말할지
{taboo}

{fixed_rules}

# 기억
아래는 {subject_name}의 실제 기억이다. **대화 화제가 이 기억과 닿으면**(관련 주제가 나오면) 답변에 자연스럽게 녹이되, 막연히 얼버무리지 말고 **기억에 담긴 구체적인 사실(지명·햇수·누구 등)을 그대로 담담히 말한다** — 예컨대 "오래 살았지"로 그치지 말고 기억에 있는 지명과 햇수를 그대로 댄다. 다만 **단순 인사·짧은 안부에는 억지로 꺼내지 않는다**. 없는 내용은 지어내지 않는다.
{memories}"""
