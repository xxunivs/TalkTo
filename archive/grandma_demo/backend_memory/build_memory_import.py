import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "data" / "memories.json"
OUTPUT_PATH = ROOT / "backend_memory" / "memory_import.json"

RELATED_PERSON_ALIASES = (
    ("신금자", ("신금자", "할머니")),
    ("이재산", ("이재산", "할아버지")),
    ("이종서", ("이종서",)),
    ("손현선", ("손현선",)),
    ("이신화", ("이신화", "신화", "큰고모")),
    ("이신경", ("이신경", "신경", "작은고모")),
    ("윤보영", ("윤보영", "큰고모부")),
    ("남석찬", ("남석찬", "작은고모부")),
    ("이준혁", ("이준혁", "준혁")),
    ("이채민", ("이채민", "채민")),
    ("향이", ("향이",)),
    ("규하", ("규하",)),
    ("남향", ("남향",)),
    ("남규하", ("남규하",)),
    ("홍윤현", ("홍윤현",)),
    ("남해욱", ("남해욱",)),
    ("윤선혜", ("윤선혜", "선혜")),
    ("윤선화", ("윤선화", "선화")),
    ("윤유진", ("윤유진", "유진")),
    ("신희용", ("신희용",)),
    ("신욱", ("신욱",)),
    ("향이 엄마", ("향이 엄마",)),
    ("향이 아빠", ("향이 아빠",)),
)

def extract_related_people(item: dict) -> list[str]:
    searchable_text = f'{item["title"]}\n{item["content"]}'

    return [
        person
        for person, aliases in RELATED_PERSON_ALIASES
        if any(alias in searchable_text for alias in aliases)
    ]

def to_backend_memory(item: dict) -> dict:
    legacy_id = item["id"]
    category = item["category"]
    item_tags = item.get("tags", [])

    return {
        "title": item["title"],
        "memoryType": "LONG_TERM",
        "relatedPeople": extract_related_people(item),
        "relatedPeriod": None,
        "bodyMarkdown": item["content"],
        "tags": [
            f"category:{category}",
            f"legacy_id:{legacy_id}",
            *item_tags,
        ],
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
