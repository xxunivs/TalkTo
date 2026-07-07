import unittest

from app.pipeline.enrichment.service import aggregate_tags, sentence_count


class SentenceCountTest(unittest.TestCase):
    def test_counts_sentences(self) -> None:
        self.assertEqual(sentence_count("첫 문장이다. 둘째다! 셋째냐?"), 3)
        self.assertEqual(sentence_count("한 문장."), 1)
        self.assertEqual(sentence_count(""), 0)


class AggregateTagsTest(unittest.TestCase):
    def _mem(self, *tag_lists):
        return {"memorySegments": [{"tags": t} for t in tag_lists]}

    def test_ranks_by_frequency_and_caps(self) -> None:
        result = self._mem(
            ["음식요리", "최근"],
            ["음식요리", "건강병원"],
            ["음식요리"],
            ["건강병원"],
        )
        tags = aggregate_tags(result, max_tags=2)
        self.assertEqual(tags, ["음식요리", "건강병원"])  # 3회, 2회

    def test_drops_out_of_vocab(self) -> None:
        tags = aggregate_tags(self._mem(["음식요리", "존재안함"]))
        self.assertEqual(tags, ["음식요리"])

    def test_empty(self) -> None:
        self.assertEqual(aggregate_tags(self._mem()), [])


if __name__ == "__main__":
    unittest.main()
