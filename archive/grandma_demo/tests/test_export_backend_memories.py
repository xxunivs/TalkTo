import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from export_backend_memories import extract_related_people, to_backend_memory


class ExportBackendMemoriesTest(unittest.TestCase):
    def test_extracts_normalized_people_from_explicit_names_and_family_aliases(self) -> None:
        item = {
            "id": "family_son",
            "category": "가족",
            "title": "막내아들 이종서",
            "content": (
                "할머니의 막내아들은 이종서이다. 이종서의 아내는 손현선이다. "
                "자녀는 이준혁과 이채민이다."
            ),
        }

        self.assertEqual(
            extract_related_people(item),
            ["신금자", "이종서", "손현선", "이준혁", "이채민"],
        )

    def test_preserves_explicit_hyangi_parent_labels_without_generic_parent_aliases(self) -> None:
        item = {
            "id": "episode",
            "category": "추억",
            "title": "엄마 생일",
            "content": "향이가 엄마 생일에 미역국을 끓여줬다. 향이 아빠도 기뻐했다.",
        }

        self.assertEqual(extract_related_people(item), ["향이", "향이 아빠"])

    def test_backend_payload_uses_extracted_related_people(self) -> None:
        payload = to_backend_memory(
            {
                "id": "episode_sinrimdong",
                "category": "추억",
                "title": "신림동 시장 추억",
                "content": "향이와 규하를 데리고 시장에 갔다. 할머니가 업고 갔다.",
            }
        )

        self.assertEqual(payload["relatedPeople"], ["신금자", "향이", "규하"])


if __name__ == "__main__":
    unittest.main()
