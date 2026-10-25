"""테스트는 매번 빈 임시 DB 와 임시 저장소를 쓴다. 로컬 talkto.db·storage/ 를 건드리지 않는다.

app 을 임포트하기 전에 환경변수를 잡아야 해서 이 파일에 둔다.
"""

import os
import tempfile
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="talkto_api_test_"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["LOCAL_MEDIA_DIR"] = str(_tmp / "media")
os.environ["AI_SERVER_URL"] = ""
os.environ["STORAGE_BACKEND"] = "local"
# 로컬 .env 에 토스 키나 결제 시험 계정이 있어도 시험은 늘 '연습 결제'(MockProvider)로 돈다
os.environ["TOSS_CLIENT_KEY"] = ""
os.environ["TOSS_SECRET_KEY"] = ""
os.environ["PAYMENTS_TEST_EMAILS"] = ""
# 로컬 .env 에 운영 알림 메일 설정이 있어도 시험은 메일을 보내지 않는다(mailer)
os.environ["OPS_EMAIL_TO"] = ""
os.environ["MAIL_FROM"] = ""


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """시험마다 요청 횟수 제한을 비운다. 모든 시험이 같은 주소(testclient)에서 온다."""
    from app.core import ratelimit

    ratelimit.reset_all()
    yield
