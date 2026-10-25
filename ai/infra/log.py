# 로그 형식 설정

import logging

# 바깥 라이브러리 로그는 경고부터만 남긴다. httpx 는 INFO 에 요청 주소 전체(서명된 S3 주소)를,
# openai 는 DEBUG 에 요청 본문(대화·기억 원문)을 남긴다.
_QUIET_LOGGERS = ("httpx", "httpx2", "httpcore", "openai")


def configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
