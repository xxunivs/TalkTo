"""
TalkTo Userbot entrypoint.

- Pyrogram (MTProto) client initialization + session loading
- Scheduler-driven call trigger loop
- Orchestrator delegates call_session control
"""
import os
import sys
import json
import random
import signal
import asyncio
import logging

import yaml
from pyrogram import Client
from pytgcalls import PyTgCalls

from orchestrator.scheduler import run_forever
from orchestrator.pipeline import run_call_loop
from orchestrator.call_session import CallSession
from userbot.call_controller import CallController
from ingest import post_call

from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
log = logging.getLogger("talkto.main")

CALL_MAX_DURATION = int(os.environ.get("CALL_MAX_DURATION_SECONDS", "300"))
TARGET_USER_ID = int(os.environ.get("TARGET_USER_ID", "0"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data/calls"))
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/app/config"))


def _pick_opening_question() -> str:
    """Pick a random opening question from personas.yaml."""
    personas_path = CONFIG_DIR / "personas.yaml"
    try:
        with open(personas_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        questions = config.get("opening_questions", [])
        if questions:
            return random.choice(questions)
    except Exception:
        log.warning("Failed to load personas.yaml, using default question")
    return "오늘 하루 어땠어? 특별한 일 있었어?"


# Graceful shutdown event
_shutdown = asyncio.Event()


def _signal_handler() -> None:
    log.info("Shutdown signal received")
    _shutdown.set()


async def trigger_call(client: Client, pytgcalls_client: PyTgCalls) -> None:
    """Single call cycle: dial -> conversation loop -> post-call ingest."""
    call_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    call_dir = DATA_DIR / call_id
    call_dir.mkdir(parents=True, exist_ok=True)

    log.info("Starting call %s to user %s", call_id, TARGET_USER_ID)

    session = CallSession(
        call_id=call_id,
        target_user_id=TARGET_USER_ID,
        audio_dir=call_dir,
    )

    controller = CallController(client, pytgcalls_client)

    try:
        # Start the voice call
        await controller.start_call(TARGET_USER_ID)

        # Opening question from personas.yaml
        opening = _pick_opening_question()

        # Run conversation loop
        session = await run_call_loop(
            session=session,
            controller=controller,
            opening_question=opening,
            max_duration=CALL_MAX_DURATION,
        )
    except Exception:
        log.exception("Error during call %s", call_id)
    finally:
        try:
            await controller.end_call(call_id)
        except Exception:
            log.exception("Error ending call %s", call_id)

    # Save transcript
    import json

    transcript_path = call_dir / "transcript.json"
    transcript_path.write_text(
        json.dumps(session.transcript, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Transcript saved: %s (%d turns)", transcript_path, len(session.transcript))

    # Post-call analysis + cognee ingest (routing handled inside post_call.run)
    try:
        analysis = post_call.run(call_dir)
        log.info("Post-call analysis complete for %s: emotion=%s, topics=%s",
                 call_id, analysis.get("emotion"), analysis.get("topics"))
    except Exception:
        log.exception("Post-call ingest failed for %s", call_id)


async def main() -> None:
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    phone = os.environ["TELEGRAM_PHONE_NUMBER"]

    client = Client(
        name="talkto_userbot",
        api_id=api_id,
        api_hash=api_hash,
        phone_number=phone,
        workdir="/app/session",
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    log.info("Starting Pyrogram client...")
    await client.start()
    log.info("Pyrogram client connected (user: %s)", client.me.first_name if client.me else "unknown")

    pytgcalls_client = PyTgCalls(client)
    await pytgcalls_client.start()
    log.info("PyTgCalls started")

    try:
        scheduler_task = asyncio.create_task(
            run_forever(lambda: trigger_call(client, pytgcalls_client))
        )
        # Wait until shutdown signal
        await _shutdown.wait()
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
    finally:
        await client.stop()
        log.info("Pyrogram client stopped")


if __name__ == "__main__":
    asyncio.run(main())
