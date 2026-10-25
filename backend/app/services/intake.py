# 핵심 질문과, 그 답을 파이프라인이 읽는 persona_intake.json 모양으로 옮기는 일.
#
# 문항 번호와 선지 문구는 AI 레포(TalkTo_PersonaAI_AI) docs/Persona_Intake.md 를 그대로 따른다.
#
# 2026-10-21 개편(Persona_Intake.md PART 0): 만들 때 꼭 답할 질문을 '가족이 겪은 그분'으로 다시
# 짰다(나와 그분 → 그분이 하시던 말 → 그분이 해 주신 것 → 그분은 어떤 분 → 조심할 이야기).
# 새 필수 질문은 이 개편 뒤에 만든 가족·고인 대화 상대(basics.intakeVersion = 2)에만 쓴다. 표시가
# 없는 예전 대화 상대와 나 자신은 예전 넷(C-01·C-02·C-05·M4-01)만 꼭 받는다 — 새 문항을 예전
# 대화 상대에 필수로 하면 그 답을 채우기 전까지 아무 답도 저장하지 못한다(READY 도 final 로 검사).
# 그분의 개인 정보(고향·하신 일·언제 무렵)와 나머지 문항은 '기억 더 채우기'에서 묻고, 그 밖은 녹음
# 분석이 채운다. 나 자신은 이번 개편 대상이 아니라 묶음·차례·문구가 개편 전 그대로다
# (SELF_QUESTION_LAYOUT — questions_for·more_questions_for·sections_for·more_modules_for).
#
# '기억이 안 나요'·'따로 없었어요'·'모름'은 표시 값(UNKNOWN·NONE)으로 저장한다. 답한 것으로 세지만,
# 프롬프트·기억·AI 로 가는 모든 길(for_ai)에서는 빈 답이다 — 이 말이 그분 입으로 나오지 않게.
# 선택지로 고른 '모름'(학교·종교·돈)과 글로 적은 '모름'도 for_ai 에서 빈 답이다.

import re
from datetime import date

from app.db.models import LifeStatus
from app.services import ages, forgetting, sms


class IntakeError(ValueError):
    """사용자에게 그대로 보여줄 입력 오류."""


# 나 자신(회원 본인)의 대화 상대. 상태는 늘 '살아 계심', 내 이름 = 그분 이름.
SELF_RELATION = "나 자신"
RELATIONS = [
    SELF_RELATION,
    "어머니",
    "아버지",
    "할머니",
    "할아버지",
    "외할머니",
    "외할아버지",
    "배우자",
    "아들",
    "딸",
    "형·오빠",
    "누나·언니",
    "남동생",
    "여동생",
    "친구",
    "스승",
]
MY_RELATIONS = [
    "아들",
    "딸",
    "손자",
    "손녀",
    "외손자",
    "외손녀",
    "어머니",
    "아버지",
    "배우자",
    "며느리",
    "사위",
    "조카",
    "형제자매",
    "친구",
    "제자",
]
# 만들 때 고르는 그분 상태. 치매 등 '대화가 어려우신 분'(unable)은 2026-10-22 법률 자문·대표
# 결정으로 사업 대상에서 뺐다 — 새로 만들 수 없고, 예전에 그렇게 고른 대화 상대만 그 상태를 그대로
# 둔다(LEGACY_STATUSES · validate_basics). 지우지 않고 새 녹음 처리·목소리 다시 만들기만 멈춘다
# (gate)
STATUSES = {
    "alive": "네, 살아 계세요",
    "deceased": "아니요, 돌아가셨어요",
}
LEGACY_STATUSES = {"unable": "대화가 어려우신 분(예전에 고른 상태)"}
LIFE_STATUS = {
    "alive": LifeStatus.LIVING,
    "deceased": LifeStatus.DECEASED,
    "unable": LifeStatus.LIVING,
}

# ── 만 19세 미만 대화 상대(2026-10-22 법률 자문 · WO 4) ──
# 아이의 대화 상대는 그 아이의 법정대리인(부모)만 만든다: 그분이 나에게(relation)와 나는 그분의
# (myRelation)를 이 목록으로만 받는다
MINOR_RELATIONS = ("아들", "딸")
MINOR_MY_RELATIONS = ("어머니", "아버지")
# 가족 공유로 함께 쓸 수 있는 분(직계가족 — 가족이 적는 '그분과 어떤 사이')
MINOR_SHARE_RELATIONS = ("어머니", "아버지", "할머니", "할아버지", "외할머니", "외할아버지")
# 부모 양쪽(민법 제909조): 신청한 부모가 고른다
GUARDIAN_BASES = {
    "both_agreed": "다른 쪽 부모님의 동의를 받았어요",
    "sole_custody": "제가 혼자 친권을 가지고 있어요",
}
# basics 에 두는 미성년 칸. notGuardian = 부모가 아닌 분이 예전에 만든 아이의 대화 상대(PUT /minor)
MINOR_KEYS = ("minor", "birthYm", "guardianBasis", "notGuardian")

# ── 돌아가신 분의 신청 자격과 유족 명단(WO 5) ──
APPLICANT_RELATIONS = {
    "spouse": "배우자",
    "child": "자녀",
    "grandchild": "손주",
    "parent": "부모",
    "other": "그 밖의 친족·지인",
}
# '그 밖'이면 동의해 준 유족(배우자·직계비속·직계존속 중 한 분)
CONSENTING_RELATIONS = {
    "spouse": "배우자",
    "child": "자녀",
    "grandchild": "손주",
    "parent": "부모",
}
# 유족 명단의 관계: 고인의 배우자·자녀(이들이 없으면 부모)
BEREAVED_RELATIONS = {
    "spouse": "배우자",
    "son": "아들",
    "daughter": "딸",
    "mother": "어머니",
    "father": "아버지",
}

# M2-01 가족 표 한 줄의 칸(이름만 꼭 받는다). 웹에서는 '나에게는'(내가 이 사람을 부르는 말)과
# 살아 계심/돌아가심, 한 줄 메모를 더 받는다(AI 레포 docs/Persona_Intake.md M2-01 '웹앱 추가').
FAMILY_FIELDS = {"name": 15, "relation": 20, "toMe": 15, "calledBy": 15, "note": 60}
FAMILY_STATUSES = ("alive", "deceased")
FAMILY_MAX = 10
# 나 자신·본인처럼 대화하는 사람(회원)을 가리키는 말. 가족 표에 따로 넣지 않는다.
_SELF_WORDS = {"나", "저", "본인", "나 자신", SELF_RELATION}

# 만들 때 질문 차례. 2 = 2026-10-21 개편(가족이 겪은 그분). POST /personas 가 가족·고인 대화
# 상대의 basics 에 적는다(칸·마이그레이션 없이). 없으면 예전(1) 대화 상대다.
INTAKE_VERSION = 2
# 예전 대화 상대와 나 자신이 꼭 답할 질문(개편 전 그대로)
LEGACY_REQUIRED = ("C-01", "C-02", "C-05", "M4-01")

# '기억이 안 나요'·'모름'과 '따로 없었어요'의 저장 값. 답한 것으로 세지만 AI 로는 보내지 않는다.
UNKNOWN = "__unknown__"
NONE = "__none__"
SENTINELS = frozenset({UNKNOWN, NONE})
SKIP_FORGOT = {"value": UNKNOWN, "label": "기억이 안 나요"}
SKIP_NONE = {"value": NONE, "label": "따로 없었어요"}
SKIP_DONT_KNOW = {"value": UNKNOWN, "label": "모름"}

# 만들 때 질문의 묶음(section)과 묶음 머리의 한 줄 안내. 화면은 질문 목록에서 처음 나온 순서대로
# 묶음 카드를 그리니, 이 순서도 QUESTIONS 와 같게 둔다(Persona_Intake.md PART 0 의 묶음 순서).
QUESTION_SECTIONS = [
    {"id": "나와 그분", "lead": "그분과 나 사이에 오가던 말부터 여쭤볼게요."},
    {
        "id": "그분이 하시던 말",
        "lead": "실제 하시던 말 그대로가 가장 좋아요. 녹음에서 찾은 말은 누르기만 하면 돼요.",
    },
    {"id": "그분이 해 주신 것", "lead": "나에게 해 주시던 일을 떠올려 주세요."},
    {"id": "그분은 어떤 분", "lead": "가족이 기억하는 그분 모습 그대로 알려 주세요."},
    {
        "id": "조심할 이야기",
        "lead": "마음 아픈 이야기를 어디까지 할지 정해 주세요. 정해 주신 대로 지켜요.",
    },
]

# 질문 문구의 갈래. title 은 돌아가신 분(과거형) 기준이고, 살아 계신 분(대화할 수 있음·어려움)은
# titleLiving(현재형)을 쓴다. 나 자신은 이번 개편 대상이 아니라 개편 전 문구 그대로다: titleSelf 가
# 있으면 그것, 없으면 title(살아 계셔도 titleLiving 을 쓰지 않는다 — question_title). 보기 글·안내도
# placeholderSelf·hintSelf 가 있으면 그것이다. 묶음·차례도 개편 전 그대로다(SELF_QUESTION_LAYOUT).
# skipIfBasics: 기본 정보의 그 칸이 차 있으면 묻지 않는다(그 칸이 답을 대신한다, P1-07A).
# skipOption: '기억이 안 나요'·'따로 없었어요' 단추. 고르면 그 value 를 답으로 저장한다.
# required 는 개편 뒤 가족·고인 대화 상대(intakeVersion 2) 기준이다. 실제로 꼭 받는 질문은
# required_ids(basics) 가 정한다(예전 대화 상대·나 자신은 LEGACY_REQUIRED).

# 조심할 이야기(M9-01 주제별 등급, M9-03)
TOPIC_GRADES = ["자유", "물으면만", "짧게 넘김", "금지"]
# 등급 저장 값(TOPIC_GRADES)은 build_intake·persona_builder 가 읽으니 그대로 둔다.
# 화면에는 아래 쉬운 말로 보인다.
TOPIC_GRADE_LABELS = {
    "자유": "편하게 이야기해요",
    "물으면만": "물어볼 때만",
    "짧게 넘김": "짧게 넘겨요",
    "금지": "이야기하지 않아요",
}
M9_TOPICS = {
    "health": "그분의 건강·병력",
    "death": "그분의 사망·장례·임종",
    "inherit": "상속·재산",
    "conflict": "가족 갈등",
    "politics": "정치",
    "religion": "종교",
    "alcohol": "술·담배",
    "regret": "후회·미안함",
}
# 화면 제목만 쉬운 말로. 주제 이름(M9_TOPICS)은 AI 에 그대로 보낸다.
M9_TITLES = {"health": "그분의 건강·앓으신 병"}
# 개편 뒤 대화 상대에 미리 채워 두는 등급. 가족은 확인만 하고, 채운 값은 답한 것으로 센다
# (answer_defaults — 대화 상대 상세의 answers 에 실리고, 저장·만들기 때 빈 칸에 넣는다).
M9_DEFAULTS = {"death": "물으면만"}
# 주제마다 한 줄(compact: 주제 + 등급 칩)로 촘촘하게 그린다. 그분의 사망·장례·임종은
# 돌아가신 분만 묻고(when=deceased), 미리 채운 등급을 확인만 하면 되는 필수다.
_M9_ROWS: list[dict] = [
    {
        "id": f"M9-01-{key}",
        "section": "조심할 이야기",
        "type": "single",
        "required": key in M9_DEFAULTS,
        "compact": True,
        **({"when": "deceased"} if key == "death" else {}),
        "title": M9_TITLES.get(key, topic),
        "options": TOPIC_GRADES,
        "labels": TOPIC_GRADE_LABELS,
        **(
            {
                "hint": "편하게 이야기해요: 그분이 먼저 꺼내도 돼요 · "
                "물어볼 때만: 먼저 꺼내지는 않아요 · "
                "짧게 넘겨요: 한두 마디만 하고 다른 이야기로 넘어가요 · "
                "이야기하지 않아요: 이 이야기는 하지 않아요"
            }
            if i == 0
            else {}
        ),
    }
    for i, (key, topic) in enumerate(M9_TOPICS.items())
]

QUESTIONS: list[dict] = [
    # ── 나와 그분 ──
    {
        "id": "P1-07A",
        "section": "나와 그분",
        "type": "text",
        "required": True,
        "when": "notSelf",
        "skipIfBasics": "calledMe",
        "maxLength": 15,
        "title": "그분은 나를 뭐라고 부르셨어요?",
        "titleLiving": "그분은 나를 뭐라고 부르세요?",
        "hint": "대화할 때 그분도 나를 이렇게 불러요.",
        "placeholder": "예: 도윤아 / 우리 딸 / 정우야",
    },
    {
        "id": "M2-01-level",
        "section": "나와 그분",
        "type": "single",
        "required": True,
        "when": "notSelf",
        "title": "나에게는 반말이셨어요, 존댓말이셨어요?",
        "titleLiving": "나에게는 반말이세요, 존댓말이세요?",
        "options": ["반말", "존댓말"],
    },
    {
        "id": "M2-01-self",
        "section": "나와 그분",
        "type": "text",
        "required": True,
        "when": "notSelf",
        "maxLength": 15,
        "title": "나와 이야기하실 때 그분은 자기를 뭐라고 하셨어요?",
        "titleLiving": "나와 이야기하실 때 그분은 자기를 뭐라고 하세요?",
        "placeholder": "예: 할머니가 / 엄마가 / 내가",
        "skipOption": SKIP_NONE,
    },
    {
        "id": "M2-01-tone",
        "section": "나와 그분",
        "type": "single",
        "required": False,
        "other": True,
        "title": "나와 이야기하실 때는 말투가 어떠셨어요?",
        "titleLiving": "나와 이야기하실 때는 말투가 어떠세요?",
        "options": [
            "다른 가족과 같음",
            "더 다정하고 살가움",
            "더 걱정하고 잔소리함",
            "더 존중하고 조심스러움",
            "더 장난스러움",
        ],
    },
    {
        "id": "M2-01",
        "section": "나와 그분",
        "type": "family",
        "required": False,
        "max": FAMILY_MAX,
        "title": "그분이 알아야 할 가족을 알려 주세요",
        "hint": "대화하다가 '아빠'·'고모'라고 해도 그분이 누구인지 알아들어요. "
        "함께 대화하지 않는 분, 돌아가신 분도 적어 주세요. 안 적어도 괜찮아요.",
        "privacy": "살아 계신 가족의 주소·직장·학교·건강 같은 사생활은 적지 말아 주세요. "
        "대화에서도 그런 이야기는 하지 않아요.",
        "relationOptions": [
            "남편",
            "아내",
            "아들",
            "딸",
            "며느리",
            "사위",
            "손자",
            "손녀",
            "형제자매",
            "어머니",
            "아버지",
            "친구",
        ],
        "toMeOptions": [
            "아빠",
            "엄마",
            "할아버지",
            "할머니",
            "삼촌",
            "고모",
            "이모",
            "외삼촌",
            "큰아버지",
            "작은아버지",
            "형·오빠",
            "누나·언니",
            "동생",
        ],
    },
    # ── 그분이 하시던 말 ──
    {
        "id": "M3-01",
        "section": "그분이 하시던 말",
        "type": "text",
        "required": True,
        "maxLength": 30,
        "title": "나를 만나거나 내 전화를 받으시면 첫마디를 뭐라고 하셨어요?",
        "titleLiving": "나를 만나거나 내 전화를 받으시면 첫마디를 뭐라고 하세요?",
        "titleSelf": "전화를 받으면 첫마디를 뭐라고 하셨나요?",
        "placeholder": "예: 도윤이니? / 왔나, 밥은?",
        "placeholderSelf": "예: 우리 강아지니?",
        "skipOption": SKIP_FORGOT,
    },
    {
        "id": "M3-02",
        "section": "그분이 하시던 말",
        "type": "text",
        "required": True,
        "maxLength": 30,
        "title": "헤어지거나 전화를 끊을 때 마지막 말은요?",
        "titleSelf": "전화를 끊을 때 마지막 말은요?",
        "placeholder": "예: 쉬어라 / 조심히 가라",
        "placeholderSelf": "예: 쉬어라 / 많이 사랑해",
        "skipOption": SKIP_FORGOT,
    },
    {
        "id": "C-09",
        "section": "그분이 하시던 말",
        "type": "phrases",
        "required": True,
        "title": "나에게 자주 하시던 말을 하나씩 적어 주세요. 걱정·당부·잔소리도 좋아요.",
        "titleLiving": "나에게 자주 하시는 말을 하나씩 적어 주세요. 걱정·당부·잔소리도 좋아요.",
        "titleSelf": "자주 하시던 말이나 입버릇이 있나요?",
        # 실제 말 그대로·녹음에서 찾은 말 누르기는 묶음 안내가 말한다(되풀이하지 않게)
        "hint": "한 줄에 하나씩요. 두세 개면 더 좋아요.",
        "hintSelf": "하나씩 적어 주세요.",
        "placeholder": "예: 밥은 먹었냐",
        # 녹음에서 찾은 말 후보를 이 문항(과 M3-07)에만 보인다
        "suggest": True,
    },
    {
        "id": "C-01",
        "section": "그분이 하시던 말",
        "type": "single",
        "required": True,
        "title": "그분은 한 번 말씀하실 때 보통 얼마나 길게 하셨어요?",
        "titleLiving": "그분은 한 번 말씀하실 때 보통 얼마나 길게 하세요?",
        "options": ["한두 문장으로 짧게", "서너 문장 정도", "그보다 길게, 예를 들어가며 자세히"],
    },
    {
        "id": "C-02",
        "section": "그분이 하시던 말",
        "type": "single",
        "required": True,
        "other": True,
        "title": "이야기할 때 주로 어떤 식이셨어요?",
        "titleLiving": "이야기할 때 주로 어떤 식이세요?",
        "options": [
            "자기 경험담이나 옛날 이야기를 들려주며 말한다",
            "결론이나 답을 바로 말한다",
            "되물어 보면서 상대 이야기를 끌어낸다",
            "말수가 적고, 말보다 행동(끼니·안부 챙김)으로 표현한다",
        ],
    },
    {
        "id": "M8-11",
        "section": "그분이 하시던 말",
        "type": "phrases",
        "required": False,
        "title": "먼저 자주 물으시던 말이 있나요?",
        "titleLiving": "먼저 자주 물으시는 말이 있나요?",
        "placeholder": "예: 어디냐 / 아픈 데는 없고?",
    },
    {
        "id": "M8-01",
        "section": "그분이 하시던 말",
        "type": "text",
        "required": False,
        "maxLength": 120,
        "title": '"보고 싶어요"라고 하면 뭐라고 하셨을까요?',
        "titleLiving": '"보고 싶어요"라고 하면 뭐라고 하실까요?',
        "placeholder": "예: 나도 보고 싶지. 밥은 잘 챙겨 먹고 있냐",
    },
    {
        "id": "M8-02",
        "section": "그분이 하시던 말",
        "type": "text",
        "required": False,
        "maxLength": 120,
        "title": '"너무 힘들어요"라고 하면요?',
        "placeholder": "예: 괜찮아, 잘될 거야. 너무 애쓰지 마라",
    },
    {
        "id": "M8-03",
        "section": "그분이 하시던 말",
        "type": "text",
        "required": False,
        "maxLength": 120,
        "title": '"좋은 일이 생겼어요!" 하면 뭐라고 하셨을까요?',
        "titleLiving": '"좋은 일이 생겼어요!" 하면 뭐라고 하실까요?',
        "placeholder": "예: 아이고 잘했다, 우리 강아지",
    },
    {
        "id": "M8-08",
        "section": "그분이 하시던 말",
        "type": "text",
        "required": False,
        "maxLength": 60,
        "title": "모르는 걸 물으면 그분은 어떻게 말하셨을까요?",
        "titleLiving": "모르는 걸 물으면 그분은 어떻게 말하실까요?",
        "hint": "대화할 때 모르는 이야기는 지어내지 않고 이 말처럼 대답해요.",
        "placeholder": "예: 그건 내가 잘 모르겠구나",
    },
    # ── 그분이 해 주신 것 ──
    {
        "id": "C-07",
        "section": "그분이 해 주신 것",
        "type": "multi",
        "required": True,
        "max": 3,
        "other": True,
        "title": "그분은 나를 어떻게 챙기셨어요?",
        "titleLiving": "그분은 나를 어떻게 챙기세요?",
        "titleSelf": "사랑하는 사람에게 마음을 어떻게 표현하셨어요?",
        "hint": "최대 3개",
        "options": [
            '"사랑한다", "고맙다" 같은 말로 직접',
            "음식·밥을 챙겨서",
            "용돈·선물·물건을 챙겨서",
            "건강·안부를 걱정하는 말로",
            "함께 시간을 보내고 일상 이야기를 나누며",
            "잘 표현하지 않는 편",
        ],
    },
    {
        "id": "C-03",
        "section": "그분이 해 주신 것",
        "type": "single",
        "required": True,
        "other": True,
        "title": "내가 힘들어할 때 그분은 어떻게 하셨어요?",
        "titleLiving": "내가 힘들어할 때 그분은 어떻게 하세요?",
        "titleSelf": "상대가 힘들어하면 그분은 어떻게 하셨어요?",
        "options": [
            "같이 울고 웃으며 감정을 나눈다",
            "담담하게 끝까지 들어준다",
            "말없이 곁에 있어준다",
            '짧은 한마디로 안심시킨다 (예: "잘될 거야")',
        ],
    },
    {
        "id": "C-04",
        "section": "그분이 해 주신 것",
        "type": "multi",
        "required": False,
        "max": 2,
        "other": True,
        "title": "내가 고민을 털어놓으면 주로 어떻게 하셨어요?",
        "titleLiving": "내가 고민을 털어놓으면 주로 어떻게 하세요?",
        "titleSelf": "고민을 털어놓으면 주로 어떻게 하셨어요?",
        "hint": "두 개까지 골라 주세요.",
        "options": [
            "구체적인 해결책을 준다",
            "무조건 내 편을 들어준다",
            "질문을 던져 스스로 답을 찾게 한다",
            "해결책보다 다독이고 안심시킨다",
        ],
    },
    {
        "id": "C-08",
        "section": "그분이 해 주신 것",
        "type": "textarea",
        "required": False,
        "maxLength": 200,
        "title": "나를 챙겨 주시던 장면 하나를 들려주세요.",
        "titleSelf": "사랑을 표현하셨던 장면 하나를 들려주세요.",
        "placeholder": "예: 매운 걸 못 먹는 손주를 위해 김치를 따로 안 맵게 담그셨어요.",
    },
    # ── 그분은 어떤 분 ──
    {
        "id": "M4-01",
        "section": "그분은 어떤 분",
        "type": "multi",
        "required": True,
        "max": 5,
        "other": True,
        "title": "성격을 가장 잘 나타내는 말을 골라주세요.",
        "hint": "최대 5개",
        "options": [
            "다정함",
            "말수가 적음",
            "유쾌함",
            "고집이 있음",
            "걱정이 많음",
            "희생적임",
            "정직함",
            "절약함",
            "부지런함",
            "조용함",
            "따뜻함",
            "엄격함",
            "호기심 많음",
            "도전적임",
            "성실함",
            "느긋함",
            "꼼꼼함",
            "사람을 좋아함",
            "혼자 있는 걸 좋아함",
        ],
    },
    # 떠오르는 것(M6-08)과 그 까닭(M6-08-why, 선택)은 나란한 두 칸이다. 둘이 기억 하나가 된다.
    # 꼭 받는 건 개편 뒤 가족·고인뿐이다. 예전 대화 상대는 선택이고, 나 자신에게는 묻지 않는다
    # (개편 때 새로 생긴 문항 — SELF_HIDDEN).
    {
        "id": "M6-08",
        "section": "그분은 어떤 분",
        "type": "text",
        "required": True,
        "maxLength": 20,
        "title": "그분 하면 떠오르는 것 하나를 알려 주세요.",
        "hint": "음식·물건·장소·냄새 무엇이든요.",
        "placeholder": "예: 된장찌개 / 낡은 라디오 / 시장",
    },
    {
        "id": "M6-08-why",
        "section": "그분은 어떤 분",
        "type": "text",
        "required": False,
        "maxLength": 60,
        "title": "왜 떠오르나요? (선택)",
        "placeholder": "예: 갈 때마다 끓여 주셨어요",
    },
    {
        "id": "M7-01",
        "section": "그분은 어떤 분",
        "type": "textarea",
        "required": True,
        "maxLength": 600,
        "title": "나와 함께한 장면 하나를 들려주세요.",
        "titleSelf": "함께한 기억 하나를 눈앞에 그려지듯 들려주세요.",
        "hint": "짧아도 괜찮아요. 언제·어디서·무슨 일이 있었는지 적어 주시면, "
        "대화할 때 그분이 이 일을 기억해요. 말로 답하셔도 돼요.",
        # 나 자신은 개편 전처럼 선택이다
        "hintSelf": "안 적어도 괜찮아요. 언제·어디서·무슨 일이 있었는지 적어 주시면, "
        "대화할 때 그분이 이 일을 기억해요.",
        "placeholder": "예: 수능 날 아침, 할머니가 새벽부터 찹쌀떡을 쪄서 도시락에 넣어주셨어요.",
    },
    {
        "id": "C-05",
        "section": "그분은 어떤 분",
        "type": "text",
        # 예전 대화 상대·나 자신은 꼭 받는다(LEGACY_REQUIRED)
        "required": False,
        "maxLength": 60,
        "title": "그분을 한 문장으로 표현한다면?",
        "placeholder": "예: 본인이 힘들어도 가족을 위해 희생하며 사랑으로 살아오신 분",
    },
    {
        "id": "C-06",
        "section": "그분은 어떤 분",
        "type": "textarea",
        "required": False,
        "maxLength": 300,
        "title": "처음 만난 사람에게 그분을 소개한다면 어떻게 말하겠어요?",
        "hint": "성격, 사람을 대하는 방식, 주변에서 느끼던 인상을 한 줄씩요.",
    },
    # ── 조심할 이야기 ──
    *_M9_ROWS,
    {
        "id": "C-10",
        "section": "조심할 이야기",
        "type": "multi",
        "required": False,
        "other": True,
        "title": "대화할 때 그분이 절대 하지 않았으면 하는 말이나 행동을 골라 주세요.",
        "hint": "여러 개 골라도 돼요.",
        "options": [
            "화내거나 꾸짖는 말투",
            "병·수술·임종·장례 이야기를 먼저 꺼내기",
            "돈·재산·상속 이야기",
            "특정 가족을 험담하거나 편 가르기",
            "종교 이야기나 권유",
            "병원·약·치료에 대한 조언",
            "옛날 이야기를 먼저 길게 꺼내기",
        ],
    },
    {
        "id": "M9-03",
        "section": "조심할 이야기",
        "type": "phrases",
        "required": False,
        "title": "먼저 꺼내지 않았으면 하는 사람이나 이야기가 있나요?",
        "placeholder": "예: 큰아버지 / 사업이 망했던 일",
        "hint": "여기 적은 이야기는 물어볼 때만 해요. 살아 계신 가족의 주소·직장·건강 같은 "
        "사생활은 아예 이야기하지 않아요.",
    },
]


# ────────────────────────── 기억 더 채우기(선택 질문) ──────────────────────────
# 다 만든 페르소나를 더 그분답게 다듬는 질문. 모두 선택이고, 저장하면 지침·기억을 다시 만든다.
# 문항 번호·선지는 Persona_Intake.md(PART 1·2 일부, PART 3 M1~M9, PART 4 T1)를 따르되, 표로 묻던
# 문항은 웹에서 쓰기 쉽게 '한 줄에 하나씩' 목록으로 바꿨다. 가족 표(M2-01)는 핵심 질문에서
# 한 사람씩 더하는 칸(type family)으로 받는다. 만들 때 묻는 문항(QUESTIONS)은 여기 두지 않는다
# — 같은 id 가 두 목록에 있으면 정보 고치기와 기억 더 채우기가 같은 답을 서로 덮어쓴다.
# 그분의 개인 정보(언제 무렵·고향·하신 일·태어난 해)는 가족이 모를 수 있어 '모름'을 함께 둔다.

MORE_MODULES = [
    {"id": "기본 정보", "lead": "언제 무렵의 그분인지, 고향과 하신 일 같은 기본 이야기"},
    {"id": "말투·표현", "lead": "화나셨을 때 말투, 사투리, 말끝을 맺으시던 말"},
    {"id": "성격·가치관", "lead": "중요하게 여기신 것, 다툼이 생기면 어떻게 하셨는지"},
    {"id": "인생 이야기", "lead": "살면서 있었던 일들 — 적어 주시면 대화할 때 그분이 기억해요"},
    {"id": "일상 속 기억", "lead": "즐겨 해 주시던 음식, 자주 계시던 곳, 그분을 떠올리게 하는 것"},
    {"id": "이럴 땐", "lead": "이런 말을 들으면 뭐라고 하셨을지"},
    {
        "id": "마지막 시기",
        "lead": "돌아가시기 전 마지막 무렵 (남기고 싶은 만큼만)",
        "when": "deceased",
    },
]

_SCENE = "있었던 일 하나를 눈앞에 그려지듯 적어 주세요. 대화할 때 그분이 이 일을 기억해요."
_SAID = "그분이 하셨을 법한 말을 따옴표 없이 그대로 적어 주세요."

MORE_QUESTIONS: list[dict] = [
    # 기본 정보(P1-05, M1) — P1-05·M1-02·M1-07 은 2026-10-21 에 만들 때 질문에서 옮겼다(id 그대로)
    {
        "id": "P1-05",
        "section": "기본 정보",
        "type": "text",
        "maxLength": 30,
        "title": "언제 무렵의 그분과 이야기하고 싶나요?",
        "hint": "적어 주시면 그 무렵의 모습으로 이야기해요.",
        "placeholder": "예: 칠순 무렵 / 2015년쯤",
        "skipOption": SKIP_DONT_KNOW,
    },
    {
        "id": "M1-01",
        "section": "기본 정보",
        "type": "text",
        "maxLength": 10,
        "title": "태어나신 해는요?",
        "placeholder": "예: 1942",
        "skipOption": SKIP_DONT_KNOW,
    },
    {
        "id": "M1-02",
        "section": "기본 정보",
        "type": "text",
        "maxLength": 20,
        "title": "고향은 어디셨어요?",
        "titleLiving": "고향은 어디세요?",
        "placeholder": "예: 전남 순천",
        "skipOption": SKIP_DONT_KNOW,
    },
    {
        "id": "M1-07",
        "section": "기본 정보",
        "type": "textarea",
        "maxLength": 400,
        "title": "어떤 일을 하며 사셨어요? 살림·농사·장사도 다 일이에요.",
        "hint": "무슨 일을 언제쯤 하셨는지 한두 줄이면 돼요.",
        "skipOption": SKIP_DONT_KNOW,
    },
    {
        "id": "M1-03",
        "section": "기본 정보",
        "type": "textarea",
        "maxLength": 300,
        "title": "고향은 어떤 곳이었나요?",
        "hint": "풍경·집·동네 사람 중 떠오르는 것",
    },
    {
        "id": "M1-05",
        "section": "기본 정보",
        "type": "phrases",
        "title": "살아오신 곳을 순서대로 적어 주세요.",
        "placeholder": "예: 순천 · 1942~1965",
    },
    {
        "id": "M1-06",
        "section": "기본 정보",
        "type": "single",
        "title": "학교는 어디까지 다니셨나요?",
        "options": [
            "학교를 다니지 못함",
            "초등(국민학교)",
            "중학교",
            "고등학교",
            "대학교",
            "대학원 이상",
            "모름",
        ],
    },
    {
        "id": "M1-08",
        "section": "기본 정보",
        "type": "multi",
        "max": 3,
        "other": True,
        "title": "가족 안에서 그분은 어떤 역할이었나요?",
        "hint": "최대 3개",
        "options": [
            "집안 살림을 도맡음",
            "생계를 책임짐",
            "가족을 챙기고 돌봄",
            "갈등을 중재함",
            "집안의 어른·결정권자",
            "막내·귀염둥이",
        ],
    },
    {
        "id": "M1-09",
        "section": "기본 정보",
        "type": "single",
        "sensitive": "religion",
        "other": True,
        "title": "종교나 신앙이 있으셨나요?",
        "options": ["없음", "기독교", "천주교", "불교", "모름"],
    },
    {
        "id": "M1-10",
        "section": "기본 정보",
        "type": "textarea",
        "sensitive": "religion",
        "maxLength": 200,
        "title": "기도하거나 마음을 의지하던 습관이 있으셨나요?",
    },
    # 말투·표현(M3-03~07 — 첫마디·마지막 말(M3-01·02)은 만들 때 묻는다)
    {
        "id": "M3-03",
        "section": "말투·표현",
        "type": "single",
        "other": True,
        "title": "화가 나면 말투가 어떻게 변하셨나요?",
        "options": [
            "거의 화내지 않음",
            "말수가 줄고 조용해짐",
            "목소리가 커지고 직설적이 됨",
            "잔소리가 길어짐",
        ],
    },
    {
        "id": "M3-04",
        "section": "말투·표현",
        "type": "text",
        "maxLength": 30,
        "title": "웃으실 때 소리나 '아이고'처럼 자주 내시던 말은요?",
        "placeholder": "예: 아유 / 아이고",
    },
    {
        "id": "M3-05",
        "section": "말투·표현",
        "type": "single",
        "title": "사투리나 특유의 억양이 있나요?",
        "options": ["없음(표준어)", "약간(고향 억양)", "뚜렷함"],
        "detail": {"placeholder": "어느 지역인지, 예시 문장 하나 (예: 전라도 · 밥 묵었냐잉)"},
    },
    {
        "id": "M3-07",
        "section": "말투·표현",
        "type": "phrases",
        "title": "말끝을 어떻게 맺으셨어요? 실제 문장 두세 개면 돼요.",
        "hint": "뜻보다 끝맺음이 드러나는 말로요.",
        "placeholder": "예: 그러믄 됐다",
        "suggest": True,
    },
    {
        "id": "M3-06",
        "section": "말투·표현",
        "type": "multi",
        "other": True,
        "title": "대화할 때 그분이 쓰시면 어색할 말투는요?",
        "hint": "여러 개 골라도 돼요.",
        "options": [
            "유행어·신조어",
            "영어 섞어 쓰기",
            "긴 설교·인생 조언",
            "과장된 감탄사",
            "딱딱한 문어체·발표 톤",
            "과한 사투리",
            "종교적 표현",
            "문학적·시적 표현",
            "원망하거나 탓하는 말",
            "단정적으로 과장하는 말",
        ],
        # 저장 값(options)은 AI 에 그대로 보낸다. 화면에는 쉬운 말로 보인다.
        "labels": {
            "과장된 감탄사": "지나치게 호들갑스러운 말",
            "딱딱한 문어체·발표 톤": "책 읽듯 딱딱한 말투",
            "문학적·시적 표현": "시나 소설 같은 말",
            "단정적으로 과장하는 말": "부풀려서 딱 잘라 하는 말",
        },
    },
    # 성격·가치관(M4 — 챙겨 주시던 장면(C-08)은 만들 때 묻는다)
    {
        "id": "M4-02",
        "section": "성격·가치관",
        "type": "phrases",
        "max": 2,
        "title": "인생에서 가장 중요하게 여기신 것은?",
        "placeholder": "예: 가족 화목",
        "hint": "두 가지까지 적을 수 있어요.",
    },
    {
        "id": "M4-03",
        "section": "성격·가치관",
        "type": "single",
        "title": "돈에 대해서는 어떤 편이셨나요?",
        "options": ["아끼는 편", "쓰는 편", "남에게는 후하고 본인에게는 아낌", "무심한 편", "모름"],
    },
    {
        "id": "M4-04",
        "section": "성격·가치관",
        "type": "phrases",
        "title": "일이나 성실함에 대해 자주 하시던 말",
        "placeholder": "예: 부지런해야 먹고산다",
    },
    {
        "id": "M4-05",
        "section": "성격·가치관",
        "type": "single",
        "other": True,
        "title": "갈등이 생기면 어떻게 하셨나요?",
        "options": [
            "따지고 바로잡는 편",
            "참고 넘기는 편",
            "말로 차분히 푸는 편",
            "자리를 피하는 편",
        ],
    },
    {
        "id": "M4-06",
        "section": "성격·가치관",
        "type": "textarea",
        "maxLength": 200,
        "title": "약속이나 예의에 대해 강조하신 것이 있나요?",
        "hint": "실제 하시던 말 하나와 늘 지키시던 행동 하나를 적어 주세요.",
    },
    {
        "id": "M4-07",
        "section": "성격·가치관",
        "type": "textarea",
        "sensitive": "religion",
        "maxLength": 300,
        "title": "신앙이나 죽음에 대해 그분은 어떻게 말하셨나요?",
        "hint": "실제로 하신 말이나 태도만 적어 주세요. 대화할 때 그분은 이 이야기를 "
        "물어볼 때만 해요.",
    },
    # 인생 이야기(M7-01 둘째 장면부터, M5) — 만들 때는 함께한 장면 하나(M7-01)만 받는다
    {
        "id": "M7-01-2",
        "section": "인생 이야기",
        "type": "textarea",
        "maxLength": 600,
        "title": "나와 함께한 장면이 더 있으면 들려주세요.",
        "hint": _SCENE,
    },
    {
        "id": "M5-01",
        "section": "인생 이야기",
        "type": "textarea",
        "maxLength": 600,
        "title": "어린 시절 살던 집을 떠올려 보세요. 어떤 장면이 보이나요?",
        "hint": _SCENE,
    },
    {
        "id": "M5-02",
        "section": "인생 이야기",
        "type": "textarea",
        "maxLength": 600,
        "title": "학창 시절이나 청년 시절 이야기 하나",
        "hint": _SCENE,
    },
    {
        "id": "M5-03",
        "section": "인생 이야기",
        "type": "textarea",
        "maxLength": 600,
        "title": "배우자와 처음 만났을 때 이야기",
        "hint": "어디서·어떻게·첫인상. 해당 없으면 건너뛰세요.",
    },
    {
        "id": "M5-04",
        "section": "인생 이야기",
        "type": "textarea",
        "maxLength": 600,
        "title": "자녀가 어릴 때 기억나는 장면 하나",
        "hint": _SCENE,
    },
    {
        "id": "M5-05",
        "section": "인생 이야기",
        "type": "textarea",
        "maxLength": 600,
        "title": "일이나 살림에서 가장 애쓰셨던 시절",
        "hint": "언제·무엇 때문에·어떻게 버티셨는지",
    },
    {
        "id": "M5-06",
        "section": "인생 이야기",
        "type": "textarea",
        "maxLength": 600,
        "title": "가장 행복해 보이셨던 때의 장면 하나",
        "placeholder": "예: 손주들이 태어났을 때",
    },
    {
        "id": "M5-07A",
        "section": "인생 이야기",
        "type": "textarea",
        "maxLength": 600,
        "title": "힘드셨던 시기가 있었나요? 말하고 싶은 만큼만요.",
        "hint": "대화할 때 그분은 이 이야기를 먼저 꺼내지 않고, 물어볼 때만 해요.",
    },
    {
        "id": "M5-08",
        "section": "인생 이야기",
        "type": "textarea",
        "maxLength": 600,
        "title": "연세 드신 뒤로 하루를 어떻게 보내셨나요?",
        "hint": "아침 → 낮 → 저녁 순으로 자주 하시던 일",
    },
    {
        "id": "M5-09",
        "section": "인생 이야기",
        "type": "textarea",
        "maxLength": 600,
        "title": "더 남기고 싶은 시절이나 장면이 있나요?",
        "hint": _SCENE,
    },
    # 일상 속 기억(M6, M2-02·M2-03)
    {
        "id": "M6-01",
        "section": "일상 속 기억",
        "type": "phrases",
        "title": "즐겨 해 주시던 음식이 있나요?",
        "placeholder": "예: 김치찌개 — 일요일마다 끓여 주셨다",
    },
    {
        "id": "M6-02",
        "section": "일상 속 기억",
        "type": "textarea",
        "maxLength": 300,
        "title": "음식으로 사람을 챙기시던 방식이 있었나요?",
        "hint": "누구 입맛에 맞춰 무엇을 어떻게 하셨는지 한 장면",
    },
    {
        "id": "M6-03",
        "section": "일상 속 기억",
        "type": "multi",
        "max": 3,
        "other": True,
        "title": "자주 챙겨 주시던 것이 있나요?",
        "hint": "최대 3개",
        "options": ["용돈", "간식·과일", "반찬·음식", "옷·생필품", "편지·문자", "없음"],
    },
    {
        "id": "M6-04",
        "section": "일상 속 기억",
        "type": "phrases",
        "title": "자주 계시던 장소",
        "placeholder": "예: 부엌 — 요리하시던 곳",
    },
    {
        "id": "M6-05",
        "section": "일상 속 기억",
        "type": "phrases",
        "title": "그분을 떠올리게 하는 냄새·소리·물건",
        "placeholder": "예: 참기름 냄새 — 명절마다 짜셨다",
    },
    {
        "id": "M6-07",
        "section": "일상 속 기억",
        "type": "phrases",
        "title": "취미나 좋아하시던 것",
        "placeholder": "예: 화투 — 명절마다",
    },
    {
        "id": "M2-02",
        "section": "일상 속 기억",
        "type": "textarea",
        "maxLength": 300,
        "title": "특별히 걱정하시던 가족이 있나요?",
        "placeholder": "예: 큰아들 — 술 / 손주 — 끼니",
    },
    {
        "id": "M2-03",
        "section": "일상 속 기억",
        "type": "textarea",
        "maxLength": 600,
        "title": "가족 한 사람과의 관계를 잘 보여 주는 기억 하나",
        "hint": "누구와 · 언제·어디서·무슨 일·그때 기분",
    },
    # 이럴 땐(M8-04~M8-10)
    {
        "id": "M8-04",
        "section": "이럴 땐",
        "type": "text",
        "maxLength": 120,
        "title": "가족이 아프다고 하면요?",
        "hint": "대화할 때 그분은 밥 챙겨 먹고 쉬라거나 병원에 가 보라는 말까지만 해요. "
        "약이나 치료 조언은 하지 않아요.",
    },
    {
        "id": "M8-05",
        "section": "이럴 땐",
        "type": "text",
        "maxLength": 120,
        "title": "가족끼리 다퉜다는 걸 아시면요?",
        "hint": _SAID,
    },
    {
        "id": "M8-06",
        "section": "이럴 땐",
        "type": "text",
        "maxLength": 120,
        "title": "공부나 일 걱정을 털어놓으면요?",
        "hint": _SAID,
    },
    {
        "id": "M8-07",
        "section": "이럴 땐",
        "type": "text",
        "maxLength": 120,
        "title": "결혼이나 연애 고민을 이야기하면요?",
        "hint": _SAID,
    },
    {
        "id": "M8-09",
        "section": "이럴 땐",
        "type": "text",
        "maxLength": 120,
        "title": "돈·상속·정치 같은 이야기를 물으면요?",
        "hint": _SAID,
    },
    {
        "id": "M8-10",
        "section": "이럴 땐",
        "type": "text",
        "maxLength": 120,
        "title": '"그때 곁에 못 있어서 죄송해요"처럼 미안해하면요?',
        "hint": _SAID,
    },
    # 마지막 시기(T1) — 돌아가신 분만
    {
        "id": "T1-01",
        "section": "마지막 시기",
        "type": "text",
        "maxLength": 20,
        "when": "deceased",
        "title": "언제 돌아가셨나요?",
        "placeholder": "예: 2024년 3월",
        "hint": "적어 두기만 하고 대화에는 쓰지 않아요. 대화할 때 그분은 돌아가신 일을 "
        "먼저 꺼내지 않아요.",
    },
    {
        "id": "T1-02",
        "section": "마지막 시기",
        "type": "single",
        "sensitive": "health",
        "when": "deceased",
        "title": "어떻게 돌아가셨나요?",
        "hint": "대화할 때 그분은 어떻게 돌아가셨는지 말하지 않아요. 가족이 물으면 담담하게 "
        "받아 주고 안부 이야기로 넘어가요. '말하고 싶지 않음'을 고르면 이 이야기는 아예 "
        "꺼내지 않아요.",
        "options": [
            "오래 투병하시다가",
            "수술·치료 중에",
            "갑작스럽게 (사고·급성 질환)",
            "노환으로 편안하게",
            "말하고 싶지 않음",
        ],
    },
    {
        "id": "T1-03",
        "section": "마지막 시기",
        "type": "textarea",
        "maxLength": 300,
        "when": "deceased",
        "title": "마지막 시기에 곁에 있었던 가족과, 있지 못했던 가족은요?",
        "hint": "곁에 못 있던 가족이 미안해하면, 대화할 때 그분이 그 사정을 알고 받아 줘요.",
    },
    {
        "id": "T1-04",
        "section": "마지막 시기",
        "type": "textarea",
        "maxLength": 600,
        "when": "deceased",
        "title": "그 무렵 이야기를 남기고 싶은 만큼만 들려주세요.",
        "hint": "마지막으로 나눈 대화나 남기신 말을 적어 주세요. 적어 두기만 하고 대화에는 "
        "쓰지 않아요. 그분은 마지막 순간을 자세히 이야기하지 않아요.",
    },
]

# 답 → 기억 조각(앞말, 쓸 범위). 표로 묻던 목록 문항은 줄마다 기억 하나.
MORE_MEMORY_TEXT = {
    "M1-01": ("태어난 해", "자유"),
    "M1-02": ("고향", "자유"),
    "M1-03": ("고향은 어떤 곳이었나", "자유"),
    "M1-06": ("다닌 학교", "자유"),
    "M1-07": ("어떤 일을 하며 살아왔나", "자유"),
    "M1-08": ("가족 안에서의 역할", "자유"),
    "M1-09": ("종교", "물으면만"),
    "M1-10": ("기도하거나 의지하던 습관", "물으면만"),
    "C-08": ("마음을 표현했던 장면", "자유"),
    "M4-03": ("돈에 대해서는", "자유"),
    "M4-06": ("약속·예의에 대해 강조한 것", "자유"),
    "M4-07": ("신앙이나 죽음에 대한 생각", "물으면만"),
    "M5-01": ("어린 시절 살던 집", "자유"),
    "M5-02": ("학창·청년 시절", "자유"),
    "M5-03": ("배우자와 처음 만났을 때", "자유"),
    "M5-04": ("자녀가 어릴 때", "자유"),
    "M5-05": ("가장 애썼던 시절", "자유"),
    "M5-06": ("가장 행복했던 때", "자유"),
    "M5-07A": ("힘들었던 시기", "물으면만"),
    "M5-08": ("말년의 하루", "자유"),
    "M5-09": ("남기고 싶은 장면", "자유"),
    "M6-02": ("음식으로 사람을 챙기던 방식", "자유"),
    "M6-03": ("자주 주던 선물이나 챙김", "자유"),
    "M2-02": ("특별히 걱정하던 가족", "자유"),
    "M2-03": ("가족과의 기억", "자유"),
    # T1-01(돌아가신 때)·T1-04(마지막 무렵)는 기억으로 만들지 않는다. 사후 페르소나는
    # 사망·임종·경위를 입에 올리지 않으므로(조립기 규칙) 찾아 넣어도 어긋나기만 한다.
    "T1-03": ("마지막 시기에 곁에 있던 가족", "물으면만"),
}
MORE_MEMORY_LISTS = {
    "M1-05": "살아온 곳",
    "M6-01": "대표 음식",
    "M6-04": "자주 계시던 장소",
    "M6-05": "떠올리게 하는 것",
    "M6-07": "취미",
}
# 만들 때 질문 M6-08(그분 하면 떠오르는 것 + 까닭) → 기억 하나. 기억은 그분이 1인칭으로 읽으니
# '그분'이라 쓰지 않는다. 예: "가족이 나를 떠올리면 생각나는 것: 된장찌개 — 갈 때마다 끓여 주셨어요"
M6_08_LEAD = "가족이 나를 떠올리면 생각나는 것"
M6_08_TAG = "떠오르는 것"


_BY_ID = {q["id"]: q for q in [*QUESTIONS, *MORE_QUESTIONS]}
# 한 문항은 한 목록에만 둔다(겹치면 뒤의 정의가 앞을 덮고, 두 화면이 같은 답을 서로 덮어쓴다)
assert len(_BY_ID) == len(QUESTIONS) + len(MORE_QUESTIONS), "QUESTIONS·MORE_QUESTIONS id 가 겹친다"


# ────────────────────────── 나 자신(개편 전 차례 그대로) ──────────────────────────
# 나 자신은 2026-10-21 개편 대상이 아니다(대표 지시 1 '적용 대상'·'나 자신 경로가 그대로인지').
# 만들 때 질문과 기억 더 채우기를 개편 전 묶음·차례·안내 그대로 보인다:
# - 언제 무렵·고향·하신 일(P1-05·M1-02·M1-07)과 사투리·말끝(M3-05·M3-07)은 만들 때 묻는다.
# - 첫마디·마지막 말(M3-01·M3-02)과 마음을 표현했던 장면(C-08)은 기억 더 채우기에서 묻는다.
# - 개편 때 새로 생긴 문항(떠오르는 것 M6-08·그 까닭, 함께한 장면 둘째 M7-01-2)은 묻지 않는다.
# - '기억이 안 나요'·'모름' 단추(skipOption)도 없다(개편 전에 없었다).
# 문구는 question_title(titleSelf), 보기 글·안내는 placeholderSelf·hintSelf 가 있으면 그것이다.
# 필수(LEGACY_REQUIRED)와 이미 저장한 답은 그대로다(validate_answers 는 모든 문항을 받는다).
SELF_QUESTION_SECTIONS = [
    {"id": "가족", "lead": "누구와 이야기하는지 알면 부르는 말부터 달라져요."},
    {"id": "그분에 대해", "lead": "'고향이 어디셨지?' 하고 물어도 대답할 수 있게요."},
    {
        "id": "말투",
        "lead": "실제 하시던 말 그대로가 가장 좋아요. 녹음에서 찾은 말은 누르기만 하면 돼요.",
    },
    {"id": "성격", "lead": "가족이 기억하는 그분 모습 그대로 알려 주세요."},
    {"id": "이럴 땐", "lead": "이런 말을 들으면 뭐라고 하셨을지 한 줄이면 충분해요."},
    {
        "id": "조심할 이야기",
        "lead": "마음 아픈 이야기를 어디까지 할지 정해 주세요. 정해 주신 대로 지켜요.",
    },
    {"id": "함께한 기억"},
]
# 나 자신의 만들 때 질문(묶음, 문항 차례). 나 자신에게 묻지 않는 문항(when=notSelf)도 그 자리에
# 둔다(정보 고치기에서 관계를 바꾸는 중에 화면이 기본 정보로 거른다 — questions_for 와 같다).
SELF_QUESTION_LAYOUT: list[tuple[str, list[str]]] = [
    ("가족", ["M2-01", "P1-07A", "M2-01-self", "M2-01-level", "M2-01-tone"]),
    ("그분에 대해", ["P1-05", "M1-02", "M1-07"]),
    ("말투", ["C-01", "C-02", "C-09", "M3-07", "M8-11", "M3-05"]),
    ("성격", ["C-05", "M4-01", "C-03", "C-04", "C-07", "C-06"]),
    ("이럴 땐", ["M8-01", "M8-02", "M8-03", "M8-08"]),
    ("조심할 이야기", ["C-10", *[q["id"] for q in _M9_ROWS], "M9-03"]),
    ("함께한 기억", ["M7-01"]),
]
# 나 자신의 기억 더 채우기: MORE_QUESTIONS 차례에서 만들 때 묻는 것과 SELF_HIDDEN 을 빼고, 만들 때
# 질문에서 온 문항을 개편 전 자리(그 문항 앞)와 묶음에 넣는다.
SELF_MORE_INSERT = {
    "M3-03": [("M3-01", "말투·표현"), ("M3-02", "말투·표현")],
    "M4-02": [("C-08", "성격·가치관")],
}
SELF_HIDDEN = frozenset({"M6-08", "M6-08-why", "M7-01-2"})
# 기억 더 채우기 묶음 안내(개편 전 문구)
_SELF_MORE_LEADS = {
    "기본 정보": "태어난 해, 살아오신 곳, 다니신 학교 같은 기본 이야기",
    "말투·표현": "전화 받을 때 첫마디, 끊을 때 마지막 말, 화나셨을 때 말투",
}
SELF_MORE_MODULES = [{**m, "lead": _SELF_MORE_LEADS.get(m["id"], m["lead"])} for m in MORE_MODULES]

_SELF_CREATE = [(qid, section) for section, ids in SELF_QUESTION_LAYOUT for qid in ids]
_SELF_MORE: list[tuple[str, str]] = []
for _q in MORE_QUESTIONS:
    _SELF_MORE += SELF_MORE_INSERT.get(_q["id"], [])
    if _q["id"] not in {qid for qid, _ in _SELF_CREATE} | SELF_HIDDEN:
        _SELF_MORE.append((_q["id"], _q["section"]))
# 모든 문항이 나 자신의 두 화면 가운데 한 곳에 한 번만 있다(SELF_HIDDEN 은 어디에도 없다)
_self_ids = [qid for qid, _ in [*_SELF_CREATE, *_SELF_MORE]]
assert len(_self_ids) == len(set(_self_ids)), "나 자신 질문 차례에 같은 문항이 두 번 있다"
assert set(_self_ids) | SELF_HIDDEN == set(_BY_ID) and not set(_self_ids) & SELF_HIDDEN, (
    "나 자신 질문 차례가 QUESTIONS·MORE_QUESTIONS 와 맞지 않는다"
)
assert {s for _, s in _SELF_CREATE} == {s["id"] for s in SELF_QUESTION_SECTIONS}

# 민감한 정보(종교·건강)를 묻는 질문. 그 대화 상대에 sensitive_{범주} 동의가 있어야 답을 받는다.
# 핵심 질문(QUESTIONS)의 '조심할 이야기'(C-10·M9 등급·M9-03)는 그분 정보가 아니라 대화 규칙이라
# 넣지 않는다.
SENSITIVE: dict[str, list[str]] = {}
for _q in [*QUESTIONS, *MORE_QUESTIONS]:
    if _q.get("sensitive"):
        SENSITIVE.setdefault(_q["sensitive"], []).append(_q["id"])


def sensitive_keys(category: str) -> list[str]:
    """그 범주의 답이 저장되는 칸(문항 번호와 덧붙인 설명 칸)."""
    return [k for qid in SENSITIVE.get(category, []) for k in (qid, f"{qid}:detail")]


ASKED_ONLY = "물으면만"


def memory_title(tag: str, scope: str | None) -> str:
    """질문 답으로 만든 기억(source=INTAKE)의 제목. '물으면만' 기억은 제목에 표시해 둔다."""
    return f"{tag} ({ASKED_ONLY})" if scope == ASKED_ONLY else tag


def sensitive_memory_titles(category: str) -> list[str]:
    """그 민감 범주(종교·건강) 답으로 만든 기억의 제목. 동의를 거두면 이 기억을 바로 지운다."""
    titles = []
    for qid in SENSITIVE.get(category, []):
        if qid in MORE_MEMORY_TEXT:
            lead, scope = MORE_MEMORY_TEXT[qid]
            titles.append(memory_title(lead, scope))
        if qid in MORE_MEMORY_LISTS:
            titles.append(MORE_MEMORY_LISTS[qid])
    return titles


def _clean(value, limit: int = 600) -> str:
    return str(value or "").strip()[:limit]


def _validate_self(data: dict) -> dict:
    """나 자신: 이름 하나만 받는다. 상태는 살아 계심, 부르는 말은 '나'."""
    name = _clean(data.get("name"), 20) or _clean(data.get("myName"), 20)
    if not name:
        raise IntakeError("이름을 적어 주세요.")
    return {
        "name": name,
        "relation": SELF_RELATION,
        "addressTerm": _clean(data.get("addressTerm"), 15) or "나",
        "status": "alive",
        "myName": _clean(data.get("myName"), 20) or name,
        "myRelation": None,
        "calledMe": None,
    }


def is_self(basics: dict) -> bool:
    return (basics or {}).get("relation") == SELF_RELATION


# ────────────────────────── 만들 때 질문 차례(개편 전·후) ──────────────────────────


def with_version(basics: dict) -> dict:
    """새로 만드는 대화 상대의 basics. 가족·고인이면 개편 뒤 질문 차례(INTAKE_VERSION)를 적는다."""
    if is_self(basics):
        return basics
    return {**basics, "intakeVersion": INTAKE_VERSION}


def keep_version(basics: dict, old: dict | None) -> dict:
    """정보 고치기: 화면이 보낸 기본 정보에는 질문 차례가 없으니(받지 않는다) 원래 값을 잇는다."""
    version = (old or {}).get("intakeVersion")
    return {**basics, "intakeVersion": version} if version is not None else basics


def is_v2(basics: dict | None) -> bool:
    """개편 뒤 필수 질문을 받는 대화 상대인가(가족·고인, intakeVersion 2). 나 자신은 늘 아니다."""
    b = basics or {}
    return b.get("intakeVersion") == INTAKE_VERSION and not is_self(b)


def asks(q: dict, basics: dict | None) -> bool:
    """이 대화 상대에게 묻는 문항인가(화면 AnswersForm.asks 와 같은 규칙).

    when=notSelf 는 나 자신에게, when=deceased 는 돌아가신 분이 아니면 묻지 않는다.
    """
    b = basics or {}
    when = q.get("when")
    if when == "notSelf":
        return not is_self(b)
    if when == "deceased":
        return b.get("status") == "deceased"
    return True


def required_ids(basics: dict | None) -> list[str]:
    """이 대화 상대가 꼭 답할 질문(질문 순서대로). 화면에도 대화 상대 상세로 알려 준다.

    개편 뒤 가족·고인은 PART 0 의 필수 질문, 예전 대화 상대·나 자신은 LEGACY_REQUIRED.
    기본 정보가 대신하는 질문(skipIfBasics, P1-07A ← calledMe)은 그 칸이 차 있으면 뺀다.
    """
    if not is_v2(basics):
        return list(LEGACY_REQUIRED)
    b = basics or {}
    return [
        q["id"]
        for q in QUESTIONS
        if q.get("required")
        and asks(q, b)
        and not (q.get("skipIfBasics") and b.get(q["skipIfBasics"]))
    ]


def answer_defaults(basics: dict | None) -> dict:
    """개편 뒤 대화 상대에 미리 채워 두는 답(M9-01 기본 등급). 예전 대화 상대는 없다."""
    if not is_v2(basics):
        return {}
    out = {}
    for key, grade in M9_DEFAULTS.items():
        qid = f"M9-01-{key}"
        if asks(_BY_ID[qid], basics):
            out[qid] = grade
    return out


def with_defaults(basics: dict | None, answers: dict | None) -> dict:
    """답에 미리 채워 두는 값을 더한 것(이미 고른 답이 이긴다). 대화 상대 상세가 보여 준다."""
    return {**answer_defaults(basics), **(answers or {})}


def question_title(q: dict, basics: dict | None) -> str:
    """그분 상태에 맞는 질문 문구: 나 자신은 titleSelf(없으면 title — 개편 전 문구 그대로),
    살아 계신 분 titleLiving, 그 밖은 title."""
    b = basics or {}
    if is_self(b):
        return q.get("titleSelf") or q["title"]
    if b.get("status") in ("alive", "unable") and q.get("titleLiving"):
        return q["titleLiving"]
    return q["title"]


def _fitted(q: dict, basics: dict | None, section: str | None = None) -> dict:
    """한 문항의 문구를 이 대화 상대에 맞춘 것. 나 자신은 개편 전 모양(묶음·보기 글·안내,
    '기억이 안 나요'·'모름' 단추 없음 — SELF_QUESTION_LAYOUT 위 설명)."""
    item = {**q, "title": question_title(q, basics)}
    if is_self(basics or {}):
        for key in ("placeholder", "hint"):
            if q.get(f"{key}Self"):
                item[key] = q[f"{key}Self"]
        item.pop("skipOption", None)
        if section:
            item["section"] = section
    return item


def questions_for(basics: dict | None) -> list[dict]:
    """대화 상대 상세에 싣는 만들 때 질문: 이 대화 상대의 필수·문구·미리 채울 답을 맞춘 것.

    required: 개편 뒤 가족·고인은 QUESTIONS 의 required, 예전 대화 상대·나 자신은 LEGACY_REQUIRED.
    기본 정보가 대신하는 질문(skipIfBasics, P1-07A ← calledMe)도 필수로 둔다 — 화면은 그 칸이 차
    있으면 묻지 않고, 정보 고치기에서 비우면 다시 꼭 묻는다(required_ids 는 지금 기본 정보로 뺀다).
    title: question_title. default: 개편 뒤 대화 상대에 미리 채워 둘 등급(M9_DEFAULTS) — 정보
    고치기에서 그분 상태를 바꿔 새로 묻게 된 문항도 화면이 채운다. 묻지 않는 문항(when)은 화면이
    기본 정보로 거른다(고치는 중에 상태가 바뀔 수 있어 여기서 빼지 않는다).
    나 자신은 개편 전 문항·묶음·차례 그대로다(SELF_QUESTION_LAYOUT).
    """
    v2 = is_v2(basics)
    if is_self(basics or {}):
        layout = [(_BY_ID[qid], section) for qid, section in _SELF_CREATE]
    else:
        layout = [(q, None) for q in QUESTIONS]
    out = []
    for q, section in layout:
        item = {
            **_fitted(q, basics, section),
            "required": bool(q.get("required")) if v2 else q["id"] in LEGACY_REQUIRED,
        }
        key = q["id"].removeprefix("M9-01-") if q["id"].startswith("M9-01-") else None
        if v2 and key in M9_DEFAULTS:
            item["default"] = M9_DEFAULTS[key]
        out.append(item)
    return out


def more_questions_for(basics: dict | None) -> list[dict]:
    """대화 상대 상세에 싣는 기억 더 채우기 질문(모두 선택): 문구를 이 대화 상대에 맞춘 것.

    가족·고인(개편 전·후)은 MORE_QUESTIONS 그대로다(만들 때 질문과 id 가 겹치지 않아 같은 답이 두
    화면에 나오거나 빠지지 않는다). 나 자신은 개편 전 목록이다(만들 때 질문의 나머지 — _SELF_MORE).
    문구는 question_title(살아 계신 분 titleLiving 등)이다. 묻지 않는 문항(when=deceased)은 화면이
    기본 정보로 거른다.
    """
    if is_self(basics or {}):
        return [
            {**_fitted(_BY_ID[qid], basics, section), "required": False}
            for qid, section in _SELF_MORE
        ]
    return [_fitted(q, basics) for q in MORE_QUESTIONS]


def sections_for(basics: dict | None) -> list[dict]:
    """만들 때 질문의 묶음과 묶음 안내(나 자신은 개편 전 묶음)."""
    return SELF_QUESTION_SECTIONS if is_self(basics or {}) else QUESTION_SECTIONS


def more_modules_for(basics: dict | None) -> list[dict]:
    """기억 더 채우기 묶음과 안내(나 자신은 개편 전 안내)."""
    return SELF_MORE_MODULES if is_self(basics or {}) else MORE_MODULES


def check_required(answers: dict, basics: dict | None) -> None:
    """꼭 답할 질문이 비었으면 IntakeError. 표시 값('기억이 안 나요' 등)은 답한 것으로 센다."""
    for qid in required_ids(basics):
        if not answers.get(qid):
            title = question_title(_BY_ID[qid], basics)
            raise IntakeError(f"꼭 답해 주셔야 하는 질문이 남았어요: {title}")


# ────────────────────────── 표시 값('기억이 안 나요' 등) ──────────────────────────


def _is_sentinel(value) -> bool:
    return isinstance(value, str) and value.strip() in SENTINELS


# 기억 더 채우기 선택지의 '모름'(M1-06 학교·M1-09 종교·M4-03 돈)과 손으로 적은 '모름'. 저장은 그대로
# 하지만(화면이 고른 선택지를 보인다) AI 로는 빈 답이다 — '다닌 학교: 모름' 같은 기억이 되면
# 그분이 자기 일을 모른다고 말하게 된다.
DONT_KNOW = "모름"


def _is_blank_for_ai(value) -> bool:
    return isinstance(value, str) and (value.strip() in SENTINELS or value.strip() == DONT_KNOW)


def for_ai(answers: dict | None) -> dict:
    """AI·프롬프트·기억으로 보낼 답: 표시 값('기억이 안 나요'·'따로 없었어요'·'모름' 단추)과
    선택지·글로 고른 '모름'을 뺀 것.

    그 말은 그분 입으로 나오면 안 된다(돌아가신 분은 '기억이 안 난다'고 하지 않는다). 목록의 항목,
    가족 표의 칸에 든 것도 뺀다. 비게 된 답은 아예 없앤다. 이미 저장한 답도 다시 만들 때 걸러진다.
    """
    out: dict = {}
    for key, value in (answers or {}).items():
        if _is_blank_for_ai(value):
            continue
        if isinstance(value, list):
            items = []
            for item in value:
                if _is_blank_for_ai(item):
                    continue
                if isinstance(item, dict):
                    item = {k: v for k, v in item.items() if not _is_blank_for_ai(v)}
                items.append(item)
            if not items and value:
                continue
            value = items
        out[key] = value
    return out


def called_me(basics: dict, answers: dict | None) -> str | None:
    """그분이 나를 부르던 말: 기본 정보(calledMe)가 먼저, 없으면 만들 때 질문 P1-07A 의 답."""
    if basics.get("calledMe"):
        return basics["calledMe"]
    value = (answers or {}).get("P1-07A")
    return value if isinstance(value, str) and value and not _is_sentinel(value) else None


# 기본 정보 검사 글(2026-10-22 법률 자문 — unable 제외·나 자신 바꾸기·만 19세 미만)
STATUS_REQUIRED = "지금 그분이 살아 계신지 골라 주세요."
STATUS_LOCKED = "이 대화 상대의 상태는 지금 바꿀 수 없어요. 고객센터로 알려 주세요."
NOT_SELF = "다른 분의 대화 상대를 '나 자신'으로 바꿀 수 없어요. 새로 만들어 주세요."
MINOR_REQUIRED = "그분이 만 19세 미만인지 골라 주세요."
MINOR_BIRTH = "아이의 태어난 해와 달을 골라 주세요."
MINOR_NOT_MINOR = "만 19세 미만이 아니면 '아니요'를 골라 주세요."
MINOR_RELATION = (
    "아이의 대화 상대는 부모님만 만들 수 있어요. 그분이 나의 아들인지 딸인지 골라 주세요."
)
MINOR_MY_RELATION = (
    "아이의 대화 상대는 부모님만 만들 수 있어요. "
    "'나는 그분의' 칸에서 어머니나 아버지를 골라 주세요."
)
GUARDIAN_BASIS_REQUIRED = "다른 쪽 부모님의 동의를 받았는지, 혼자 친권을 가지고 계신지 골라 주세요."
GUARDIAN_ATTEST_REQUIRED = "아이의 법정대리인(부모)인지 확인해 주세요."
# 대표 답(2026-10-23 #1) 글 그대로
UNDER14_CLOSED = (
    "만 14세 미만 아이의 대화 상대는 아직 만들 수 없어요(법정대리인 확인 문자를 준비하고 있어요)."
)
MINOR_FIXED = (
    "아이의 태어난 해·달과 만 19세 미만 여부는 고칠 수 없어요. "
    "잘못 적었으면 고객센터로 알려 주세요."
)


def validate_basics(data: dict, *, previous: dict | None = None, today: date | None = None) -> dict:
    """기본 정보를 손으로 본다. 정해진 칸만 새 dict 로 돌려준다(틀리면 IntakeError).

    previous 는 고칠 때의 원래 basics(새로 만들 때 None). 고칠 때는
    - 예전에 고른 '대화가 어려우신 분'(unable)은 그대로만 둔다(다른 상태로도, 새로도 못 고른다).
    - 다른 분의 대화 상대를 '나 자신'으로 바꾸지 못한다(그분 동의 녹음을 피하는 길을 닫는다).
    - 만 19세 미만 칸은 화면이 보내지 않으면 원래 값을 잇는다(빠지면 아이 안전 규칙과 가족 공유
      제한이 풀린다). 답한 미성년 여부·태어난 해와 달은 고치지 못한다.
    살아 계신 분(나 자신 아님)을 새로 만들 때는 만 19세 미만인지 꼭 받는다(minor_fields).
    """
    prev = previous or {}
    relation = _clean(data.get("relation"), 20)
    if relation == SELF_RELATION:
        if previous is not None and prev.get("relation") and not is_self(prev):
            raise IntakeError(NOT_SELF)
        return _validate_self(data)
    name = _clean(data.get("name"), 20)
    if not name:
        raise IntakeError("그분 성함을 적어 주세요.")
    if not relation:
        raise IntakeError("그분이 나에게 어떤 분인지 골라 주세요.")
    status = data.get("status")
    if prev.get("status") == "unable":
        if status != "unable":
            raise IntakeError(STATUS_LOCKED)
    elif status not in STATUSES:
        raise IntakeError(STATUS_REQUIRED)
    my_name = _clean(data.get("myName"), 20)
    if not my_name:
        raise IntakeError("내 이름을 적어 주세요.")
    my_relation = _clean(data.get("myRelation"), 20) or None
    basics = {
        "name": name,
        "relation": relation,
        "addressTerm": _clean(data.get("addressTerm"), 15) or relation,
        "status": status,
        "myName": my_name,
        "myRelation": my_relation,
        "calledMe": _clean(data.get("calledMe"), 15) or None,
    }
    return basics | _minor_basics(data, previous, basics, today)


def _minor_basics(data: dict, previous: dict | None, basics: dict, today: date | None) -> dict:
    """basics 에 더할 만 19세 미만 칸(SPEC §6.2 표)."""
    prev = previous or {}
    if previous is not None and "minor" in prev:
        # 이미 답한 대화 상대: 미성년 여부·태어난 해와 달은 그대로(고치려 하면 막는다)
        kept = {key: prev[key] for key in MINOR_KEYS if key in prev}
        asked = data.get("minor")
        if asked is not None and asked != prev["minor"]:
            raise IntakeError(MINOR_FIXED)
        if prev["minor"] is not True:
            return kept
        birth = data.get("birthYm")
        if birth not in (None, "") and birth != prev.get("birthYm"):
            raise IntakeError(MINOR_FIXED)
        if prev.get("notGuardian"):
            # 부모가 아닌 분이 예전에 만든 아이(PUT /minor): 관계를 막지 않고 법정대리인 칸은
            # 받지 않는다
            return kept
        if basics["status"] == "alive":
            _check_parent(basics["relation"], basics["myRelation"])
        basis = data.get("guardianBasis") or prev.get("guardianBasis")
        if basis not in GUARDIAN_BASES:
            raise IntakeError(GUARDIAN_BASIS_REQUIRED)
        return kept | {"guardianBasis": basis}
    if previous is not None or basics["status"] != "alive":
        # 돌아가신 분·예전 대화 상대(미성년 키 없음)는 묻지 않는다. 예전 살아 계신 분은 따로
        # PUT /personas/{id}/minor 로 답한다(법정대리인 확인 기록·가족 공유 범위를 함께 정한다)
        return {}
    minor = data.get("minor")
    if not isinstance(minor, bool):
        raise IntakeError(MINOR_REQUIRED)
    if not minor:
        return {"minor": False}
    return minor_fields(data, basics["relation"], basics["myRelation"], today=today)


def _check_parent(relation: str | None, my_relation: str | None) -> None:
    if relation not in MINOR_RELATIONS:
        raise IntakeError(MINOR_RELATION)
    if my_relation not in MINOR_MY_RELATIONS:
        raise IntakeError(MINOR_MY_RELATION)


def minor_birth(value, today: date | None = None) -> tuple[str, int]:
    """아이의 태어난 해와 달("YYYY-MM")과 지금 만 나이. 형식이 틀리거나 앞날이면 MINOR_BIRTH,
    만 19세 이상이면 MINOR_NOT_MINOR. 이번 달에 태어났으면 나이는 0 으로 본다(ages 는 다음 달
    1일생으로 본다)."""
    today = today or ages.today_kst()
    text = value.strip() if isinstance(value, str) else ""
    try:
        year, month = ages.parse_ym(text)
    except ValueError as error:
        raise IntakeError(MINOR_BIRTH) from error
    if (year, month) > (today.year, today.month):
        raise IntakeError(MINOR_BIRTH)
    age = max(ages.age_on(text, today), 0)
    if age >= 19:
        raise IntakeError(MINOR_NOT_MINOR)
    return text, age


def minor_fields(
    data: dict,
    relation: str | None,
    my_relation: str | None,
    *,
    today: date | None = None,
    creating: bool = True,
) -> dict:
    """'만 19세 미만'이라고 답한 아이 대화 상대의 칸: 태어난 해와 달 · 부모만 · 부모 양쪽 ·
    법정대리인 확인(guardianAttest — 요청에만 있고 basics 에 두지 않는다). 새로 만들 때(creating)는
    만 14세 미만이면 확인 문자를 보낼 수 있어야 한다(sms.under14_open — 대표 답 2026-10-23: 지금은
    막힘).
    예전 대화 상대가 답할 때(PUT /minor)는 creating=False — 이미 있는 대화 상대라 사실대로 답할 길을
    막지 않는다(처리·목소리는 법정대리인 확인 전까지 게이트가 막는다)."""
    birth, age = minor_birth(data.get("birthYm"), today)
    if creating and age < 14 and not sms.under14_open():
        raise IntakeError(UNDER14_CLOSED)
    _check_parent(relation, my_relation)
    basis = data.get("guardianBasis")
    if basis not in GUARDIAN_BASES:
        raise IntakeError(GUARDIAN_BASIS_REQUIRED)
    if data.get("guardianAttest") is not True:
        raise IntakeError(GUARDIAN_ATTEST_REQUIRED)
    return {"minor": True, "birthYm": birth, "guardianBasis": basis}


def _family_rows(value, limit: int = FAMILY_MAX) -> list[dict]:
    """가족 표 줄들. 이름 없는 줄·모르는 칸은 버리고, 같은 이름은 한 번만. 오류는 내지 않는다."""
    rows: list[dict] = []
    seen: set[str] = set()
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        row = {k: _clean(item.get(k), n) for k, n in FAMILY_FIELDS.items()}
        # '모름' 같은 표시 값은 빈 칸으로 본다(이름이 표시 값이면 그 줄은 버린다)
        row = {k: v for k, v in row.items() if v and v not in SENTINELS}
        if item.get("status") in FAMILY_STATUSES:
            row["status"] = item["status"]
        name = row.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def validate_answers(data: dict, *, final: bool, basics: dict | None = None) -> dict:
    """모르는 칸은 버리고, 최종 제출이면 그 대화 상대의 필수 칸을 확인한다.

    basics 는 그 대화 상대의 기본 정보다. 필수 질문(개편 전·후, 나 자신)과 미리 채울 답을 정한다.
    없으면 예전 필수 넷(LEGACY_REQUIRED)으로 본다. '기억이 안 나요'·'따로 없었어요'·'모름' 단추가
    있는 문항(skipOption)만 그 표시 값을 받고, 다른 문항에 온 표시 값은 빈 답으로 버린다.
    """
    out: dict = {}
    for qid, q in _BY_ID.items():
        value = data.get(qid)
        limit = q.get("maxLength", 300)
        if q["type"] == "family":
            rows = _family_rows(value, q.get("max", FAMILY_MAX))
            if rows:
                out[qid] = rows
        elif q["type"] in ("multi", "phrases"):
            items = [_clean(v, 80) for v in (value or []) if _clean(v, 80)]
            items = list(dict.fromkeys(i for i in items if i not in SENTINELS))
            if q.get("max"):
                items = items[: q["max"]]
            if q["type"] == "phrases":
                items = items[:10]
            if items:
                out[qid] = items
        else:
            # 표시 값은 칸 길이(예: 태어난 해 10자)와 상관없이 그대로 둔다(잘리면 표시 값이 아니다)
            raw = _clean(value)
            if raw in SENTINELS:
                text = raw if raw == (q.get("skipOption") or {}).get("value") else ""
            else:
                text = _clean(value, limit)
            if text:
                out[qid] = text
        detail = _clean(data.get(f"{qid}:detail"), 80)
        if q.get("detail") and detail and detail not in SENTINELS:
            out[f"{qid}:detail"] = detail
    if basics is not None:
        out = with_defaults(basics, out)
    if final:
        check_required(out, basics)
    return out


def family_rows(basics: dict, answers: dict | None) -> list[dict]:
    """가족 표 줄 가운데 나(회원)·그분 자신이 아닌 사람만(이름이 같으면 나로 본다).

    '모름' 같은 표시 값이 든 칸은 빈 칸이다(_family_rows).
    """
    me = {basics.get("myName"), basics.get("name")}
    return [
        row
        for row in _family_rows((answers or {}).get("M2-01"))
        if row["name"] not in me
        and row["name"] not in _SELF_WORDS
        and row.get("relation") not in _SELF_WORDS
    ]


def subject_context(basics: dict, answers: dict | None = None) -> dict:
    answers = for_ai(answers)
    me = {
        "name": basics["myName"],
        # 나 자신이면 대화하는 사람이 곧 그분이다
        "relationToSubject": "본인" if is_self(basics) else basics.get("myRelation"),
        "addressTerms": [t for t in [called_me(basics, answers)] if t],
    }
    others = family_rows(basics, answers)
    return {
        "subject": {"name": basics["name"], "addressTerm": basics.get("addressTerm")},
        "familyMembers": [
            me,
            *[
                {
                    "name": row["name"],
                    "relationToSubject": row.get("relation"),
                    # 그분이 이 사람을 부르던 말(받아쓰기·사람 찾기에 쓴다)
                    "addressTerms": [t for t in [row.get("calledBy")] if t],
                }
                for row in others
            ],
        ],
        "glossaryTerms": sorted(
            {basics["name"], basics["myName"], *[row["name"] for row in others]}
        ),
    }


def build_intake(basics: dict, answers: dict) -> dict:
    """AI 서버 /v1/persona/assembly 의 subjectContext·intakeContext.

    칸 이름은 AI 레포 pipeline/s0_intake/b_split_intake.to_intake 와 같다.
    '기억이 안 나요' 같은 표시 값은 빈 답으로 본다(for_ai — 그 말이 AI 로 가지 않게).
    """
    a = for_ai(answers)
    dialect = a.get("M3-05")
    detail = a.get("M3-05:detail")
    if dialect and detail and dialect != "없음(표준어)":
        dialect = f"{dialect} ({detail})"

    situational = []
    for qid, situation in (
        ("M8-01", '"보고 싶어요"라고 하면'),
        ("M8-02", '"너무 힘들어요"라고 하면'),
        ("M8-03", '"좋은 일이 생겼어요!"라고 하면'),
        ("M8-04", "가족이 아프다고 하면"),
        ("M8-05", "가족끼리 다퉜다는 걸 알면"),
        ("M8-06", "공부나 일 걱정을 털어놓으면"),
        ("M8-07", "결혼이나 연애 고민을 이야기하면"),
        # 공통 규칙(AI prompts/rules/unknown_facts.md)의 고정 예보다 이 말이 앞서게 상황에 적어 둔다
        ("M8-08", "모르는 것을 물으면 (모른다고 할 때는 늘 이 말투로)"),
        ("M8-09", "돈·상속·정치 같은 주제를 물으면"),
        ("M8-10", "사용자가 미안함이나 죄책감을 말하면"),
    ):
        if not a.get(qid):
            continue
        # 고인 대화 상대는 잊었다고 하지 않는다(잠금): '기억이 안 나'·'까먹었다'가 든 M8-08 은 빼고
        # 공통 규칙의 말을 쓴다. 살아 계신 분은 그대로다
        if qid == "M8-08" and basics["status"] == "deceased" and forgetting.says_forgot(a[qid]):
            continue
        situational.append({"situation": situation, "response": a[qid]})
    for line in a.get("M8-11", []):
        situational.append({"situation": "먼저 말을 걸 때", "response": line})

    taboo = [{"topic": t, "grade": "금지", "sourceQuestion": "C-10"} for t in a.get("C-10", [])]
    for key, topic in M9_TOPICS.items():
        # 그분 상태에 묻지 않는 줄(살아 계신 분의 임종 줄 — 상태를 바꾸기 전 답·미리 채운 값)은 뺀다
        if a.get(f"M9-01-{key}") and asks(_BY_ID[f"M9-01-{key}"], basics):
            taboo.append({"topic": topic, "grade": a[f"M9-01-{key}"], "sourceQuestion": "M9-01"})
    for topic in a.get("M9-03", []):
        taboo.append({"topic": topic, "grade": "물으면만", "sourceQuestion": "M9-03"})
    if basics["status"] == "deceased" and a.get("T1-02") == "말하고 싶지 않음":
        taboo.append(
            {"topic": "돌아가신 일과 마지막 무렵", "grade": "금지", "sourceQuestion": "T1-02"}
        )

    interjections = [t.strip() for t in re.split(r"[/,·]", a.get("M3-04") or "") if t.strip()]
    examples = [
        {"text": t, "inferred": False, "sourceQuestion": qid}
        for qid in ("C-09", "M4-04")
        for t in a.get(qid, [])
    ]

    me = {
        "name": basics["myName"],
        "relation": basics.get("myRelation"),
        # 기본 정보에 없으면 만들 때 질문 P1-07A(그분은 나를 뭐라고 부르셨어요?)의 답
        "addressTermTo": called_me(basics, a),
        "addressTermFrom": basics.get("addressTerm"),
        "selfReference": a.get("M2-01-self"),
        "speechLevel": a.get("M2-01-level"),
        "tone": a.get("M2-01-tone"),
        "talksWithPersona": True,
        # 지금 대화하는 사람은 늘 계정 주인이다(가족 초대는 아직 없다)
        "isListener": True,
    }
    if is_self(basics):
        # 대화하는 사람이 이 대화 상대 자신(계정 주인)이다. 따로 부르는 가족으로 그리지 않는다.
        me = {
            "name": basics["myName"],
            "relation": "본인(이 대화 상대 자신)",
            "selfReference": "나",
            "tone": a.get("M2-01-tone"),
            "talksWithPersona": True,
            "isListener": True,
        }
    me = {k: v for k, v in me.items() if v is not None}
    family = [me]
    for row in family_rows(basics, a):
        member = {
            "name": row["name"],
            "relation": row.get("relation"),
            "addressTermTo": row.get("calledBy"),
            # 나 자신이면 relation 이 이미 '나의 ○○'라 따로 적지 않는다
            "listenerCalls": None if is_self(basics) else row.get("toMe"),
            "deceased": True if row.get("status") == "deceased" else None,
            "note": row.get("note"),
        }
        family.append({k: v for k, v in member.items() if v is not None})
    if len(family) > 1:
        # 가족을 적었으면 살아 계신 가족의 사생활은 꺼내지 않는다(Persona Intake M9-01 '금지')
        taboo.append(
            {
                "topic": "다른 가족의 사생활",
                "grade": "금지",
                "note": "살아 계신 가족의 주소·직장·학교·건강을 먼저 꺼내거나 지어내 말하지 않는다"
                " — 사용자가 가족 걱정을 털어놓으면 들어 준다",
                "sourceQuestion": "M2-01",
            }
        )
    return {
        "_comment": "TalkTo 웹에서 받은 핵심 질문 답으로 만든 것.",
        "subjectContext": subject_context(basics, a),
        "intakeContext": {
            "basicProfile": {
                "status": "사망" if basics["status"] == "deceased" else "생존",
                "relation": "본인" if is_self(basics) else basics["relation"],
                # 한 문장 소개(C-05)는 개편 뒤 선택이라 비어 있을 수 있다(조립기는 없으면 뺀다)
                "oneLine": a.get("C-05"),
                # 아래는 선택 질문(C-06 은 만들 때, P1-05·T1-02 는 '기억 더 채우기')
                "period": a.get("P1-05"),
                "introduction": a.get("C-06"),
                "deathContext": a.get("T1-02") if basics["status"] == "deceased" else None,
                # 만 19세 미만 아이(살아 계신 분만)면 AI 가 아이 안전 규칙을 넣는다. 태어난 해와
                # 달은 보내지 않는다(이 한 칸만)
                "minor": ages.is_minor_now(basics, ages.today_kst()),
            },
            "audience": "나와 내가 초대한 사람만",
            "speechStyle": {
                "length": a.get("C-01"),
                "manner": a.get("C-02"),
                "dialect": dialect,
                "whenAngry": a.get("M3-03"),
                "interjections": interjections,
                "avoidStyles": a.get("M3-06", []),
                "sentenceEndings": a.get("M3-07", []),
                "callOpen": a.get("M3-01"),
                "callClose": a.get("M3-02"),
            },
            "speechExamples": examples,
            "personality": {
                "oneLine": a.get("C-05"),
                "introduction": a.get("C-06"),
                "traits": a.get("M4-01", []),
                "values": a.get("M4-02", []),
                "affectionStyle": a.get("C-07", []),
                "comfortStyle": a.get("C-03"),
                "adviceStyle": a.get("C-04", []),
                "conflictStyle": a.get("M4-05"),
            },
            "familyMap": family,
            "situationalReactions": situational,
            "tabooTopics": taboo,
        },
    }


def build_intake_memories(basics: dict, answers: dict) -> list[dict]:
    """B 문항 답 → 기억 조각. 녹음에서 나온 기억과 같이 Memory 로 저장한다.

    '모름' 같은 표시 값은 빈 답이라 기억이 되지 않는다(for_ai).
    """
    answers = for_ai(answers)
    rows = []
    # 함께한 장면: 만들 때 하나(M7-01), 둘째부터는 기억 더 채우기(M7-01-2)
    for n, qid in enumerate(("M7-01", "M7-01-2"), start=1):
        if answers.get(qid):
            rows.append(
                {
                    "id": f"M7-01-{n}",
                    "memoryText": answers[qid],
                    "tags": ["기억 카드"],
                    "importanceScore": 7,
                    "relatedPeople": [basics["myName"]],
                    "confidence": "confirmed",
                    "sourceQuestion": qid,
                    "usageScope": "자유",
                }
            )
    # 그분 하면 떠오르는 것(M6-08)과 그 까닭(M6-08-why)은 기억 하나
    thing = answers.get("M6-08")
    if isinstance(thing, str) and thing:
        why = answers.get("M6-08-why")
        text = f"{M6_08_LEAD}: {thing}" + (f" — {why}" if isinstance(why, str) and why else "")
        row = _memory(basics, "M6-08", 1, text, M6_08_TAG, "자유")
        row["importanceScore"] = 7
        rows.append(row)
    # 선택 질문 답 → 기억 조각(챙겨 주시던 장면은 만들 때, 나머지는 '기억 더 채우기')
    for qid, (lead, scope) in MORE_MEMORY_TEXT.items():
        value = answers.get(qid)
        if not value:
            continue
        if qid.startswith("T1-") and basics.get("status") != "deceased":
            continue
        text = ", ".join(value) if isinstance(value, list) else value
        rows.append(_memory(basics, qid, 1, f"{lead}: {text}", lead, scope))
    for qid, lead in MORE_MEMORY_LISTS.items():
        for i, item in enumerate(answers.get(qid, []), start=1):
            rows.append(_memory(basics, qid, i, f"{lead}: {item}", lead, "자유"))
    return rows


def _memory(basics: dict, qid: str, n: int, text: str, tag: str, scope: str) -> dict:
    return {
        "id": f"{qid}-{n}",
        "memoryText": text,
        "tags": [tag],
        "importanceScore": 6,
        "relatedPeople": [basics["myName"]],
        "confidence": "confirmed",
        "sourceQuestion": qid,
        "usageScope": scope,
    }
