import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app
from pipeline.s1_transcription.d_voice_groups import SpeakerItem, cluster_speakers

GRANDMA = [1.0, 0.0, 0.0]
GRANDMA_ON_PHONE = [0.9, 0.3, 0.0]  # 같은 사람, 통화 음질 차이 (유사도 ~0.95)
GRANDSON = [0.0, 1.0, 0.0]
MOTHER = [0.2, 0.0, 1.0]


class ClusterSpeakersTest(unittest.TestCase):
    """같은 사람은 녹음이 달라도 한 묶음, 다른 사람은 따로."""

    def test_same_person_across_recordings_is_one_voice(self) -> None:
        groups = cluster_speakers([
            SpeakerItem("r1", "SPK_0", 30_000, GRANDMA),
            SpeakerItem("r1", "SPK_1", 10_000, GRANDSON),
            SpeakerItem("r2", "SPK_1", 20_000, GRANDMA_ON_PHONE),
            SpeakerItem("r2", "SPK_0", 5_000, MOTHER),
        ], threshold=0.5)
        members = [{(m.recording_id, m.speaker_label) for m in g.members} for g in groups]
        self.assertEqual(members[0], {("r1", "SPK_0"), ("r2", "SPK_1")})  # 가장 오래 말한 목소리가 먼저
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0].speech_ms, 50_000)

    def test_same_recording_needs_stronger_match(self) -> None:
        # 한 녹음 안의 두 화자는 0.7 이상이어야 합친다. 0.6 정도면 다른 사람으로 둔다.
        similar = [0.6, 0.8, 0.0]  # GRANDMA 와 0.6
        groups = cluster_speakers([
            SpeakerItem("r1", "SPK_0", 30_000, GRANDMA),
            SpeakerItem("r1", "SPK_1", 10_000, similar),
        ], threshold=0.5)
        self.assertEqual(len(groups), 2)

    def test_speaker_without_fingerprint_stays_alone(self) -> None:
        groups = cluster_speakers([
            SpeakerItem("r1", "SPK_0", 30_000, GRANDMA),
            SpeakerItem("r2", "SPK_0", 500, None),
        ], threshold=0.5)
        self.assertEqual(len(groups), 2)


class FakeEmbedder:
    VECTORS = {("a", "SPK_0"): GRANDMA, ("a", "SPK_1"): GRANDSON,
               ("b", "SPK_0"): GRANDSON, ("b", "SPK_1"): GRANDMA_ON_PHONE}

    def __init__(self, cache_dir=None) -> None:
        pass

    def embed_speakers(self, audio_path, segments):
        name = Path(audio_path).name
        return {s.speaker_label: self.VECTORS[(name, s.speaker_label)] for s in segments}


def _segments(labels: list[str]) -> list[dict]:
    return [
        {"id": str(uuid.uuid4()), "segmentIndex": i, "startMs": i * 3000, "endMs": i * 3000 + 2500,
         "speakerLabel": label, "transcriptText": "말"}
        for i, label in enumerate(labels)
    ]


class VoiceGroupsApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    @patch("pipeline.s1_transcription.d_voice_groups.download_audio",
           side_effect=lambda url, **kw: Path(url.rsplit("/", 1)[-1]))
    @patch("pipeline.s1_transcription.b_speaker_id.SpeakerEmbedder", FakeEmbedder)
    def test_groups_across_recordings(self, _download) -> None:
        response = self.client.post("/v1/analysis/voice-groups", json={"recordings": [
            {"recordingId": "rec-a", "audioUrl": "http://localhost:8000/media/a",
             "transcriptSegments": _segments(["SPK_0", "SPK_0", "SPK_1"])},
            {"recordingId": "rec-b", "audioUrl": "http://localhost:8000/media/b",
             "transcriptSegments": _segments(["SPK_0", "SPK_1", "SPK_1"])},
        ]})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["method"], "ecapa")
        voices = [{(m["recordingId"], m["speakerLabel"]) for m in g["members"]} for g in body["groups"]]
        self.assertEqual(voices, [{("rec-a", "SPK_0"), ("rec-b", "SPK_1")},
                                  {("rec-a", "SPK_1"), ("rec-b", "SPK_0")}])
        self.assertEqual(body["groups"][0]["speechMs"], 10_000)
