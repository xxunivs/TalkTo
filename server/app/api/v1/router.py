from fastapi import APIRouter

from app.api.v1 import (
    admin,
    admin_import,
    admin_payments,
    admin_personas,
    analytics,
    auth,
    billing,
    consents,
    conversations,
    feedback,
    gaps,
    interviews,
    legal,
    materials,
    memories,
    notices,
    persona_setup,
    personas,
    sharing,
    stats,
    subject_consents,
    subject_rules,
    support,
    transcriptions,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(consents.router)
api_router.include_router(legal.router)
api_router.include_router(personas.router)
# /personas/active 가 /personas/{id} 보다 먼저 잡히도록 personas 다음에 둔다
api_router.include_router(persona_setup.router)
api_router.include_router(materials.router)
# 말로 답하기(/personas/{id}/transcriptions — 녹음 → 글, 녹음은 남기지 않는다)
api_router.include_router(transcriptions.router)
# 가족 공유: /personas/{id}/shares… (주인·들어온 가족)와 /invites/… (초대 받은 분)
api_router.include_router(sharing.router)
api_router.include_router(sharing.invite_router)
# 법률 자문 반영(2026-10-22): 그분 동의 녹음(/personas/{id}/subject-consent…)과 미성년·법정대리인·
# 유족 확인(/personas/{id}/minor · guardian… · bereaved)
api_router.include_router(subject_consents.router)
api_router.include_router(subject_rules.router)
# 앱 안 알림(/notices…)
api_router.include_router(notices.router)
api_router.include_router(conversations.router)
api_router.include_router(conversations.speech_router)
api_router.include_router(memories.router)
api_router.include_router(feedback.router)
# '더 알려 주실 기억'(/personas/{id}/gap-questions…)과 가족 고침 확인(/personas/{id}/corrections/…)
api_router.include_router(gaps.router)
# 관리자: 동의 녹음 검수·대화 상대 법률 처리·결제 환불. /admin/personas/unable 같은 고정 경로가
# 다른 /admin/personas/{id} 경로에 먼저 잡히지 않게 admin 보다 앞에 둔다
api_router.include_router(subject_consents.admin_router)
api_router.include_router(admin_personas.router)
api_router.include_router(admin_payments.router)
api_router.include_router(admin.router)
api_router.include_router(admin_import.router)
# 기억 인터뷰 신청(주인)과 관리자 화면(/admin/interviews…, /admin/personas/{id}/interview…)
api_router.include_router(interviews.router)
api_router.include_router(interviews.admin_router)
api_router.include_router(stats.router)
api_router.include_router(analytics.router)
api_router.include_router(billing.router)
# 고객센터(/support)와 관리자 문의 코드 찾기(/admin/support/lookup)
api_router.include_router(support.router)
api_router.include_router(support.admin_router)
