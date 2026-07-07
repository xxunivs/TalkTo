import math
import unittest

from app.providers.stt.elevenlabs_scribe import group_scribe_words


def word(text, start, end, speaker="speaker_0", type_="word", logprob=None):
    entry = {"text": text, "start": start, "end": end, "speaker_id": speaker, "type": type_}
    if logprob is not None:
        entry["logprob"] = logprob
    return entry


class GroupScribeWordsTest(unittest.TestCase):
    def test_splits_segments_on_speaker_change(self) -> None:
        words = [
            word("밥은", 0.0, 0.5, "speaker_0"),
            word(" ", 0.5, 0.5, "speaker_0", type_="spacing"),
            word("먹었냐", 0.5, 1.2, "speaker_0"),
            word("네", 1.5, 1.8, "speaker_1"),
        ]

        segments = group_scribe_words(words)

        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].speaker_label, "SPK_0")
        self.assertEqual(segments[0].transcript_text, "밥은 먹었냐")
        self.assertEqual(segments[0].start_ms, 0)
        self.assertEqual(segments[0].end_ms, 1200)
        self.assertEqual(segments[1].speaker_label, "SPK_1")
        self.assertEqual(segments[1].segment_index, 1)

    def test_confidence_averages_word_logprobs(self) -> None:
        words = [
            word("밥은", 0.0, 0.5, logprob=-0.2),
            word(" ", 0.5, 0.5, type_="spacing"),
            word("먹었냐", 0.5, 1.2, logprob=-0.4),
        ]

        segments = group_scribe_words(words)

        self.assertAlmostEqual(segments[0].confidence, round(math.exp(-0.3), 4))

    def test_confidence_is_none_without_logprobs(self) -> None:
        segments = group_scribe_words([word("밥은", 0.0, 0.5)])

        self.assertIsNone(segments[0].confidence)

    def test_skips_audio_events_and_empty_segments(self) -> None:
        words = [
            word("(웃음)", 0.0, 0.5, type_="audio_event"),
            word(" ", 0.5, 0.5, type_="spacing"),
            word("응", 1.0, 1.2, "speaker_1"),
        ]

        segments = group_scribe_words(words)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].transcript_text, "응")


if __name__ == "__main__":
    unittest.main()


class StripNonSpeechNotesTest(unittest.TestCase):
    def test_removes_pause_and_silence_notes(self) -> None:
        from app.providers.stt.elevenlabs_scribe import strip_non_speech_notes

        self.assertEqual(
            strip_non_speech_notes("다이어트도 하려고요. (2초간 멈춤)"),
            "다이어트도 하려고요.",
        )
        self.assertEqual(strip_non_speech_notes("(침묵) 관절 약을 먹었는데"), "관절 약을 먹었는데")
        self.assertEqual(strip_non_speech_notes("(3초 멈춤)"), "")

    def test_keeps_normal_parentheses(self) -> None:
        from app.providers.stt.elevenlabs_scribe import strip_non_speech_notes

        self.assertEqual(
            strip_non_speech_notes("병원(정형외과)에 갔다"), "병원(정형외과)에 갔다"
        )
