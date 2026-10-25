import unittest
from uuid import uuid4

from app.error import InvalidVoiceSampleError
from app.schema.memory import PersistedTranscriptSegment
from app.schema.voice import VoiceCloneFromTranscriptRequest, VoiceSampleSegment
from pipeline.s4_voice.a_voice import (
    clone_voice_from_transcript,
    collect_subject_samples,
    select_clone_samples,
)

_URL = "https://storage/recording.wav"


def _seg(index: int, start: int, end: int, label: str) -> PersistedTranscriptSegment:
    return PersistedTranscriptSegment(
        id=uuid4(),
        segmentIndex=index,
        startMs=start,
        endMs=end,
        speakerLabel=label,
        transcriptText="내용",
    )


class CollectSubjectSamplesTest(unittest.TestCase):
    """전사 구간 → 대상자 구간 자동 수집(순수 로직) 검증."""

    def test_filters_to_subject_and_sorts_by_time(self) -> None:
        segments = [
            _seg(1, 1000, 4000, "B"),  # 대상자
            _seg(2, 4000, 9000, "A"),  # 상대 화자 → 제외
            _seg(3, 500, 2500, "B"),   # 대상자(앞) → 정렬 확인
        ]
        samples = collect_subject_samples(segments, "B", _URL)
        self.assertEqual([s.start_ms for s in samples], [500, 1000])
        self.assertTrue(all(str(s.audio_url) == _URL for s in samples))

    def test_drops_invalid_range(self) -> None:
        segments = [_seg(0, 5000, 3000, "B")]  # end <= start
        self.assertEqual(collect_subject_samples(segments, "B", _URL), [])


class CloneFromTranscriptGuardTest(unittest.TestCase):
    """라벨 미확정·빈 샘플이면 엉뚱한 화자 클론을 막고 진행하지 않는다(발화량 폴백 없음)."""

    def _request(self, label: str | None) -> VoiceCloneFromTranscriptRequest:
        return VoiceCloneFromTranscriptRequest(
            name="clone",
            audioUrl=_URL,
            transcriptSegments=[_seg(0, 0, 2000, "B")],
            subjectSpeakerLabel=label,
        )

    def test_missing_label_raises(self) -> None:
        with self.assertRaises(InvalidVoiceSampleError):
            clone_voice_from_transcript(self._request(None), settings=None)

    def test_no_matching_segments_raises(self) -> None:
        with self.assertRaises(InvalidVoiceSampleError):
            clone_voice_from_transcript(self._request("Z"), settings=None)


class SelectCloneSamplesTest(unittest.TestCase):
    """누적 풀 상한 초과 시 긴 구간 우선 선별(앞 통화 편중 방지)."""

    def _sample(self, start: int, end: int, url: str = _URL) -> VoiceSampleSegment:
        return VoiceSampleSegment(audioUrl=url, startMs=start, endMs=end)

    def test_under_budget_keeps_all_longest_first(self) -> None:
        samples = [self._sample(0, 1000), self._sample(0, 3000), self._sample(0, 2000)]
        out = select_clone_samples(samples, max_seconds=180)
        self.assertEqual([s.end_ms - s.start_ms for s in out], [3000, 2000, 1000])

    def test_over_budget_takes_longest_until_cap(self) -> None:
        # 단일 녹음, 상한 5초: 4초 + 3초(=7초, 초과)까지 담고 2초는 버림.
        samples = [self._sample(0, 2000), self._sample(0, 4000), self._sample(0, 3000)]
        out = select_clone_samples(samples, max_seconds=5)
        self.assertEqual([s.end_ms - s.start_ms for s in out], [4000, 3000])

    def test_spreads_across_recordings(self) -> None:
        # 녹음 A에 8초 구간 둘, 녹음 B에 8초 하나. 상한 10초면 라운드로빈으로
        # A만 독식하지 않고 A·B가 하나씩 들어간다.
        a1 = self._sample(0, 8000, "https://x/a.wav")
        a2 = self._sample(8000, 16000, "https://x/a.wav")
        b1 = self._sample(0, 8000, "https://x/b.wav")
        out = select_clone_samples([a1, a2, b1], max_seconds=10)
        urls = {str(s.audio_url) for s in out}
        self.assertEqual(urls, {"https://x/a.wav", "https://x/b.wav"})


class MultiSampleUpfrontDownloadTest(unittest.TestCase):
    """다구간 클론: 자르기 전에 녹음마다 한 번씩 먼저 다 받고, 실패하면 임시 폴더를 지운다(ffmpeg 없이)."""

    def test_downloads_all_sources_before_first_cut_and_cleans_up(self) -> None:
        import subprocess
        from pathlib import Path
        from unittest import mock

        from pipeline.s4_voice import a_voice

        a, b = "https://x/a.wav", "https://x/b.wav"
        segments = [
            VoiceSampleSegment(audio_url=url, start_ms=0, end_ms=3000) for url in (a, b, a)
        ]
        events: list[str] = []
        workdirs: list[Path] = []

        def fake_download(url, *, timeout_seconds, max_bytes, temp_dir):
            events.append(f"download {url}")
            workdirs.append(Path(temp_dir))
            path = Path(temp_dir) / f"src_{len(events)}.wav"
            path.write_bytes(b"RIFF")
            return path

        def fake_cut(src, dst, start_ms, end_ms, *, loudnorm=False):
            events.append("cut")
            raise subprocess.CalledProcessError(1, "ffmpeg", stderr=b"bad")

        settings = mock.Mock(audio_download_timeout_seconds=10, max_audio_bytes=None)
        with mock.patch.object(a_voice.shutil, "which", return_value="ffmpeg"), \
                mock.patch.object(a_voice, "download_audio", fake_download), \
                mock.patch.object(a_voice, "_ffmpeg_normalize", fake_cut), \
                self.assertRaises(InvalidVoiceSampleError):
            a_voice._build_multi_sample(segments, settings=settings)

        self.assertEqual(events, [f"download {a}", f"download {b}", "cut"])
        self.assertFalse(workdirs[0].exists())  # 실패 시 임시 폴더 정리(이전과 같음)


if __name__ == "__main__":
    unittest.main()
