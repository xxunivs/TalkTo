# 로그 형식 설정

import logging

# 외부 라이브러리 로그는 WARNING 이상만 (httpx INFO 에 서명된 S3 주소, openai DEBUG 에 대화, 기억 원문)
_QUIET_LOGGERS = ("httpx", "httpx2", "httpcore", "openai")


def configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
