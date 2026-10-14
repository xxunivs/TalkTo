"""테스트는 외부 API 를 부르지 않는다. .env 가 없어도 돌도록 키 자리에 가짜 값을 넣는다(진짜 값이 있으면 그대로 둔다)."""
import os

for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY"):
    os.environ.setdefault(key, "test-key")

# 기능 플래그는 테스트가 기본 동작(legacy)을 보게 고정한다. 로컬 .env 에서 4층·중간계층을 켜 둬도
# 테스트 결과가 바뀌지 않게(load_dotenv 는 이미 있는 값을 덮지 않는다). 켠 경로는 각 테스트가 직접 설정한다.
for key, value in {
    "PERSONA_PROMPT_BUILDER": "legacy",
    "PERSONA_ORCHESTRATOR": "false",
    "ORCHESTRATOR_ASSEMBLER": "legacy",
    "ORCHESTRATOR_RULES": "legacy",
    "ORCHESTRATOR_PRECHECK": "noop",
    "ORCHESTRATOR_GUARD": "pass",
    "ORCHESTRATOR_LANG_GUARD": "off",
    "ORCHESTRATOR_SELECTOR": "passthrough",
    "ORCHESTRATOR_SHOTS": "system",
    "PERSONA_DEATH_TALK": "intake",
}.items():
    os.environ[key] = value

# 배포 환경(staging·production)은 토큰이 없으면 서버가 뜨지 않는다. 로컬 .env 와 무관하게 test 로 고정한다.
os.environ["APP_ENV"] = "test"
