import os
import sys
import time
from dotenv import load_dotenv
from openai import OpenAI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

TRAIN_PATH = "finetune/train.jsonl"
VAL_PATH = "finetune/val.jsonl"
MODEL = "gpt-4.1-mini-2025-04-14"

def upload_file(path, purpose="fine-tune"):
    print(f"업로드 중: {path}")
    with open(path, "rb") as f:
        result = client.files.create(file=f, purpose=purpose)
    print(f"  → file_id: {result.id}")
    return result.id

def start_finetune(train_file_id, val_file_id):
    print(f"\n파인튜닝 시작: {MODEL}")
    job = client.fine_tuning.jobs.create(
        training_file=train_file_id,
        validation_file=val_file_id,
        model=MODEL,
    )
    print(f"  → job_id: {job.id}")
    print(f"  → status: {job.status}")
    return job.id

def check_status(job_id):
    job = client.fine_tuning.jobs.retrieve(job_id)
    print(f"\n상태: {job.status}")
    if job.status == "succeeded":
        print(f"완료! 모델 ID: {job.fine_tuned_model}")
        print(f"\n이 모델 ID를 core/response/generator.py에 넣으면 됩니다:")
        print(f'  model="{job.fine_tuned_model}"')
    elif job.status == "failed":
        print(f"실패: {job.error}")
    else:
        print("아직 학습 중... 몇 분 후 다시 확인하세요:")
        print(f"  python finetune/run_finetune.py --check {job_id}")
    return job

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--check":

        job_id = sys.argv[2]
        check_status(job_id)
    else:

        train_id = upload_file(TRAIN_PATH)
        val_id = upload_file(VAL_PATH)
        job_id = start_finetune(train_id, val_id)
        print(f"\n학습이 시작됐습니다. 완료까지 수십 분 ~ 몇 시간 걸릴 수 있어요.")
        print(f"상태 확인: python finetune/run_finetune.py --check {job_id}")
