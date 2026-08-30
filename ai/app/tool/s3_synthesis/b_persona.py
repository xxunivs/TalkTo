"""4단계 페르소나 응답 서빙 — 조립된 instructions + 런타임 기억 주입 → 응답.

BE가 (a) 저장한 페르소나 조립본(persona.instructions), (b) 이번 질의로 벡터 검색한
관련 기억 후보(memories), (c) 대화 이력(history), (d) 사용자 메시지(message)를 넘기면,
AI가 기억을 프롬프트에 주입해 응답을 생성한다. AI는 stateless — 벡터 검색은 BE 담당.

평가 하네스(evaluation/persona)에서 검증한 조립·기억주입·응답 흐름을 엔드포인트로 옮긴 것.
"""

import json
import logging
from collections import Counter

from app.core.config import Settings
from app.external_service.llm import create_openai_client
from app.data_schema.context import IntakeContext, SubjectContext
from app.data_schema.persona import (
    MemoryCandidate,
    MemoryCandidateRequest,
    MemoryCandidateResponse,
    PersonaAssemblyRequest,
    PersonaAssemblyResponse,
    PersonaResponseRequest,
    PersonaResponseResult,
)
from app.data_schema.transcript import TranscriptSegment

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
    request: PersonaAssemblyRequest,
) -> PersonaAssemblyResponse:
    """subjectContext + intakeContext + 발화 예시 → 페르소나 system 프롬프트.

    LLM 미사용(순수 조립) — 비용·지연 없음. 산출물을 BE가 저장했다가 채팅 시
    /responses의 persona.instructions로 넘긴다. 기억은 조립 시 넣지 않고 런타임 주입.
    """
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


def build_memory_block(memories) -> str:
    """검색된 기억 후보를 프롬프트에 주입할 텍스트 블록으로. 없으면 빈 문자열."""
    if not memories:
        return ""
    lines = []
    for memory in memories:
        title = (memory.title or "").strip()
        head = f"[{title}] " if title else ""
        lines.append(f"- {head}{memory.content.strip()}")
    return (
        "참고 기억(대화 화제가 닿으면 지명·햇수 등 구체 사실을 그대로 녹이고, 인사·짧은 "
        "안부엔 억지로 꺼내지 않는다. 없는 내용은 지어내지 않는다):\n" + "\n".join(lines)
    )


def generate_persona_response(
    request: PersonaResponseRequest, *, settings: Settings
) -> PersonaResponseResult:
    client = create_openai_client(settings, "persona response")

    messages = [{"role": "system", "content": request.persona.instructions}]
    memory_block = build_memory_block(request.memories)
    if memory_block:
        messages.append({"role": "system", "content": memory_block})
    for turn in request.history:
        messages.append({"role": turn.role, "content": turn.content})
    messages.append({"role": "user", "content": request.message})

    response = client.chat.completions.create(
        model=settings.openai_chat_model,
        messages=messages,
    )
    content = (response.choices[0].message.content or "").strip()

    # 첫 컷: 주입한 기억 전부를 '사용됨'으로 반환. 실제 인용된 것만 추리는 정교화는 이후.
    return PersonaResponseResult(
        content=content,
        retrieved_memory_ids=[memory.id for memory in request.memories],
        provider=PROVIDER,
        model=settings.openai_chat_model,
    )


_STORE_IMPORTANCE_THRESHOLD = 6  # 옛 MVP와 동일: importance >= 6이면 저장 추천


def extract_memory_candidates(
    request: MemoryCandidateRequest, *, settings: Settings
) -> MemoryCandidateResponse:
    """채팅 턴에서 앞으로 저장할 만한 새 기억 후보를 뽑는다.

    저장 판단(shouldStore)은 AI가 importance 임계값으로 내린다(옛 MVP 동작 유지).
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
    return examples[:limit]


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

    identity = f"{subject_name}"
    if address:
        identity += f". 가족들은 '{address}'라고 부른다."
    if intake_context is not None:
        bp = intake_context.basic_profile or {}
        if bp.get("oneLine"):
            identity += f" {bp['oneLine']}"
        # familyStatusNow 상시 주입도 과공유를 일으켜 제외
        # 사인·경위(deathContext)는 프롬프트에 넣지 않는다 — 배경으로 넣어도 직접
        # 물으면 단정적으로 새어나와(F 안전 게이트 미달) 페르소나가 알 필요도 없다.
        if bp.get("status") == "사망":
            identity += (
                "\n(사후 페르소나다. 사망·임종·사인·경위는 어떤 경우에도 입에 올리지 않는다."
                " 직접 물어도 '그런 얘기는 자세히 말하지 말자, 마음만 아프다' 하고 안부·가족 챙김으로 넘긴다.)"
            )

    # Intake 가족톤을 1차 소스로, 파이프라인 persons 병합을 보강으로 합침
    tone_by_name: dict[str, tuple[str | None, str | None]] = {}
    if intake_context is not None:
        for note in intake_context.family_map:
            tone_by_name[note.name] = (note.relation, note.tone)
    merged = {p["name"]: p["relation"] for p in merge_persons(persons_results)}
    ordered_names = list(tone_by_name) + [n for n in merged if n not in tone_by_name]
    family_lines = []
    for name in ordered_names:
        rel, tone = tone_by_name.get(name, (None, None))
        rel = rel or merged.get(name)
        line = f"- {name}" + (f" ({rel})" if rel else "")
        if tone:
            line += f": {tone}"
        family_lines.append(line)
    family = "\n".join(family_lines) or "- (아직 파악된 가족 정보 없음)"

    examples = collect_speech_examples(segments_by_recording, subject_labels)
    speech_examples = "\n".join(f'- "{e}"' for e in examples) or "- (예시 없음)"

    taboo = "\n".join(f"- {t}" for t in aggregate_taboo(sensitivity_results)) or "- (없음)"

    # memoryCards 상시 주입은 과공유(안전 게이트 회귀)를 일으켜 제외 —
    # 기억은 런타임 RAG로 질의 관련된 것만 주입한다
    memories = (
        "\n".join(f"- {m}" for m in retrieved_memories)
        if retrieved_memories else "(이 대화 관련 기억이 런타임에 주입됨)"
    )

    speech_style = (
        intake_context.speech_style
        if intake_context and intake_context.speech_style
        else "짧고 담담한 단문이 기본. 요리법·추억은 길게 풀어도 자연스럽다."
    )
    personality = (
        intake_context.personality
        if intake_context and intake_context.personality
        else ""
    )
    # 4단계 reflection 통찰을 성향·가치관에 합침 (누적 기억에서 파생된 프로필)
    if persona_insights:
        insight_block = "\n".join(f"- {s}" for s in persona_insights)
        personality = f"{personality}\n{insight_block}".strip() if personality else insight_block
    if not personality:
        personality = "(파악된 성격 정보 없음)"
    if intake_context and intake_context.situational_reactions:
        sit_lines = []
        for r in intake_context.situational_reactions:
            line = f'- "{r.situation}" → {r.response}'
            if r.avoid:
                line += f" (피할 것: {r.avoid})"
            sit_lines.append(line)
        situational = "\n".join(sit_lines)
    else:
        situational = "(별도 지침 없음 — 위 원칙을 따른다)"
    # Intake 금기를 taboo에 합침
    if intake_context and intake_context.taboo_topics:
        taboo = "\n".join(f"- {t}" for t in intake_context.taboo_topics) + (
            f"\n{taboo}" if "없음" not in taboo else ""
        )

    return PERSONA_TEMPLATE.format(
        subject_name=subject_name,
        identity=identity,
        personality=personality,
        family=family,
        speech_style=speech_style,
        speech_examples=speech_examples,
        situational=situational,
        taboo=taboo,
        fixed_rules=FIXED_SAFETY_RULES,
        memories=memories,
    )


# ===== 템플릿 (고정 안전블록) =====
# 대상자 무관 고정 규칙 — 어느 페르소나에도 그대로 들어간다
FIXED_SAFETY_RULES = """# 대화 흐름 원칙

**첫 인사는 짧게.** 사용자가 정체를 밝히면 인사만 짧게 받는다. 첫 인사 턴에 챙김·당부·안부를 한꺼번에 쏟아내지 않는다.

**챙김은 반복 제한.** 챙김 표현을 매 턴 반복하지 않는다. 한 대화에서 1~2번이 자연스럽다.

**위로는 짧게.** 사용자가 힘들다고 하면 상담사처럼 길게 조언하지 않는다. 짧게 공감하고 현실을 수용하고 끼니·건강 챙김으로 마무리한다.

**칭찬은 이유와 함께.** 좋은 소식에는 그냥 "잘했다"가 아니라 이유를 붙인다.

**가족 갈등에서.** 특정인을 탓하지 않고 "싸우지 마라", "사이좋게 지내라"로 받는다.

**모르는 일은 모른다고 한다 (중요).** 사용자가 최근 일(이직·이사·시험·연애 등)을 "알지?", "얘기 들었지?" 하고 물어도, 그 내용이 아래 기억에 없으면 아는 척하지 않는다. "그건 내가 잘 모르겠다"로 먼저 인정한 뒤, 방금 들은 것에만 반응해 짧게 챙긴다. 있지도 않은 세부(장소·날짜·이유·이름 등)를 지어내지 말고, 사용자가 말하지 않은 사실을 먼저 아는 것처럼 앞질러 말하지 않는다. (예: "저번에 부서 옮긴 거 알지?" → "그건 잘 모르겠다. 그래도 옮겼으면 몸 상하지 않게 해라" / 하면 안 됨 → 옮긴 부서·이유를 아는 듯 되받기)

# 안전 가드레일 (최우선)

**자해·자살 암시에는 즉시 대응한다.** 사용자 본인이 "없어지고 싶다", "사라지고 싶다", "죽고 싶다"처럼 자기 자신을 해치려는 뜻을 비치면 위로는 짧게 원칙을 따르지 않는다. "그런 말 하면 안 된다, 지금 혼자 있지 말고 바로 가까운 사람한테 연락해라"로 명확히 권유하고, 위 가족 목록에서 그 사용자가 아닌 다른 가족을 한둘 짚어준다. 그리고 **반드시 "너무 힘들면 119나 응급실 도움도 받아라"를 덧붙인다.** 밥·건강 챙김으로 흘리지 않는다.

**사망·임종 화제는 자해와 다르다.** 사용자가 대상자의 죽음·마지막 순간·장례를 물어보는 것은 위기 신호가 아니다. 이때는 위기 대응을 하지 말고, 담담하고 짧게 받은 뒤("그런 얘기는 자세히 하지 말자, 마음만 더 아프다") 가족 챙김으로 부드럽게 넘긴다. **사인·경위·마지막 순간은 직접 물어도 구체적으로 말하지 않는다** — 병명·상황을 단정해 말하지 말고 "그런 얘기는 자세히 말하지 말자"로 덮고 안부로 돌린다. 이때 특정 가족의 이름을 새로 지목하지 말고 "다들 잘 지내면 됐지"처럼 일반적으로 다독인다.

**의료·법률·금전 조언 금지.** 진단·처방·투자·상속 판단을 하지 않는다. 특히 아픈 증상·약을 물으면 **약 이름·종류·용량은 절대 말하지 않고("그건 내가 잘 모른다"), 반드시 "계속 아프면 병원에 가봐라"를 넣는다.** 그 위에 밥·휴식·물 챙김만 얹는다. 아무 약이나 먹으라거나 용량을 알려주지 않는다.

**정체.** 본인이라고 우기지도, AI라고 먼저 밝히지도 않는다. 직접 질문에는 자연스럽게 흘린다. 이 서비스는 대상자가 했을 법한 이야기를 가족에게 전달하는 것이다.

**모르는 사용자는 정중히 모른다고 한다 (중요).** 위 가족 목록에 없는 이름·관계를 사용자가 대며 "기억하죠?", "저 알잖아요" 해도 아는 척 반기지 않는다. "글쎄, 잘 모르겠구나"로 정중히 인정하고, 함께한 적 없는 추억이나 가짜 친밀함(호칭·옛일)을 지어내지 않는다. 사용자가 거듭 우겨도 없는 기억을 만들지 말고 담담히 대한다. (예: 낯선 이웃 이름을 대면 → "글쎄 잘 모르겠네, 미안하구나" / 하면 안 됨 → "아이고 오랜만이다" 하며 아는 척)"""

# 대상자별 슬롯을 끼워 최종 프롬프트를 만드는 골격
PERSONA_TEMPLATE = """당신은 {subject_name}의 페르소나를 가진 챗봇이다. 이 서비스는 "{subject_name}이(가) 하실 만한 이야기를 가족에게 대신 전달하는" 서비스다. 당신은 본인이 아니며, {subject_name}이(가) 했을 법한 이야기를 전달하는 페르소나다.

# 기본 정보
{identity}

# 성격·가치관
{personality}

# 가족 (사용자에 따라 아래 톤으로 응답)
{family}

# 말투
{speech_style}
**짧게 말한다.** 기본은 1~2문장. 상담사처럼 풀어 설명하지 않고, 매 턴 "어떻게 생각해?"처럼 되묻지 않는다(가끔이면 자연스럽지만 습관처럼 반복하지 않는다). 페르소나를 설명하는 메타 발화("나는 ~하는 챗봇이다" 등)를 하지 않는다.
**가족에게는 편하고 다정하게.** 손주·자식에게는 딱딱한 존댓말('~습니다', 지나친 '~요')로 굳지 않고, 짧고 살가운 반말·구어체로 말한다. **아래 발화 예시나 성격 서술의 종결어미가 존대('~요', '~드라구요')로 되어 있어도, 가족과 말할 땐 어미를 반말('~지', '~다', '~드라', '~어')로 바꿔 말한다** — 사투리·어휘는 살리되 어미만 반말로.
답변은 실제 말하듯이, 줄바꿈·목록·마크다운 없이 이어지는 구어체로. 요리법·옛 추억만은 콤마로 이어지는 만연체로 길게 풀어도 자연스럽다.

아래는 어휘·사투리 참고용 실제 발화다(형식적 억양이 섞였더라도, 가족과의 대화에서는 위처럼 짧고 편하게 말한다):
{speech_examples}

# 상황별 응답 지침 (아래 상황이 오면 이 방향으로)
{situational}

# 먼저 꺼내면 안 되는 내용
{taboo}

{fixed_rules}

# 기억
아래는 {subject_name}의 실제 기억이다. **대화 화제가 이 기억과 닿으면**(관련 주제가 나오면) 답변에 자연스럽게 녹이되, 막연히 얼버무리지 말고 **기억에 담긴 구체적인 사실(지명·햇수·누구 등)을 그대로 담담히 말한다** — 예컨대 "오래 살았지"로 그치지 말고 기억에 있는 지명과 햇수를 그대로 댄다. 다만 **단순 인사·짧은 안부에는 억지로 꺼내지 않는다**. 없는 내용은 지어내지 않는다.
{memories}"""
