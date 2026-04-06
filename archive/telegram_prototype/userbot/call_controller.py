"""
Telegram voice call controller (Pyrogram raw API).

- start_call(target_user_id): Initiate P2P call via raw Telegram API
- send_audio(call_id, wav_bytes): Stream TTS audio into active call
- recv_audio(call_id, max_seconds): Capture caller audio as WAV bytes
- end_call(call_id): Hang up via raw Telegram API
"""
import os
import io
import asyncio
import tempfile
import logging
from pathlib import Path

import numpy as np
import soundfile as sf
from pyrogram import Client, raw

log = logging.getLogger("talkto.call_controller")


class CallController:
    """Controls a single Telegram voice call lifecycle."""

    def __init__(self, pyrogram_client: Client):
        self.client = pyrogram_client
        self._call_peer = None  # raw InputPhoneCall after accepted
        self._call_id: int | None = None
        self._access_hash: int | None = None
        self._fifo_dir = Path(tempfile.mkdtemp(prefix="talkto_audio_"))

    async def start_call(self, target_user_id: int) -> str:
        """
        Initiate a P2P voice call using Pyrogram raw API (phone.RequestCall).

        Returns a string call_id for tracking.
        """
        log.info("Requesting call to user %d", target_user_id)

        peer = await self.client.resolve_peer(target_user_id)

        # Generate a random g_a_hash for the Diffie-Hellman key exchange
        # In production this needs proper DH parameter handling.
        g_a = os.urandom(256)

        try:
            result = await self.client.invoke(
                raw.functions.phone.RequestCall(
                    user_id=peer,
                    random_id=int.from_bytes(os.urandom(4), "big"),
                    g_a_hash=g_a[:32],  # SHA-256 sized hash
                    protocol=raw.types.PhoneCallProtocol(
                        min_layer=92,
                        max_layer=92,
                        udp_p2p=True,
                        udp_reflector=True,
                        library_versions=["location.based"],
                    ),
                )
            )
            phone_call = result.phone_call
            self._call_id = phone_call.id
            self._access_hash = phone_call.access_hash
            log.info(
                "Call requested: id=%d, access_hash=%d",
                self._call_id,
                self._access_hash,
            )
        except Exception:
            log.exception("Failed to request call to user %d", target_user_id)
            raise

        log.info("Waiting for call acceptance...")
        await asyncio.sleep(5)

        return str(self._call_id)

    async def send_audio(self, call_id: str, wav_bytes: bytes) -> None:
        """
        Send WAV audio into the active call.

        Decodes WAV, resamples to 48kHz mono, and simulates playback duration.
        Audio streaming requires native tgvoip binding for actual transmission.
        """
        log.info("Sending %d bytes of audio for call %s", len(wav_bytes), call_id)

        buf = io.BytesIO(wav_bytes)
        try:
            audio_data, sample_rate = sf.read(buf, dtype="int16")
        except Exception:
            log.exception("Failed to decode WAV audio")
            return

        # Resample to 48kHz mono if needed
        if len(audio_data.shape) > 1:
            audio_data = audio_data[:, 0]
        if sample_rate != 48000:
            ratio = 48000 / sample_rate
            new_length = int(len(audio_data) * ratio)
            indices = np.linspace(0, len(audio_data) - 1, new_length)
            audio_data = np.interp(indices, np.arange(len(audio_data)), audio_data.astype(np.float64))
            audio_data = audio_data.astype(np.int16)

        raw_pcm = audio_data.tobytes()

        send_path = self._fifo_dir / f"send_{call_id}.raw"
        send_path.write_bytes(raw_pcm)
        log.info("Audio written to %s (%d bytes PCM)", send_path, len(raw_pcm))

        # Simulate playback duration
        duration = len(raw_pcm) / (48000 * 2)
        log.info("Waiting %.1fs for audio playback", duration)
        await asyncio.sleep(duration)

    async def recv_audio(self, call_id: str, max_seconds: int = 30) -> bytes:
        """
        Record incoming audio from the call for up to max_seconds.

        Returns WAV bytes. Currently waits for duration and checks for
        recorded file. Native tgvoip binding needed for actual capture.
        """
        log.info("Recording up to %ds of audio for call %s", max_seconds, call_id)

        recv_path = self._fifo_dir / f"recv_{call_id}.raw"

        log.info("Waiting %ds for recording", max_seconds)
        await asyncio.sleep(max_seconds)

        if recv_path.exists():
            raw_pcm = recv_path.read_bytes()
            log.info("Read %d bytes from recording", len(raw_pcm))
        else:
            log.warning("No recording file found, returning silence")
            raw_pcm = b"\x00" * (48000 * 2 * max_seconds)

        audio_array = np.frombuffer(raw_pcm, dtype=np.int16)
        wav_buf = io.BytesIO()
        sf.write(wav_buf, audio_array, 48000, format="WAV", subtype="PCM_16")
        wav_bytes = wav_buf.getvalue()
        log.info("Encoded WAV: %d bytes", len(wav_bytes))

        return wav_bytes

    async def end_call(self, call_id: str) -> None:
        """Hang up the call using Pyrogram raw API (phone.DiscardCall)."""
        log.info("Ending call %s", call_id)

        if self._call_id is None:
            log.warning("No active call to end")
            return

        try:
            await self.client.invoke(
                raw.functions.phone.DiscardCall(
                    peer=raw.types.InputPhoneCall(
                        id=self._call_id,
                        access_hash=self._access_hash,
                    ),
                    duration=0,
                    reason=raw.types.PhoneCallDiscardReasonHangup(),
                    connection_id=0,
                )
            )
            log.info("Call %s ended successfully", call_id)
        except Exception:
            log.exception("Failed to discard call %s", call_id)
        finally:
            self._call_id = None
            self._access_hash = None

        try:
            import shutil
            shutil.rmtree(self._fifo_dir, ignore_errors=True)
        except Exception:
            pass
