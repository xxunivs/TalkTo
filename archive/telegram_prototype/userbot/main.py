"""
TalkTo Bot entrypoint.

- Telegram Bot API로 음성/텍스트 메시지 기반 대화
- 주기적으로 대상에게 음성 질문 → 답변 대기 → 꼬리질문 반복
"""
import os
import json
import random
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from io import BytesIO

import yaml
from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    filters,
)

from orchestrator.pipeline import speak, transcribe, next_question
from orchestrator.call_session import CallSession
from ingest import post_call
from ingest.cognee_overlay import save_sync as save_overlay

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
log = logging.getLogger("talkto.main")

BOT_TOKEN = os.environ["BOT_TOKEN"]
TARGET_CHAT_ID = int(os.environ["TARGET_CHAT_ID"])
SCHEDULE_HOUR = int(os.environ.get("SCHEDULE_HOUR", "22"))  # 매일 밤 10시
MAX_TURNS = int(os.environ.get("MAX_TURNS", "10"))
REPLY_TIMEOUT = int(os.environ.get("REPLY_TIMEOUT_SECONDS", "120"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data/calls"))
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/app/config"))

# Reply queue: handler pushes, conversation loop pops
_reply_queue: asyncio.Queue | None = None


def _pick_opening_question() -> str:
    personas_path = CONFIG_DIR / "personas.yaml"
    try:
        with open(personas_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        questions = config.get("opening_questions", [])
        if questions:
            return random.choice(questions)
    except Exception:
        log.warning("Failed to load personas.yaml, using default")
    return "오늘 하루 어땠어? 특별한 일 있었어?"


async def handle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming voice, audio, video_note, or text from target user."""
    if update.effective_chat.id != TARGET_CHAT_ID:
        return
    if _reply_queue is None:
        return

    msg = update.message
    voice = msg.voice or msg.audio
    video_note = msg.video_note

    if voice:
        file = await context.bot.get_file(voice.file_id)
        data = await file.download_as_bytearray()
        log.info("Received voice reply (%d bytes)", len(data))
        await _reply_queue.put(("voice", bytes(data)))
    elif video_note:
        file = await context.bot.get_file(video_note.file_id)
        data = await file.download_as_bytearray()
        log.info("Received video_note reply (%d bytes)", len(data))
        await _reply_queue.put(("voice", bytes(data)))
    elif msg.text:
        log.info("Received text reply: %s", msg.text[:80])
        await _reply_queue.put(("text", msg.text))


async def send_and_wait(app: Application, text: str) -> tuple[str, str | bytes] | None:
    """TTS → send voice message → wait for reply (voice or text)."""
    global _reply_queue

    wav_bytes = await speak(text)
    await app.bot.send_voice(
        chat_id=TARGET_CHAT_ID,
        voice=BytesIO(wav_bytes),
        caption=text,
    )
    log.info("Sent voice message: %s", text[:60])

    # Drain any stale messages in queue
    while not _reply_queue.empty():
        try:
            _reply_queue.get_nowait()
        except asyncio.QueueEmpty:
            break

    try:
        result = await asyncio.wait_for(_reply_queue.get(), timeout=REPLY_TIMEOUT)
        return result
    except asyncio.TimeoutError:
        log.info("No reply within %ds", REPLY_TIMEOUT)
        return None


async def run_conversation(app: Application) -> None:
    """Run a single conversation session."""
    session_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    call_dir = DATA_DIR / session_id
    call_dir.mkdir(parents=True, exist_ok=True)

    session = CallSession(
        call_id=session_id,
        target_user_id=TARGET_CHAT_ID,
        audio_dir=call_dir,
    )

    question = _pick_opening_question()
    empty_count = 0

    for turn in range(MAX_TURNS):
        log.info("Turn %d/%d", turn + 1, MAX_TURNS)

        reply = await send_and_wait(app, question)
        session.add_turn("assistant", question)

        if reply is None:
            empty_count += 1
            session.add_turn("user", "")
            if empty_count >= 2:
                log.info("No reply 2x, ending conversation")
                break
            continue

        reply_type, reply_data = reply

        if reply_type == "voice":
            answer_text = await transcribe(reply_data)
            log.info("Transcribed: %s", answer_text[:120] if answer_text else "(empty)")
        else:
            answer_text = reply_data
            log.info("Text reply: %s", answer_text[:120])

        session.add_turn("user", answer_text)

        # 실시간 cognee 저장 (매 턴마다 overlay에 기록, 별도 스레드)
        if answer_text.strip():
            try:
                await asyncio.to_thread(save_overlay, [answer_text], session_id)
                log.info("Saved to cognee overlay: %s", answer_text[:60])
            except Exception:
                log.exception("Realtime cognee save failed")

        if not answer_text.strip():
            empty_count += 1
            if empty_count >= 2:
                log.info("Empty 2x, ending conversation")
                break
            continue
        else:
            empty_count = 0

        if turn < MAX_TURNS - 1:
            try:
                question = await next_question(answer_text)
            except Exception:
                log.exception("LLM follow-up failed")
                break

    # Save transcript
    transcript_path = call_dir / "transcript.json"
    transcript_path.write_text(
        json.dumps(session.transcript, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Transcript saved: %s (%d turns)", transcript_path, len(session.transcript))

    if len(session.transcript) >= 2:
        try:
            analysis = post_call.run(call_dir)
            log.info("Analysis complete: emotion=%s, topics=%s",
                     analysis.get("emotion"), analysis.get("topics"))
        except Exception:
            log.exception("Post-call ingest failed for %s", session_id)


async def scheduler_loop(app: Application) -> None:
    """매일 SCHEDULE_HOUR시에 대화 트리거."""
    from datetime import datetime, timedelta
    while True:
        now = datetime.now()
        target = now.replace(hour=SCHEDULE_HOUR, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        log.info("Next conversation at %s (%d seconds)", target.strftime("%Y-%m-%d %H:%M"), int(wait_seconds))
        await asyncio.sleep(wait_seconds)
        try:
            await run_conversation(app)
        except Exception:
            log.exception("Conversation failed")


async def post_init(app: Application) -> None:
    global _reply_queue
    _reply_queue = asyncio.Queue()
    asyncio.create_task(scheduler_loop(app))


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(
        filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE | filters.TEXT & (~filters.COMMAND),
        handle_reply,
    ))

    app.post_init = post_init

    log.info("Starting TalkTo bot...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
