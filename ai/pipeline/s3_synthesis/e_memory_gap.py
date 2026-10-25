"""기억 빈틈 표시와 가족이 고쳐 준 말투 — 대답 호출에 붙는 두 절, 그리고 '더 알려 주실 기억' 질문 만들기.

1) 기억 빈틈(BE 요청 flagGaps): 대답을 만드는 같은 호출에서 그분 기억에 없는 것을 물었으면 대답 끝에
   [기억없음:주제] 를 하나 붙이게 한다(LLM 을 더 부르지 않는다). 감정 표시처럼 생성 바로 뒤, 음성 표시·가드·
   대화 기록·기억보다 먼저 떼어 낸다(split_gap). 대답 글·말투는 바뀌지 않는다.
   그분의 죽음·장례·임종, 위기(죽고 싶다·자해), 건강·병·약, 종교 화제면 빈틈으로 치지 않는다(지침 + turn_gap).
2) 말투 고침(BE 요청 styleExamples): 가족이 '그분이라면 이렇게 말했을 것'이라고 고쳐 준 쌍을 짧은 예시 절로 넣는다.
   말투 예시일 뿐이라 그 속의 이름·날짜·일은 사실로 쓰지 않고, 말 높이·위 규칙·뒤의 말투 덧붙임은 그대로 따르게 한다.
두 절 모두 기존 경로(generate_persona_response)와 중간계층(legacy·structured 조립기)이 같은 자리(음성 지침 바로
뒤, 참고 기억 앞)에 넣는다. 요청에 없으면 아무것도 넣지 않아 예전과 같은 메시지다.

3) POST /v1/persona/gap-question: 대화가 끝난 뒤 BE 가 후보 하나를 보내면 가족이 답할 수 있는 질문 하나를 만들거나
   (new), 열린 질문과 같은 것이면 그 id 로 합치거나(merge), 물을 것이 아니면 건너뛴다(skip). 대답 경로 밖의 호출이다.
   위 화제의 후보는 모델을 부르지 않고 건너뛰고, 모델이 만든 질문이 그런 화제여도 버린다(gap_blocked).
   함께 쓰는 가족의 대화에서 온 후보(fromMember)는 그 가족의 말을 옮기거나 이름·장소를 넣지 않고 주제만 일반적으로 묻는다.
"""

from __future__ import annotations

import json
import logging
import re

from infra.external_api.openai_client import chat_client
from infra.schema.persona import GapQuestionRequest, GapQuestionResponse, StyleExample
from infra.setting import Settings
from orchestrator.guard import CRISIS_LINE
from orchestrator.precheck import is_safety_turn

logger = logging.getLogger(__name__)

PROVIDER = "openai"

GAP_INSTRUCTION = """[기억 빈틈 표시]
가족이 그분의 지난 일·좋아하던 것·겪은 일·가족 이야기를 물었는데 위 지시문과 참고 기억 어디에도 그 내용이 없으면, 대답 맨 끝에 [기억없음:주제] 를 딱 하나 붙인다. 주제는 물은 것을 2~10자 낱말로 쓴다(예: [기억없음:즐겨 부른 노래]).
- 인사·안부·근황·마음을 나누는 말, 기억에 있는 것을 물은 말에는 붙이지 않는다.
- 그분의 죽음·장례·임종·마지막 순간을 묻는 말, 죽고 싶다·자해 같은 힘든 마음을 털어놓는 말, 건강·병·약, 종교·신앙 이야기에는 붙이지 않는다.
- 대답은 위 지시대로 평소처럼 한다. 이 표시 때문에 모른다고 말하거나 대답 글·말투를 바꾸지 않는다.
이 표시는 상대 화면에 보이지 않고 목소리로도 읽히지 않는다."""

STYLE_HEAD = "# 말투를 이렇게 고쳐 달라고 했어요"
STYLE_LEAD = (
    "아래는 말투 예시일 뿐이다. 가족이 앞선 대답을 '그분이라면 이렇게 말했을 것'이라고 고쳐 주었다. "
    "고친 말의 말버릇·길이·표현만 따른다. 예시에 나온 이름·날짜·장소·일 같은 내용은 사실로 가져다 쓰지 않는다 — "
    "사실은 위 지시문·참고 기억·지금 대화에서만 쓴다. 말 높이(존댓말·반말)와 위 규칙은 그대로 지킨다"
    "(고친 말이 반말이어도 위에서 존댓말을 쓰라고 했으면 존댓말로, 그 반대도 같다). 고친 말을 그대로 되풀이하지 않는다."
)

# ---------------------------------------------------------------- 묻지 않는 화제(결정적 거르기)
# 가족에게 빈틈 질문을 만들지 않는 말: 그분의 죽음·장례·임종·마지막 순간, 위기(죽고 싶다·자해 — precheck.SAFETY 와
# 답 속 위기 상담 번호 guard.CRISIS_LINE), 건강·병·약, 종교·신앙. 모델 지침(GAP_INSTRUCTION·GAP_QUESTION_PROMPT)이
# 먼저 막고, 모델이 어겨도 여기서 한 번 더 막는다(대답 경로의 memoryGap, /v1/persona/gap-question 앞뒤 모두).
# 일부러 넓게 잡는다 — 질문 하나를 덜 만드는 편이 이런 화제를 가족에게 되묻는 것보다 낫다.
_NO_GAP_TOPIC = re.compile(
    # 죽음·장례·임종(precheck.SAFETY 의 돌아가셨·임종·장례·마지막 순간에 더해)
    r"죽음|죽었|죽기\s?전|사망|별세|임종|장례|빈소|발인|입관|화장터|상여(?!금)|유언|산소|묘소|묘지|성묘|납골|추모|제사"
    r"|마지막\s?(?:순간|모습|길|숨|병상|며칠|날들|가시는)|돌아가시기\s?전|숨\s?(?:을\s?)?거두"
    # 건강·병·약 (아파트·아프리카는 아니다)
    r"|건강|아프(?!리카)|아팠|아파(?!트)|편찮|병원|병명|병환|지병|투병|앓|입원|수술|진단|치료|치매|중풍|뇌졸중|당뇨|혈압"
    r"|항암|암\s?(?:으로|이었|이셨|에\s?걸|투병|진단|수술|센터)|간병|요양|응급|중환자|의사\s?선생"
    # 약 — '드시던 약'·'약 드셨'. 약속·약과·약식·약밥(먹거리)은 아니다
    r"|약\s?(?:을\s?)?(?:드시|드셨|먹|복용)|(?:드시|드셨|먹|복용하)\S{0,2}\s?약(?![속과간식밥수])|약국|약봉지"
    # 종교·신앙 (얘기도·경기도·계절에·미사일·불경기·믿음직은 아니다)
    r"|종교|신앙|믿음(?!직)|교회|성당|사찰|(?<![가-힣])절에\s?(?:다니|가)|예배|(?<![가-힣])미사(?![일여])"
    r"|(?<![가-힣])기도|기도(?:하시|하셨|드리|드렸|문|원)|하나님|하느님|부처님|불교|기독교|천주교|개신교"
    r"|찬송|성경|불경(?!기)|교인|신자|목사|신부님|수녀|스님|세례|염불|천국|극락"
)


def gap_blocked(*texts: str | None) -> bool:
    """빈틈 질문을 만들지 않을 말인가. 위기·죽음(precheck.is_safety_turn), 위기 상담 번호, 죽음·장례·임종·건강·종교 화제."""
    for text in texts:
        if text and (is_safety_turn(text) or CRISIS_LINE.search(text) or _NO_GAP_TOPIC.search(text)):
            return True
    return False


def turn_gap(message: str, topic: str | None) -> str | None:
    """대답 경로(기존·중간계층 같게)가 BE 에 돌려줄 빈틈 주제. 묻지 않는 화제면 None(빈틈 후보를 만들지 않는다)."""
    if topic is None or gap_blocked(message, topic):
        return None
    return topic

# [기억없음:주제] — 모델이 괄호·띄어쓰기·쌍점을 조금씩 바꿔 낸다: [기억 없음: 노래], (기억없음:노래), 【기억없음】
_GAP = re.compile(
    r"[ \t]*[\[(（【]\s*기억\s*없음\s*(?:[:：]\s*([^\[\]()（）【】\n]{0,40}?))?\s*[\])）】]",
)
# 모양이 더 어긋난 표시 — 여는 괄호 + 기억없음 인데 위 모양이 아닌 것: [기억없음 - 노래], [기억없음/노래],
# [기억없음 노래], [기억없음:노래(제목)], 40자 넘는 주제, 닫는 괄호가 없는 것. 위에서 못 뗀 것을 여기서 뗀다
# (안 떼면 화면·대화 기록·목소리로 그대로 나간다. 뒤에서 다시 거르는 곳이 없다). 짝이 맞는 닫는 괄호까지,
# 그 줄에서 닫히지 않으면 줄 끝까지 지운다(표시는 대답 맨 끝에 붙이라고 했다). 길이 제한은 두지 않는다.
_GAP_LOOSE = re.compile(r"[ \t]*([\[(（【])\s*기억\s*없음")
_OPENERS = "[(（【"
_CLOSERS = "])）】"
# 어긋난 표시의 주제 앞 나눔표(쌍점·대시·빗금·띄어쓰기 등)
_LOOSE_LEAD = re.compile(r"^[\s:：;\-‐–—―─/~·,.|>=]+")


def _loose_end(text: str, start: int) -> tuple[int, bool]:
    """'여는 괄호 + 기억없음' 바로 뒤(start)부터 그 여는 괄호와 짝이 맞는 닫는 괄호 바로 뒤 (자리, True).
    그 줄에서 닫히지 않으면 (줄 끝, False)."""
    depth = 1
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "\n":
            return i, False
        if ch in _OPENERS:
            depth += 1
        elif ch in _CLOSERS:
            depth -= 1
            if depth == 0:
                return i + 1, True
    return len(text), False


def _gap_spans(text: str) -> list[tuple[int, int, str]]:
    """표시가 있는 자리 (시작, 끝, 주제) 를 앞에서부터. 엄격한 표시를 먼저 찾고, 그 밖의 어긋난 표시를 더한다."""
    spans = [(m.start(), m.end(), (m.group(1) or "").strip()) for m in _GAP.finditer(text)]
    for m in _GAP_LOOSE.finditer(text):
        opener = m.start(1)
        if any(start <= opener < end for start, end, _ in spans):
            continue  # 이미 뗄 표시(엄격한 표시·앞의 어긋난 표시) 안이다
        end, closed = _loose_end(text, m.end())
        body = text[m.end():end - 1 if closed else end]
        body = _GAP_LOOSE.split(body, maxsplit=1)[0]  # 속에 든 표시는 주제가 아니다
        spans.append((m.start(), end, " ".join(_LOOSE_LEAD.sub("", body).split())))
    return sorted(spans)


def split_gap(text: str) -> tuple[str | None, str]:
    """대답에서 기억 빈틈 표시를 모두 떼어 (주제, 깨끗한 글) 을 돌려준다.

    표시가 없으면 (None, 글 그대로). 표시가 있으면 주제는 첫 표시의 것(없으면 빈 글, 60자까지)이다.
    표시가 없을 때는 글을 한 글자도 바꾸지 않는다(예전 대답과 같게).
    """
    spans = _gap_spans(text or "")
    if not spans:
        return None, text
    topic = spans[0][2][:60]
    pieces: list[str] = []
    cursor = 0
    for start, end, _ in spans:
        if start >= cursor:  # 겹친 표시(어긋난 표시 속 엄격한 표시)는 바깥 것과 함께 한 번에 지운다
            pieces.append(text[cursor:start])
        cursor = max(cursor, end)
    pieces.append(text[cursor:])
    clean = re.sub(r"[ \t]+\n", "\n", "".join(pieces))
    return topic, clean.strip()


def _quote(text: str, limit: int) -> str:
    one = " ".join((text or "").split())
    return one if len(one) <= limit else one[: limit - 1] + "…"


def style_block(examples: list[StyleExample]) -> str:
    """고친 말투 예시 절. 없으면 빈 글."""
    rows = [e for e in examples if e.original.strip() and e.corrected.strip()]
    if not rows:
        return ""
    lines = [STYLE_HEAD, STYLE_LEAD]
    for e in rows:
        lines.append(f"- 예전 대답: \"{_quote(e.original, 200)}\"\n  고친 말: \"{_quote(e.corrected, 300)}\"")
    return "\n".join(lines)


def extra_system(flag_gaps: bool, examples: list[StyleExample]) -> list[str]:
    """음성 지침 바로 뒤에 넣을 system 글(순서대로). 둘 다 없으면 빈 목록 — 예전과 같은 메시지."""
    out = []
    if flag_gaps:
        out.append(GAP_INSTRUCTION)
    block = style_block(examples)
    if block:
        out.append(block)
    return out


# ---------------------------------------------------------------- 질문 만들기 (/v1/persona/gap-question)

GAP_QUESTION_PROMPT = """너는 TalkTo 서비스다. 가족이 돌아가신 분이나 사랑하는 분을 닮은 대화 상대(AI)와 이야기하다가, 대화 상대가 모르는 것이 나왔다.
그 빈틈을 채우도록 대화가 끝난 뒤 가족에게 물을 질문을 하나 만든다. 질문하는 쪽은 TalkTo 다(대화 상대가 아니다).

규칙
- 가족이 직접 겪었거나 기억하는 것을 묻는다. 예: "{label}께서 즐겨 부르시던 노래가 있었나요?", "{label}께서 자주 가시던 곳이 있었나요?"
- 그분의 이력을 캐묻지 않는다: 병명·사인·임종·장례·재산·빚·종교·정치·주소·연락처·주민번호·학력·직장 같은 것은 묻지 않는다.
- 그분의 죽음·장례·임종·마지막 순간에 관한 말, 죽고 싶다·자해 같은 힘든 마음을 털어놓은 말(위기), 건강·병·약, 종교·신앙에 관한 것이면 → skip.
- 인사·안부·근황·감정 표현·가족 자신의 요즘 일이면 물을 것이 아니다 → skip.
- 열린 질문 가운데 같은 것을 묻는 것이 있으면 새로 만들지 말고 그 id 로 merge.
- 질문은 해요체 한 문장, 50자 안쪽. 50~60대가 읽기 쉬운 말로. 그분은 "{label}"라고 부른다.
- {tense}{member}
- topic 은 질문 주제를 2~12자 낱말로(예: 즐겨 부른 노래).

JSON으로만: {{"action": "new|merge|skip", "question": "...", "topic": "...", "mergeInto": "열린 질문 id 또는 null"}}"""

_SELF_TENSE = "만든 분 자신이 그분이다. 그분께 직접 묻는다(예: \"평소 즐겨 부르시는 노래가 있나요?\"). 그분 호칭은 쓰지 않는다."
_PAST = "돌아가신 분이다. 지난 일로 묻는다(…셨나요, …있었나요)."
_PRESENT = "살아 계신 분이다. 지금 일로 묻는다(…하시나요, …있나요)."
# 함께 쓰는 가족의 대화·고침에서 온 후보(fromMember). 질문과 주제는 만든 분이 보므로 그 가족이 한 말을 옮기지 않는다
# (약관 family_share: 가족과의 대화에서 나온 것도 대화 내용은 빼고 질문만 만든다). 아니면 빈 글 — 예전과 같은 지시문.
_MEMBER_RULE = (
    "\n- 이 말을 한 사람은 질문을 읽을 만든 분이 아니라 함께 쓰는 다른 가족이다. 그 가족이 한 말을 옮기지 않는다"
    "(따옴표로든 바꿔 말해서든 인용하지 않는다). 그 가족이 한 말에 나온 사람 이름·호칭·장소·날짜·사건과 그 가족 자신의 사정은 "
    "질문과 topic 에 넣지 않는다. 질문은 주제만 일반적으로 묻는다(예: \"민수 결혼식에 오셨던 거\" → "
    "\"{label}께서 가족 잔치 때 즐겨 하시던 일이 있었나요?\"). 그렇게 묻지 못하면 skip."
)
# 함께 쓰는 가족 후보에서 만든 질문에 따옴표가 있으면 그 가족의 말을 옮긴 것으로 보고 버린다(일반적인 질문에는 따옴표가 없다)
_QUOTES = re.compile(r"[\"'“”‘’「」『』]")

# 질문에 나오면 버리는 말(규칙을 어긴 질문). 모델이 지켜도 한 번 더 막는다.
_PROBING = re.compile(
    r"병명|사인|돌아가신\s?(이유|원인|날|때)|임종|장례|재산|빚|유산|상속|종교|정치|주소|연락처|전화번호|주민|계좌|비밀번호|학력|연봉"
)
_MAX_QUESTION = 120


def _label(request: GapQuestionRequest) -> str:
    label = (request.subject_label or "").strip()
    return label or "그분"


def _tense(request: GapQuestionRequest) -> str:
    if request.subject_self:
        return _SELF_TENSE
    return _PRESENT if request.deceased is False else _PAST


def _user_content(request: GapQuestionRequest) -> str:
    c = request.candidate
    lines = [
        f"가족이 한 말: {c.user_message.strip()}",
        f"대화 상대의 대답: {c.reply.strip() or '(없음)'}",
    ]
    if c.topic.strip():
        lines.append(f"대화 상대가 모른다고 표시한 주제: {c.topic.strip()}")
    if c.signal == "feedback":
        lines.append("가족이 이 대답을 '내용이 틀려요'라고 했다.")
        if c.correction and c.correction.strip():
            lines.append(f"가족이 바로잡은 말: {c.correction.strip()}")
    elif c.signal == "low_recall":
        lines.append("대화 상대의 기억에서 맞는 것을 찾지 못했다.")
    if request.from_member:
        lines.append("이 말을 한 사람: 함께 쓰는 다른 가족(질문은 만든 분이 본다 — 이 가족이 한 말은 옮기지 않는다).")
    if request.open_questions:
        lines.append("열린 질문:")
        lines += [f"- id={q.id} 주제={q.topic or '-'}: {q.question}" for q in request.open_questions]
    else:
        lines.append("열린 질문: 없음")
    return "\n".join(lines)


def _skip(settings: Settings) -> GapQuestionResponse:
    return GapQuestionResponse(action="skip", provider=PROVIDER, model=settings.openai_chat_model)


def make_gap_question(request: GapQuestionRequest, *, settings: Settings) -> GapQuestionResponse:
    """후보 하나 → 새 질문 / 열린 질문에 합치기 / 건너뛰기. 모델 답이 이상하면 건너뛴다(오류로 만들지 않는다).

    죽음·장례·임종·위기·건강·종교 후보는 모델을 부르지 않고 건너뛴다(gap_blocked). 대답은 위기 상담 번호만 본다
    (대답의 '건강 잘 챙겨라' 같은 챙김 말로 멀쩡한 후보를 버리지 않게).
    """
    c = request.candidate
    if gap_blocked(c.user_message, c.topic, c.correction) or CRISIS_LINE.search(c.reply or ""):
        return _skip(settings)
    client = chat_client(settings, "gap question")
    label = _label(request)
    system = GAP_QUESTION_PROMPT.format(label=label, tense=_tense(request),
                                        member=_MEMBER_RULE.format(label=label) if request.from_member else "")
    response = client.chat.completions.create(
        model=settings.openai_chat_model,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": _user_content(request)}],
    )
    try:
        payload = json.loads(response.choices[0].message.content or "{}")
    except json.JSONDecodeError:
        logger.warning("gap question reply was not JSON")
        return _skip(settings)
    if not isinstance(payload, dict):
        return _skip(settings)
    action = str(payload.get("action") or "").strip().lower()
    question = " ".join(str(payload.get("question") or "").split())[:_MAX_QUESTION]
    topic = " ".join(str(payload.get("topic") or "").split())[:60]
    if not topic and not request.from_member:
        # 대답 모델이 붙인 주제로 채운다. 가족 대화에서 온 것이면 채우지 않는다(그 가족이 물은 말에서 나온 주제다)
        topic = request.candidate.topic.strip()[:60]
    open_ids = {q.id for q in request.open_questions}
    if action == "merge":
        target = str(payload.get("mergeInto") or payload.get("merge_into") or "").strip()
        if target in open_ids:
            return GapQuestionResponse(action="merge", merge_into=target, provider=PROVIDER,
                                       model=settings.openai_chat_model)
        action = "new"  # 없는 id 에 합치라고 하면 질문이 있을 때만 새로 만든다
    if action != "new" or not question or _PROBING.search(question) or gap_blocked(question, topic):
        return _skip(settings)
    if request.from_member and _QUOTES.search(question):  # 함께 쓰는 가족의 말을 옮겼다
        return _skip(settings)
    return GapQuestionResponse(action="new", question=question, topic=topic or None, provider=PROVIDER,
                               model=settings.openai_chat_model)
