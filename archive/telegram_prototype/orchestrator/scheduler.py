"""
10분마다 발신 트리거 (테스트 단계).

프로덕션에서는 사용자 생활패턴 기반으로 교체 예정.
"""
import os
import asyncio

INTERVAL_MIN = int(os.environ.get("CALL_INTERVAL_MINUTES", "10"))


async def run_forever(trigger_call):
    while True:
        try:
            await trigger_call()
        except Exception as e:
            print(f"[scheduler] call failed: {e}")
        await asyncio.sleep(INTERVAL_MIN * 60)
