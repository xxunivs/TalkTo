"""전사본을 파일 번호(1~13) 순서대로 재정렬하는 스크립트"""
import re

input_path = "data/voice_raw/할머니_dataset_원본.txt"
output_path = "data/전사본.txt"

# 원본 읽기
with open(input_path, "r", encoding="utf-8") as f:
    content = f.read()

# 파일별로 분리 (구분선 기준)
sections = re.split(r"={10,}", content)

# 각 섹션에서 파일 번호 추출
file_sections = {}
for section in sections:
    match = re.search(r"할머니_목소리_(\d+)\.wav", section)
    if match:
        num = int(match.group(1))
        file_sections[num] = section.strip()

# 1~13 순서대로 정렬해서 저장
with open(output_path, "w", encoding="utf-8") as f:
    for i in sorted(file_sections.keys()):
        f.write(f"{'='*80}\n")
        f.write(f"[파일 {i:02d}] 할머니 목소리 {i}.wav\n")
        f.write(f"{'-'*40}\n")

        # 메타데이터 제거하고 대화 내용만 추출
        lines = file_sections[i].split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if re.match(r"\[파일 \d+\]", line):
                continue
            if re.match(r"-{3,}", line):
                continue
            if re.match(r"처리 시간:|청크 수:|처리 완료:", line):
                continue
            if "변환된 텍스트" in line or "Fine-tuning" in line:
                continue
            if re.match(r"생성 일시:|총 파일 수:|성공한 파일:|실패한 파일:|성공률:|처리된 총 청크|사용 모델:|언어:", line):
                continue
            if "사용 팁" in line or "위 텍스트를" in line or "화자별 분리" in line or "fine-tuning" in line.lower():
                continue
            f.write(line + "\n")
        f.write("\n")

print(f"완료! {len(file_sections)}개 파일을 순서대로 정렬하여 {output_path}에 저장했습니다.")
