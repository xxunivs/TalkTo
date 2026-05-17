"""
Export PersonaAI memory chunks to the backend Memory create DTO shape.

Output:
  data/backend_memory_import.json
"""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "data" / "memories.json"
OUTPUT_PATH = ROOT / "data" / "backend_memory_import.json"


def to_backend_memory(item: dict) -> dict:
    legacy_id = item["id"]
    category = item["category"]

    return {
        "title": item["title"],
        "memoryType": "LONG_TERM",
        "relatedPeople": [],
        "relatedPeriod": None,
        "bodyMarkdown": item["content"],
        "tags": [
            f"category:{category}",
            f"legacy_id:{legacy_id}",
        ],
        "confidenceScore": 1.0,
    }


def main() -> None:
    memories = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    backend_memories = [to_backend_memory(item) for item in memories]
    OUTPUT_PATH.write_text(
        json.dumps(backend_memories, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"exported {len(backend_memories)} memories to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
