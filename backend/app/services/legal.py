"""동의문·약관과 동의 기록.

원문은 app/static/legal/*.md 하나뿐이다(앞머리 key/title/version/kind + 마크다운 본문).
서버는 {{상호}} 같은 자리표시를 설정 값으로 채워 보내고, {{호칭}} 은 그대로 둔다(화면이 그 대화
상대의 호칭으로 바꾼다).

동의는 두 곳에 남긴다.
- consents: 지금 상태(사용자·종류마다 한 줄). 게이트와 화면이 읽는다.
- consent_records: 이력. 덮어쓰지 않고 쌓는다(언제·어느 버전·어떤 글을 보고 동의했는지).

계정 필수 동의(ACCOUNT_REQUIRED, 아홉 가지)는 모두 가입할 때(첫 로그인 게이트) 받는다. 초대받은
가족도 같다.

더 받지 않는 문서는 app/static/legal/retired/ 로 옮겨 글을 그대로 둔다(예전 동의 기록의 doc·
text_sha256 증빙). docs() 는 그 폴더를 읽지 않아 /legal 목록에서 빠지고, 내 동의 내역의 이름은
RETIRED_TITLES(종류)·RETIRED_DOC_TITLES(문서)가 잇는다. version_of·text_sha256 은 그 폴더에서도
찾는다(예전 문서로 받은 동의를 거두는 기록이 그 판·해시를 남기게).

kind: script 는 그분 동의 녹음 대본이다(subject_consent_script·subject_consent_child). 화면이 '## '
줄마다 한 단계로 나누고, {{확인 단어}}·{{호칭}}·{{보호자}} 는 서버가 채우지 않는다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Consent, ConsentRecord, User
from app.services import mailer

LEGAL_DIR = Path(__file__).resolve().parent.parent / "static" / "legal"
RETIRED_DIR = LEGAL_DIR / "retired"
# 문서가 없는 종류(age14)의 버전
DEFAULT_VERSION = "0.3"
PENDING = "(준비 중)"
CHECKING = "확인 중"


@dataclass(frozen=True)
class Doc:
    key: str
    title: str
    version: str
    kind: str
    body: str  # 자리표시를 채우기 전 원문


def _parse(text: str) -> tuple[dict[str, str], str]:
    """앞머리(--- … ---)와 본문을 나눈다."""
    text = text.replace("\r\n", "\n").lstrip("﻿")
    match = re.match(r"---\n(.*?)\n---\n?", text, re.S)
    if not match:
        return {}, text
    meta = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, text[match.end() :]


def _load(folder: Path) -> dict[str, Doc]:
    out = {}
    for path in sorted(folder.glob("*.md")):
        meta, body = _parse(path.read_text(encoding="utf-8"))
        key = meta.get("key") or path.stem
        out[key] = Doc(
            key=key,
            title=meta.get("title", key),
            version=meta.get("version", DEFAULT_VERSION),
            kind=meta.get("kind", "consent"),
            body=body.strip("\n") + "\n",
        )
    return out


@lru_cache
def docs() -> dict[str, Doc]:
    return _load(LEGAL_DIR)


def doc(key: str) -> Doc | None:
    return docs().get(key)


@lru_cache
def _retired_docs() -> dict[str, Doc]:
    return _load(RETIRED_DIR)


def _retired(key: str) -> Doc | None:
    """retired/ 의 문서. /legal 에는 내지 않고, 예전 문서로 받은 동의를 거두는 기록(이미 만든 unable
    대화 상대의 voice_clone 거두기 — pledge_unable)이 그 문서의 판·해시를 그대로 남기게만 읽는다."""
    return _retired_docs().get(key)


# 계정 필수 동의. 모두 가입할 때(첫 로그인 게이트) 받는다. 순서·글은 화면에 그대로 쓴다.
# kind: confirm(확인) / consent(동의)
ACCOUNT_REQUIRED: list[dict] = [
    {
        "type": "age14",
        "label": "만 14세 이상이에요",
        "doc": None,
        "kind": "confirm",
    },
    {
        "type": "service",
        "label": "TalkTo의 대화 상대는 AI가 만든 것이라는 점을 이해했어요",
        "doc": "service",
        "kind": "confirm",
    },
    {
        "type": "terms",
        "label": "이용약관에 동의해요",
        "doc": "terms",
        "kind": "consent",
    },
    {
        "type": "privacy",
        "label": "개인정보 수집·이용 안내를 확인했어요",
        "doc": "privacy",
        "kind": "confirm",
    },
    {
        "type": "overseas",
        "label": (
            "해외 회사에 맡기는 처리(대답을 만드는 OpenAI)와 "
            "서버를 빌려 쓰는 곳(AWS 서울)을 확인했어요"
        ),
        "doc": "overseas",
        "kind": "confirm",
    },
    {
        "type": "overseas_fish",
        "label": (
            "대답을 그분 목소리로 읽어 주고 그분 목소리를 만드는 "
            "Fish Audio(미국)로 보내는 데 동의해요"
        ),
        "doc": "overseas",
        "kind": "consent",
    },
    {
        "type": "voice_data",
        "label": "녹음 파일 처리에 동의해요",
        "doc": "voice_data",
        "kind": "consent",
    },
    {
        "type": "voice_synthesis",
        "label": "그분 목소리 만들기에 동의해요",
        "doc": "voice_synthesis",
        "kind": "consent",
    },
    {
        "type": "overseas_elevenlabs",
        "label": (
            "녹음과 말로 적은 답의 받아쓰기, 그리고 예전에 만든 목소리를 다시 만들기 전까지 "
            "대답 읽기를 하는 ElevenLabs(미국)로 보내는 데 동의해요"
        ),
        "doc": "overseas",
        "kind": "consent",
    },
]
ACCOUNT_TYPES = [item["type"] for item in ACCOUNT_REQUIRED]
_ACCOUNT = {item["type"]: item for item in ACCOUNT_REQUIRED}

# 대화 상대별 종류(type = '<앞부분>:<persona_id>') → (문서, 내 동의 내역에 보일 이름)
# voice_clone 의 문서는 그분 상태(pledge_*)에 따라 달라 부르는 쪽이 정한다.
PERSONA_TYPES: dict[str, tuple[str | None, str]] = {
    "voice_clone": (None, "그분 목소리 만들기 확인"),
    "recording_pledge": ("recording_pledge", "녹음 올리기 전 확인"),
    "material_pledge": ("material_pledge", "남긴 글 올리기 전 확인"),
    "sensitive_religion": ("sensitive_religion", "종교·신앙 이야기 동의"),
    "sensitive_health": ("sensitive_health", "건강 이야기 동의"),
    "recurring": ("payment", "정기결제 동의"),
    "withdrawal_limit": ("payment", "결제 취소(청약철회) 제한 확인"),
    # 가족 공유(문서는 family_share 하나). 앞의 셋은 주인, 마지막은 들어오는 가족
    "family_share": ("family_share", "가족 공유 켜기"),
    "family_share_subject": ("family_share", "가족 공유 — 그분 허락 확인"),
    "family_share_sensitive": ("family_share", "가족 공유 — 건강·종교 이야기 따로 허락 확인"),
    "family_share_member": ("family_share", "가족 공유로 함께 이야기하기"),
    # 기억 인터뷰 신청(app/services/interview.py). 신청할 때마다 그 신청서에서 받는다(만든 분만)
    "interview_use": ("interview", "기억 인터뷰 — 들은 이야기를 기억·말투에 넣는 데 동의"),
    "interview_recording": ("interview", "기억 인터뷰 — 통화 녹음 동의"),
    # 살아 계신 분의 그분 동의 녹음 검수 통과(app/services/subject_consent.py). 문서는 녹음할 때
    # 보인 대본(성인·아이)이라 부르는 쪽이 정한다
    "subject_consent": (None, "그분 동의 녹음 — 검수 통과"),
    # 만 19세 미만 대화 상대(app/services/minors.py · api/v1/subject_rules.py). 만 14세 미만만 동의
    # 둘과 확인 문자까지 받는다
    "guardian_attest": ("guardian_consent", "법정대리인 확인"),
    "guardian_consent": ("guardian_consent", "만 14세 미만 아이 정보 수집·이용 — 법정대리인 동의"),
    "guardian_overseas": (
        "guardian_consent",
        "만 14세 미만 아이 정보를 해외 회사로 보내는 데 — 법정대리인 동의",
    ),
    "guardian_sms": ("guardian_consent", "법정대리인 휴대전화 확인 문자"),
    # 돌아가신 분의 신청 자격·유족 명단 확인(app/services/bereaved.py)
    "bereaved_pledge": ("pledge_deceased", "돌아가신 분 — 유족 확인"),
}
# 대화 상대별 종류 가운데 글이 바뀌면(버전이 오르면) 다음에 올릴 때 다시 받는 것.
# 가족 공유는 넣지 않는다(글을 고쳐도 함께 쓰는 가족이 멈추지 않게)
REASK_ON_CHANGE = {"recording_pledge", "material_pledge"}
# 계정 단위지만 필수는 아닌 종류
OTHER_TITLES = {"regular_price": "원래 가격 동의"}
# 더 받지 않는 종류(계정 단위·대화 상대별). 예전에 받은 줄이 내 동의 내역에 제 이름으로 보이게 둔다.
# 게이트에서 빼도 다시 묻거나 기록을 지우지 않는다. 예전 목소리를 MiniMax가 읽는 것은 이미 받은
# overseas_minimax 동의에 따른다(다시 만들기 전까지)
RETIRED_TITLES = {
    "overseas_minimax": "그분 목소리를 만드는 MiniMax로 보내는 데 동의(지금은 받지 않아요)",
    "pvc_transfer": "똑 닮은 목소리 서류와 녹음 보내기 동의(지금은 받지 않아요)",
    "contact_interview": "똑 닮은 목소리 상담 연락처 동의(지금은 받지 않아요)",
}
# retired/ 로 옮긴 문서(종류는 그대로 받는 것). 예전 voice_clone 줄이 그 문서 이름으로 보이게 둔다.
# 대화가 어려우신 분(unable)은 2026-10-22부터 새로 만들 수 없다(이미 만든 대화 상대는 남는다)
RETIRED_DOC_TITLES = {
    "pledge_unable": "대화가 어려우신 분 — 본인 허락 확인(지금은 받지 않아요)",
}

# ────────────────────────── 결제 전 확인 ──────────────────────────
# 결제할 때마다 체크하는 두 문장(종류 recurring·withdrawal_limit, 문서 payment). 증빙 해시가 체크한
# 글을 덮도록 payment.md 에도 한 글자도 다르지 않게 적는다. 화면은 /legal 의 payment 로 받는다
PAYMENT_LABELS = {
    "recurring": (
        "매달 같은 날 자동으로 결제되는 것에 동의해요. 언제든 해지할 수 있다는 것을 안내받았어요."
    ),
    "withdrawal_limit": (
        "결제하면 바로 유료 이용이 시작되고, 결제 후 대화를 이용하면 단순 변심에 의한 청약철회가 "
        "제한된다는 점을 확인했어요(결제 후 7일 안에 대화하지 않았으면 전액 환불, 체험을 쓰지 않은 "
        "대화 상대는 첫 결제 후 7일 안 전액 환불, 안내와 다르게 제공된 경우 환불은 그대로 "
        "보장돼요)."
    ),
}
# 결제 직전에 체크 위에 한 번 더 보이는 청약철회 안내(전자상거래법 제17조 제6항). 토스 결제창에도
# 쓴다
PAYMENT_NOTICE = (
    "결제하시면 바로 유료 이용이 시작돼요. 결제한 뒤 이 대화 상대와 대화를 시작하면(초대한 가족이 "
    "시작해도 같아요) 단순 변심에 의한 결제 취소(청약철회)가 제한돼요. 다만 결제한 날부터 7일 안에 "
    "한 번도 대화하지 않으셨거나, 무료 체험을 쓰지 않은 대화 상대를 처음 결제한 날부터 7일 "
    "안이거나, 표시·광고나 안내와 다르게 드렸을 때는 전액 돌려드려요. 한 번 결제한 이용 기간은 "
    "결제일부터 다음 달 같은 날의 전날까지예요."
)

# ────────────────────────── 만 19세 미만 대화 상대 ──────────────────────────
# 법정대리인이 체크하는 글(문서 guardian_consent). attest 는 모든 아이, consent·overseas 는 만 14세
# 미만만. 해외 회사 이름은 동의 항목 글이라 적는다(화면의 다른 글에는 적지 않는다)
GUARDIAN_LABELS = {
    "attest": (
        "저는 이 아이의 법정대리인(부모)이에요. 다른 쪽 부모님의 동의를 받았거나 제가 혼자 친권을 "
        "가지고 있어요."
    ),
    "consent": "만 14세 미만인 아이의 정보를 모으고 쓰는 데 법정대리인으로서 동의해요.",
    "overseas": (
        "아이의 정보를 미국의 Fish Audio·ElevenLabs·OpenAI로 보내는 데 법정대리인으로서 동의해요."
    ),
}

# ────────────────────────── 가족 공유 ──────────────────────────
# 대화 상대별 종류('<앞부분>:<persona_id>')라 계정 게이트(missing_required)와 상관없다.
# 주인은 그분 상태(persona_builder.pledge_doc 와 같은 deceased/alive/unable/self)에 따라 받는
# 확인이 다르다. 살아 계신 분은 그분 허락과 건강·종교 이야기 허락을 따로 받는다(개인정보 보호법
# 제17조·제23조). unable(대화가 어려우신 분)은 새로 만들 수 없지만 이미 만든 대화 상대가 남아 살아
# 계신 분과 같은 글을 둔다. 들어오는 가족은 family_share_member 하나다.
SHARE_DOC = "family_share"
SHARE_MEMBER_TYPE = "family_share_member"
SHARE_PREFIXES = (
    "family_share",
    "family_share_subject",
    "family_share_sensitive",
    SHARE_MEMBER_TYPE,
)
# 살아 계신 분(예전에 고른 unable 도 같은 글)
_SHARE_LIVING = {
    "family_share": (
        "가족 공유 안내를 읽었고, {{호칭}}의 대화 상대를 초대한 가족과 함께 쓰는 데 동의해요."
    ),
    "family_share_subject": (
        "{{호칭}}께 초대할 가족도 이 대화 상대와 이야기하게 된다고 직접 말씀드렸고, "
        "{{호칭}}께서 괜찮다고 하셨어요."
    ),
    "family_share_sensitive": (
        "건강·종교 이야기도 가족과의 대화에서 나올 수 있다는 것까지 "
        "{{호칭}}께 따로 허락을 받았어요."
    ),
}
# 주인 체크 문구(그분 상태 → 종류 → 글). 화면에 그대로 쓴다. {{호칭}} 은 주인의 호칭(화면이 채운다)
SHARE_OWNER_LABELS: dict[str, dict[str, str]] = {
    "deceased": {
        "family_share": (
            "가족 공유 안내를 읽었고, {{호칭}}의 대화 상대를 초대한 가족과 함께 쓰는 데 동의해요. "
            "만들 때 적은 유족 명단의 가족 가운데 반대하는 분이 없어요."
        ),
    },
    "alive": dict(_SHARE_LIVING),
    "unable": dict(_SHARE_LIVING),
    "self": {
        "family_share": (
            "가족 공유 안내를 읽었고, 제 목소리와 이야기로 만든 대화 상대와 "
            "제가 초대한 가족이 이야기하는 데 동의해요."
        ),
    },
}
# 들어오는 가족의 체크 문구. 가족은 호칭을 모르니 {{호칭}} 을 쓰지 않는다
SHARE_MEMBER_LABEL = (
    "가족 공유 안내를 읽었고, 만든 분께 내 계정 이름과 내가 적은 이름·관계·부르는 말·말 높이, "
    "들어온 날이 보이는 데 동의해요."
)


def share_owner_types(kind: str) -> list[str]:
    """주인이 가족 공유 전에 할 확인(그분 상태마다). 돌아가신 분·나 자신은 family_share 하나,
    살아 계신 분(예전 unable 포함)은 셋. 모르는 상태는 가장 많이 받는 쪽으로 본다."""
    return list(SHARE_OWNER_LABELS.get(kind) or SHARE_OWNER_LABELS["alive"])


def persona_type(prefix: str, persona_id: str) -> str:
    return f"{prefix}:{persona_id}"


def split_type(consent_type: str) -> tuple[str, str | None]:
    """'voice_clone:abc' → ('voice_clone', 'abc'). 계정 종류는 (종류, None)."""
    prefix, _, persona_id = consent_type.partition(":")
    return prefix, persona_id or None


# ────────────────────────── 자리표시 ──────────────────────────


def _won(value: int) -> str:
    return f"{value:,}원"


def _period(hours: int) -> str:
    """시간을 글로. 날로 나눠떨어지면 날로(168 → '7일'), 아니면 시간으로(36 → '36시간')."""
    return f"{hours // 24}일" if hours % 24 == 0 else f"{hours}시간"


def placeholders() -> dict[str, str]:
    """{{…}} 에 채울 값. 해외 회사의 나라·보관 같은 사실은 자리표시로 두지 않고 문서에 바로 적는다
    (글이 바뀌면 버전이 올라 다시 동의를 받게)."""
    from app.services.billing import GRACE_DAYS

    s = get_settings()

    def text(value: str | None, empty: str) -> str:
        return value.strip() if value and value.strip() else empty

    def limit(value: int | None) -> str:
        return "(제한 없음)" if value is None else str(value)

    return {
        "상호": text(s.business_name, PENDING),
        "대표자": text(s.business_owner, PENDING),
        "문의 이메일": text(s.business_email, PENDING),
        "전화": text(s.business_phone, PENDING),
        "데이터 서버 위치": text(s.legal_data_region, CHECKING),
        # 결제 전(베타) 무료 기준. 운영 값은 deploy.py 가 정한다(대화 상대 3명, 30일마다 200번)
        "prelaunch_personas": limit(s.prelaunch_personas_per_account),
        "prelaunch_conversations": limit(s.prelaunch_conversations),
        "text_only_fair_use": f"{s.text_only_fair_use:,}",
        # 환불 정책 첫머리. 결제가 열리면 빈 줄이 되어 사라진다(그때 글을 따로 고치지 않아도 되게)
        "결제 전 안내": ""
        if s.payments_open
        else (
            "**지금은 결제를 받지 않아요.** 베타 기간이라 구독 신청만 받고, "
            "신청만으로는 돈이 나가지 않아요. 아래 기준은 결제가 열린 뒤에 적용해요."
        ),
        # 처리방침 6번 지울 때 알림. 고객 메일이 켜져 있을 때만 '이메일로도'(SES 샌드박스·꺼짐이면
        # 보내지 않으니 적지 않는다 — 대표가 USER_MAIL_ENABLED 를 켜면 저절로 붙는다)
        "메일 안내": "(받을 수 있는 이메일이 있으면 이메일로도)"
        if mailer.user_mail_configured()
        else "",
        "trial_personas": str(s.trial_personas_per_account),
        "trial_days": str(s.trial_days),
        "trial_conversations": str(s.trial_conversations),
        "beta_months": str(s.beta_months),
        "grace_days": str(GRACE_DAYS),
        "plan_memories_price": _won(s.plan_memories_price),
        "plan_family_price": _won(s.plan_family_price),
        # 가족 공유(sharing.py): 대화 상대 하나에 초대할 수 있는 가족 수(쓰지 않은 링크도 센다)와
        # 초대 링크가 열려 있는 때. '일'·'시간'까지 채운다(문서는 '{{share_link_days}} 안에')
        "share_members": str(s.share_members_per_persona),
        "share_link_days": _period(s.share_link_hours),
    }


_SLOT = re.compile(r"\{\{([^{}]+)\}\}")


def fill(text: str) -> str:
    """아는 자리표시만 채운다. {{호칭}} 처럼 모르는 것은 그대로 둔다(화면이 바꾼다)."""
    values = placeholders()
    return _SLOT.sub(lambda m: values.get(m.group(1).strip(), m.group(0)), text)


def body(key: str) -> str | None:
    """보여 줄 본문(자리표시를 채운 것)."""
    item = doc(key)
    return fill(item.body) if item else None


def text_sha256(key: str | None) -> str | None:
    """보인 본문의 해시. 지금 문서에 없으면 retired/ 문서로(예전 동의를 거두는 기록의 증빙)."""
    item = (doc(key) or _retired(key)) if key else None
    shown = fill(item.body) if item else None
    return hashlib.sha256(shown.encode("utf-8")).hexdigest() if shown is not None else None


def account_items() -> list[dict]:
    """계정 필수 동의(표 순서). 모두 가입할 때 받는다."""
    return [
        {
            "type": item["type"],
            "label": fill(item["label"]),
            "doc": item["doc"],
            "kind": item["kind"],
        }
        for item in ACCOUNT_REQUIRED
    ]


# 호스팅서비스를 제공하는 자의 상호(전자상거래법 제10조 제1항 제6호, 시행령 제11조의4).
# 사업자 정보와 함께 화면에 보인다
HOSTING_PROVIDER = "Amazon Web Services(AWS)"


def business() -> dict:
    """사이버몰 첫 화면에 보일 사업자 정보(전자상거래법 제10조 제1항). 비운 값은 None."""
    s = get_settings()

    def clean(value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    return {
        "name": clean(s.business_name),
        "owner": clean(s.business_owner),
        "email": clean(s.business_email),
        "phone": clean(s.business_phone),
        "regNo": clean(s.business_reg_no),
        "address": clean(s.business_address),
        "mailOrderNo": clean(s.mail_order_no),
        "hosting": HOSTING_PROVIDER,
    }


# ────────────────────────── 버전·이름 ──────────────────────────


def default_doc(consent_type: str) -> str | None:
    prefix, persona_id = split_type(consent_type)
    if persona_id is None and prefix in _ACCOUNT:
        return _ACCOUNT[prefix]["doc"]
    return PERSONA_TYPES.get(prefix, (None, ""))[0]


def version_of(doc_key: str | None) -> str:
    # retired/ 로 옮긴 문서도 그 판으로(pledge_unable 은 0.4 — 기본값 0.3 으로 덮지 않게)
    item = (doc(doc_key) or _retired(doc_key)) if doc_key else None
    return item.version if item else DEFAULT_VERSION


def current_version(consent_type: str) -> str:
    return version_of(default_doc(consent_type))


def title_of(consent_type: str, doc_key: str | None = None) -> str:
    prefix, persona_id = split_type(consent_type)
    if persona_id is None and prefix in _ACCOUNT:
        return fill(_ACCOUNT[prefix]["label"])
    if persona_id is not None and prefix in PERSONA_TYPES:
        # 문서가 그때그때 다른 종류(목소리 확인은 그분 상태, 동의 녹음은 대본)는 그 문서 이름으로
        if prefix in ("voice_clone", "subject_consent") and doc_key:
            if doc(doc_key):
                return doc(doc_key).title
            if doc_key in RETIRED_DOC_TITLES:
                return RETIRED_DOC_TITLES[doc_key]
        return PERSONA_TYPES[prefix][1]
    return RETIRED_TITLES.get(prefix) or OTHER_TITLES.get(prefix, consent_type)


# ────────────────────────── 기록 ──────────────────────────


def log(
    db: AsyncSession,
    user_id: str | None,
    consent_type: str,
    agreed: bool,
    *,
    persona_id: str | None = None,
    doc: str | None = None,
    version: str | None = None,
    channel: str = "web",
    sha256: str | None = None,
) -> ConsentRecord:
    """이력만 한 줄 더한다(결제 직전 확인처럼 지금 상태가 필요 없는 것). commit 은 부르는 쪽.

    channel 은 받은 곳: web(화면에서 누름), admin(관리자라 동의 화면 없이 보낸 기록),
    share_*(가족 공유 동의를 남이 끝냈거나 다른 일로 끝난 기록 — sharing.END_CHANNELS ·
    STATUS_CHANNEL), admin_review(그분 동의 녹음 검수 통과), sms(법정대리인 확인 문자),
    basis_*(만 19세 미만 대화 상대의 부모 양쪽 근거를 고친 기록 — 새 값).

    sha256 을 주면 지금 문서 대신 그 해시를 남긴다(그분 동의 녹음은 검수하는 날이 아니라 녹음할 때
    보인 대본의 판·해시가 증빙이다 — version 과 함께 넘긴다).
    """
    doc_key = doc if doc is not None else default_doc(consent_type)
    row = ConsentRecord(
        user_id=user_id,
        consent_type=consent_type,
        persona_id=persona_id if persona_id is not None else split_type(consent_type)[1],
        agreed=agreed,
        version=version or version_of(doc_key),
        doc=doc_key,
        text_sha256=sha256 or text_sha256(doc_key),
        channel=channel,
        created_at=datetime.now(UTC),
    )
    db.add(row)
    return row


async def record(
    db: AsyncSession,
    user: User,
    consent_type: str,
    agreed: bool,
    *,
    persona_id: str | None = None,
    doc: str | None = None,
    channel: str = "web",
) -> None:
    """지금 상태(consents)를 고치고 이력(consent_records)에 한 줄 더한다. commit 은 부르는 쪽.

    channel 은 log 와 같다(받은 곳·끝난 까닭)."""
    doc_key = doc if doc is not None else default_doc(consent_type)
    version = version_of(doc_key)
    row = await db.scalar(
        select(Consent).where(Consent.user_id == user.id, Consent.consent_type == consent_type)
    )
    if row is None:
        row = Consent(user_id=user.id, consent_type=consent_type)
        db.add(row)
    row.agreed = agreed
    row.version = version
    row.agreed_at = datetime.now(UTC) if agreed else None
    log(
        db,
        user.id,
        consent_type,
        agreed,
        persona_id=persona_id,
        doc=doc_key,
        version=version,
        channel=channel,
    )


async def missing_required(db: AsyncSession, user: User) -> list[str]:
    """빠진 계정 필수 동의(없음·거둠·버전이 바뀜). 표 순서대로. 게이트가 본다(모든 계정이 아홉 가지
    모두로 막힌다 — 초대받은 가족도)."""
    rows = (
        await db.scalars(
            select(Consent).where(
                Consent.user_id == user.id, Consent.consent_type.in_(ACCOUNT_TYPES)
            )
        )
    ).all()
    have = {r.consent_type: r for r in rows}
    return [
        t
        for t in ACCOUNT_TYPES
        if not (have.get(t) and have[t].agreed and have[t].version == current_version(t))
    ]


async def has(db: AsyncSession, user_id: str, consent_type: str) -> bool:
    """동의했고(거두지 않았고), 계정 종류면 지금 문서 버전과 같은가."""
    row = await db.scalar(
        select(Consent).where(Consent.user_id == user_id, Consent.consent_type == consent_type)
    )
    if row is None or not row.agreed:
        return False
    prefix, persona_id = split_type(consent_type)
    if persona_id is not None:
        # 올리기 전 확인은 글이 바뀌면 다시 받고, 나머지 대화 상대별 종류는 버전을 따지지 않는다
        if prefix in REASK_ON_CHANGE:
            return row.version == version_of(PERSONA_TYPES[prefix][0])
        return True
    return row.version == current_version(consent_type)


async def last_doc(db: AsyncSession, user_id: str, consent_type: str) -> str | None:
    """마지막으로 동의할 때 보여 준 문서(voice_clone 은 그분 상태에 따라 다르다)."""
    return await db.scalar(
        select(ConsentRecord.doc)
        .where(
            ConsentRecord.user_id == user_id,
            ConsentRecord.consent_type == consent_type,
            ConsentRecord.agreed.is_(True),
        )
        .order_by(ConsentRecord.created_at.desc(), ConsentRecord.id.desc())
        .limit(1)
    )
