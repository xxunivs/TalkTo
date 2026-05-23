"""
transcript.txt에서 파인튜닝 데이터셋(JSONL)을 생성하는 스크립트.

화자 구분된 전사본에서:
- 손녀 발화 → user
- 할머니 발화 → assistant
- 슬라이딩 윈도우로 multi-turn 대화 생성
"""
import os
import sys
import json
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompts.system_prompt_B import SYSTEM_PROMPT

INPUT_PATH = "data/transcript.txt"
OUTPUT_TRAIN = "finetune/train.jsonl"
OUTPUT_VAL = "finetune/val.jsonl"

WINDOW_SIZE = 6   # 한 예시에 포함할 대화 턴 수
SHIFT = 2         # 슬라이딩 윈도우 이동 간격


def parse_transcript(path):
    """전사본에서 파일별 대화 리스트를 추출"""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 파일별로 분리
    files = re.split(r"={10,}", content)

    all_conversations = []
    for section in files:
        turns = []
        for line in section.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            if re.match(r"\[파일 \d+\]", line) or re.match(r"-{3,}", line):
                continue

            if line.startswith("할머니:"):
                turns.append(("assistant", line[len("할머니:"):].strip()))
            elif line.startswith("손녀:"):
                turns.append(("user", line[len("손녀:"):].strip()))

        if turns:
            all_conversations.append(turns)

    return all_conversations


def make_pairs(turns):
    """대화 턴에서 user-assistant 쌍 만들기"""
    pairs = []
    i = 0
    while i < len(turns):
        if turns[i][0] == "user":
            user_msg = turns[i][1]
            # 다음 assistant 찾기
            if i + 1 < len(turns) and turns[i + 1][0] == "assistant":
                assistant_msg = turns[i + 1][1]
                pairs.append((user_msg, assistant_msg))
                i += 2
            else:
                i += 1
        else:
            # assistant가 먼저 나오면 건너뛰기 (할머니가 먼저 말한 경우)
            i += 1
    return pairs


def sliding_window(pairs, window_size, shift):
    """슬라이딩 윈도우로 multi-turn 예시 생성"""
    examples = []
    for start in range(0, max(1, len(pairs) - window_size + 1), shift):
        window = pairs[start:start + window_size]
        if len(window) >= 2:  # 최소 2턴
            examples.append(window)

    # 마지막 부분이 빠지지 않도록
    if pairs and (not examples or examples[-1] != pairs[-window_size:]):
        last = pairs[-window_size:]
        if len(last) >= 2:
            examples.append(last)

    return examples


def to_jsonl(examples, system_prompt):
    """OpenAI 파인튜닝 JSONL 형식으로 변환"""
    jsonl_lines = []
    for example in examples:
        messages = [{"role": "system", "content": system_prompt}]
        for user_msg, assistant_msg in example:
            messages.append({"role": "user", "content": user_msg})
            messages.append({"role": "assistant", "content": assistant_msg})
        jsonl_lines.append({"messages": messages})
    return jsonl_lines


def main():
    conversations = parse_transcript(INPUT_PATH)
    print(f"파일 수: {len(conversations)}")

    all_examples = []
    for conv in conversations:
        pairs = make_pairs(conv)
        examples = sliding_window(pairs, WINDOW_SIZE, SHIFT)
        all_examples.extend(examples)

    print(f"총 학습 예시: {len(all_examples)}")

    # 90% train, 10% validation
    split = int(len(all_examples) * 0.9)
    train = all_examples[:split]
    val = all_examples[split:]

    train_jsonl = to_jsonl(train, SYSTEM_PROMPT)
    val_jsonl = to_jsonl(val, SYSTEM_PROMPT)

    with open(OUTPUT_TRAIN, "w", encoding="utf-8") as f:
        for item in train_jsonl:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    with open(OUTPUT_VAL, "w", encoding="utf-8") as f:
        for item in val_jsonl:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"train: {len(train_jsonl)}건 → {OUTPUT_TRAIN}")
    print(f"val:   {len(val_jsonl)}건 → {OUTPUT_VAL}")


if __name__ == "__main__":
    main()
