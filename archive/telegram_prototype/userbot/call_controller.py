"""
Telegram P2P voice call controller (Pyrogram + pytgcalls).

- start_call(target_user_id): play()로 P2P 전화 발신 + TTS 오디오 재생
- send_audio(wav_bytes): WAV → raw PCM 변환 후 play()로 스트리밍
- recv_audio(max_seconds): record()로 상대방 음성 녹음 → WAV 반환
- end_call(): leave_call()로 통화 종료
"""
import io
import asyncio
import tempfile
import logging
from pathlib import Path

import numpy as np
import soundfile as sf
from pyrogram import Client
from pytgcalls import PyTgCalls
from pytgcalls.types import CallConfig
from pytgcalls.types.raw import AudioStream, Stream
from pytgcalls.types.raw.audio_parameters import AudioParameters

log = logging.getLogger("talkto.call_controller")

# pytgcalls expects raw PCM: 48kHz, 16-bit, mono
PCM_RATE = 48000
PCM_CHANNELS = 1
PCM_SAMPLE_BYTES = 2  # 16-bit


class CallController:
    """Controls a single Telegram P2P voice call lifecycle."""

    def __init__(self, pyrogram_client: Client, pytgcalls_client: PyTgCalls):
        self.client = pyrogram_client
        self.pytgcalls = pytgcalls_client
        self._target_user_id: int | None = None
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="talkto_audio_"))

    async def start_call(self, target_user_id: int) -> str:
        """
        Initiate a P2P voice call by playing a short silence.
        pytgcalls auto-detects P2P when chat_id > 0.
        """
        self._target_user_id = target_user_id
        log.info("Starting P2P call to user %d", target_user_id)

        # Create a short silence file to initiate the call
        silence_path = self._tmp_dir / "silence.raw"
        silence = np.zeros(PCM_RATE * 1, dtype=np.int16)  # 1 second silence
        silence_path.write_bytes(silence.tobytes())

        await self.pytgcalls.play(
            target_user_id,
            Stream(
                microphone=AudioStream(
                    media_source=str(silence_path),
                    path=str(silence_path),
                    parameters=AudioParameters(
                        bitrate=PCM_RATE,
                        channels=PCM_CHANNELS,
                    ),
                ),
            ),
            config=CallConfig(timeout=60),
        )
        log.info("Call initiated to user %d", target_user_id)

        # Wait for connection to establish
        await asyncio.sleep(3)
        return str(target_user_id)

    async def send_audio(self, call_id: str, wav_bytes: bytes) -> None:
        """
        Send WAV audio into the active call.
        Converts WAV to raw PCM (48kHz mono 16-bit) and streams via pytgcalls.
        """
        log.info("Sending %d bytes of audio", len(wav_bytes))

        buf = io.BytesIO(wav_bytes)
        try:
            audio_data, sample_rate = sf.read(buf, dtype="int16")
        except Exception:
            log.exception("Failed to decode WAV audio")
            return

        # Convert to mono if stereo
        if len(audio_data.shape) > 1:
            audio_data = audio_data[:, 0]

        # Resample to 48kHz if needed
        if sample_rate != PCM_RATE:
            ratio = PCM_RATE / sample_rate
            new_length = int(len(audio_data) * ratio)
            indices = np.linspace(0, len(audio_data) - 1, new_length)
            audio_data = np.interp(
                indices, np.arange(len(audio_data)), audio_data.astype(np.float64),
            ).astype(np.int16)

        # Write raw PCM to temp file
        pcm_path = self._tmp_dir / f"send_{call_id}.raw"
        pcm_path.write_bytes(audio_data.tobytes())
        log.info("PCM written: %s (%d samples)", pcm_path, len(audio_data))

        # Stream into the call
        await self.pytgcalls.play(
            self._target_user_id,
            Stream(
                microphone=AudioStream(
                    media_source=str(pcm_path),
                    path=str(pcm_path),
                    parameters=AudioParameters(
                        bitrate=PCM_RATE,
                        channels=PCM_CHANNELS,
                    ),
                ),
            ),
        )

        # Wait for audio to finish playing
        duration = len(audio_data) / PCM_RATE
        log.info("Playing %.1fs of audio", duration)
        await asyncio.sleep(duration)

    async def recv_audio(self, call_id: str, max_seconds: int = 30) -> bytes:
        """
        Record incoming audio from the call for up to max_seconds.
        Returns WAV bytes.
        """
        log.info("Recording up to %ds of audio", max_seconds)

        recv_path = self._tmp_dir / f"recv_{call_id}.raw"

        # Start recording the other party's audio
        await self.pytgcalls.record(
            self._target_user_id,
            Stream(
                speaker=AudioStream(
                    media_source=str(recv_path),
                    path=str(recv_path),
                    parameters=AudioParameters(
                        bitrate=PCM_RATE,
                        channels=PCM_CHANNELS,
                    ),
                ),
            ),
        )

        # Wait for recording duration
        await asyncio.sleep(max_seconds)

        # Read recorded PCM data
        if recv_path.exists() and recv_path.stat().st_size > 0:
            raw_pcm = recv_path.read_bytes()
            log.info("Recorded %d bytes of PCM", len(raw_pcm))
        else:
            log.warning("No audio recorded, returning silence")
            raw_pcm = b"\x00" * (PCM_RATE * PCM_SAMPLE_BYTES * max_seconds)

        # Encode as WAV
        audio_array = np.frombuffer(raw_pcm, dtype=np.int16)
        wav_buf = io.BytesIO()
        sf.write(wav_buf, audio_array, PCM_RATE, format="WAV", subtype="PCM_16")
        wav_bytes = wav_buf.getvalue()
        log.info("Encoded WAV: %d bytes", len(wav_bytes))

        return wav_bytes

    async def end_call(self, call_id: str) -> None:
        """Hang up the call."""
        log.info("Ending call to user %s", self._target_user_id)

        if self._target_user_id is None:
            log.warning("No active call to end")
            return

        try:
            await self.pytgcalls.leave_call(self._target_user_id)
            log.info("Call ended successfully")
        except Exception:
            log.exception("Failed to end call")
        finally:
            self._target_user_id = None

        # Clean up temp files
        try:
            import shutil
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
        except Exception:
            pass
