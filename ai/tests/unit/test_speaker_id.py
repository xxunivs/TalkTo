import unittest

from app.pipeline.speaker_id.service import SpeakerMatch, cosine_similarity, identify_subject


class CosineSimilarityTest(unittest.TestCase):
    def test_identical_vectors(self) -> None:
        self.assertAlmostEqual(cosine_similarity([1.0, 2.0], [1.0, 2.0]), 1.0)

    def test_orthogonal_vectors(self) -> None:
        self.assertAlmostEqual(cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_zero_vector_is_zero(self) -> None:
        self.assertEqual(cosine_similarity([0.0, 0.0], [1.0, 1.0]), 0.0)


class IdentifySubjectTest(unittest.TestCase):
    embeddings = {
        "SPK_0": [1.0, 0.0],
        "SPK_1": [0.6, 0.8],
    }

    def test_picks_most_similar_above_threshold(self) -> None:
        match = identify_subject(self.embeddings, [0.7, 0.7], threshold=0.5)

        self.assertEqual(
            match, SpeakerMatch(speaker_label="SPK_1", similarity=match.similarity)
        )
        self.assertGreater(match.similarity, 0.9)

    def test_returns_none_below_threshold(self) -> None:
        match = identify_subject(self.embeddings, [-1.0, 0.0], threshold=0.5)

        self.assertIsNone(match)

    def test_returns_none_for_empty_speakers(self) -> None:
        self.assertIsNone(identify_subject({}, [1.0, 0.0], threshold=0.5))


if __name__ == "__main__":
    unittest.main()
