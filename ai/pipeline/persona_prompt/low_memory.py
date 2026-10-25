"""기억이 적은 페르소나용 런타임 절 (모른다 반복으로 대화가 닫히는 문제 대응, 기존 경로와 중간계층 공용)"""

from __future__ import annotations

LOW_MEMORY_MAX = 5  # 이하면 기억이 적다고 봄 (실측 전 임시값)
BE_MEMORY_TOP_K = 5  # BE 가 /responses 에 보내는 검색 기억 수 (top-5)
LOW_MEMORY_HEAD = "# 기억이 아직 적을 때"


def memory_count(
    n_sent: int, memory_total: int | None = None, *, top_k: int = BE_MEMORY_TOP_K
) -> int | None:
    """총 기억 수 (memoryTotal 우선, 받은 수 < top_k 면 그 수, 아니면 None: 적다고 보지 않음)"""
    if memory_total is not None:
        return memory_total
    if n_sent < top_k:
        return n_sent
    return None


def is_low_memory(
    n_sent: int,
    memory_total: int | None = None,
    *,
    low_max: int = LOW_MEMORY_MAX,
    top_k: int = BE_MEMORY_TOP_K,
) -> bool:
    n = memory_count(n_sent, memory_total, top_k=top_k)
    return n is not None and n <= low_max


_SHARED = (
    "- 안부·인사·근황에는 기억이 필요 없다. 평소 성격과 말투대로 받고, 상대 근황을 하나 묻는다.\n"
    "- 상대가 이번 대화에서 알려 준 것은 다음 말에 자연스럽게 쓴다. 들은 것 외의 세부는 보태지 않는다.\n"
)


def low_memory_note(
    n: int, *, public: bool = False, deceased: bool = False, polite: bool = False
) -> str:
    """기억이 적을 때 절 (공개 본인, 비공개 사망, 비공개 생존별 문구, 존댓말 상대면 예시도 존댓말)"""
    head = f"{LOW_MEMORY_HEAD} (지금 기억 {n}개)\n"
    if public:
        return (
            head
            + '- 모르는 것을 물으면 "모르겠네요"로 끝내지 않는다. 짧게 인정한 뒤 그 이야기를 구체적으로 되물어 잇는다'
            '(예: "그건 제가 잘 모르겠네요. 언제 적 이야기예요?"). "더 말씀해 주실래요?" 같은 막연한 되묻기를 '
            "두 번 잇따라 쓰지 않는다.\n"
            "- 인사·안부·근황에는 기억이 필요 없다. 평소 성격과 말투대로 받고, 상대 근황을 하나 묻는다.\n"
            "- 상대가 이번 대화에서 알려 준 것은 다음 말에 자연스럽게 쓴다. 들은 것 외의 세부는 보태지 않는다.\n"
            "- '모른다'는 한 대화에서 한두 번이면 충분하다. 기억이 없다는 말을 거듭하지 않는다."
        )
    if deceased:
        # 잠금 2: 잊은 듯, 모르는 듯한 예시 금지 (test_low_memory 에서 확인)
        example, vague = (
            (
                '"그 이야기 한번 들려주실래요? 언제 적 일이었는지 듣고 싶네요."',
                '"이야기 좀 해 주실래요?"',
            )
            if polite
            else ('"그 얘기 네가 한번 해 줄래? 그게 언제 적 일이었는데?"', '"얘기 좀 해 줄래?"')
        )
        return (
            head
            + "- 물은 것이 기억에 없으면 지어내지 않고, 그 이야기를 가족에게서 듣고 싶다는 마음으로 구체적으로 "
            f"되물어 잇는다(예: {example}). {vague}처럼 막연한 되묻기를 두 번 잇따라 쓰지 않는다.\n"
            + _SHARED
            + "- 이야기를 청하는 되묻기도 한 대화에서 한두 번이면 충분하다. 같은 되묻기를 거듭하지 않고 상대 이야기로 잇는다."
        )
    example, vague = (
        ('"그건 잘 생각이 안 나네요. 그게 언제 일이었어요?"', '"이야기 좀 해 주실래요?"')
        if polite
        else ('"그건 잘 생각이 안 나네. 그게 언제 일이었는데?"', '"얘기 좀 해 줄래?"')
    )
    return (
        head
        + "- 모르는 것을 물으면 '모른다'로 끝내지 않는다. 짧게 인정한 뒤 그 이야기를 구체적으로 되물어 잇는다"
        f"(예: {example}). {vague} 같은 막연한 되묻기를 두 번 잇따라 쓰지 않는다.\n"
        + _SHARED
        + "- '모른다'는 한 대화에서 한두 번이면 충분하다. 기억이 없다는 말을 거듭하지 않는다."
    )
