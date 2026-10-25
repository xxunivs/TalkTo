"""HTTP and backend-contract tests.

로컬 .env 에 AI_SERVER_TOKEN 이 있으면 인증이 켜져, 토큰을 안 실어 보내는 테스트는
검증에 닿기도 전에 401 로 떨어진다. 테스트는 개발자 기계의 .env 를 타면 안 되므로
여기서 입장 확인을 통째로 비워 둔다. 인증 자체는 test/app 에서 따로 본다.
"""

from app.api.dependencies import require_internal_request
from main import app

app.dependency_overrides[require_internal_request] = lambda: None
