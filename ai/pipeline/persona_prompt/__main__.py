"""로컬 실행: 4층 프롬프트 생성(build), 인터뷰 후보 추출(extract-interview, OpenAI 사용)

    python -m pipeline.persona_prompt build --subject 박영자 [--subject-out 박영자] [--intake ...json]
                                            [--labels ...json] [--interview ...candidates.json] [--out-dir ...]
    python -m pipeline.persona_prompt extract-interview --subject 박영자   # LLM 1회 -> 후보 파일(사람이 확인)

build 산출물: <out-dir>/persona_spec.json, c_system_prompt_layered.txt, persona_spec_log.txt
  --shots-mode messages: 주고받기를 shots_messages.json 으로 분리
  persona_spec.json: 중간계층 등록부(<ORCHESTRATOR_PERSONA_DIR>/<이름>/spec.json) 겸용
  기본 out-dir: output/<subject-out>/s3_synthesis
  설문: output/<subject-out>/s2_extraction/b_intake_prompt.json, 없으면 input/<subject>/persona_intake.json
  --labels: {"통화01.wav": "SPK_0", ...} 대상자 화자 라벨 (없으면 b_speaker_id.json)
extract-interview: input/<subject>/가족인터뷰_*.txt -> output/<subject-out>/s3_synthesis/interview_candidates.json
  근거 줄 검증 통과 후보만 저장, 사람이 검토 후 build --interview 로 반영
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.persona_prompt import build
from pipeline.persona_prompt.render import shot_messages


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("cmd", choices=["build", "extract-interview"])
    ap.add_argument("--subject", required=True)
    ap.add_argument("--subject-out")
    ap.add_argument("--input-root", default="input")
    ap.add_argument("--out-root", default="output")
    ap.add_argument("--intake")
    ap.add_argument("--labels")
    ap.add_argument("--interview")
    ap.add_argument("--out-dir")
    ap.add_argument("--max-shots", type=int, default=30)
    ap.add_argument(
        "--no-common", action="store_true", help="L1 공통 규칙 빼고(중간계층이 붙일 때)"
    )
    ap.add_argument(
        "--shots-mode",
        choices=["system", "messages"],
        default="system",
        help="실제 주고받기: system=지시문 안 글로 / messages=지시문에서 빼고 shots_messages.json 으로",
    )
    ap.add_argument(
        "--death-talk",
        choices=["intake", "off", "on"],
        default="intake",
        help="사인·임종 질문: intake=설문 그대로 / off=끔 / on=켬",
    )
    a = ap.parse_args()
    subject_dir = Path(a.out_root) / (a.subject_out or a.subject)
    input_dir = Path(a.input_root) / a.subject
    out_dir = Path(a.out_dir) if a.out_dir else subject_dir / "s3_synthesis"
    out_dir.mkdir(parents=True, exist_ok=True)

    if a.cmd == "extract-interview":
        from infra.external_api.openai_client import create_openai_client
        from infra.setting import load_settings
        from pipeline.persona_prompt.from_interview import extract_candidates

        src = next(input_dir.glob("가족인터뷰_*.txt"))
        s = load_settings()
        cands = extract_candidates(
            src.read_text(encoding="utf-8-sig"),
            client=create_openai_client(s, "interview extraction"),
            model=s.openai_analysis_model,
        )
        out = out_dir / "interview_candidates.json"
        out.write_text(json.dumps(cands, ensure_ascii=False, indent=1), encoding="utf-8")
        n = {k: len(v) for k, v in cands.items() if isinstance(v, list)}
        print(f"{src.name} → {out}\n  후보 {n}, 근거 검증 탈락 {cands['dropped']}")
        return

    read = lambda p: json.loads(Path(p).read_text(encoding="utf-8")) if p else None  # noqa: E731
    spec, log = build.for_local(
        subject_dir,
        input_dir,
        intake_path=Path(a.intake) if a.intake else None,
        labels=read(a.labels),
        interview=read(a.interview),
        max_shots=a.max_shots,
        death_talk={"intake": None, "off": False, "on": True}[a.death_talk],
    )
    text = build.prompt(spec, include_common=not a.no_common, shots_mode=a.shots_mode)
    (out_dir / "persona_spec.json").write_text(spec.model_dump_json(indent=1), encoding="utf-8")
    (out_dir / "c_system_prompt_layered.txt").write_text(text, encoding="utf-8")
    if a.shots_mode == "messages":
        msgs = shot_messages(spec)
        (out_dir / "shots_messages.json").write_text(
            json.dumps(msgs, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(
            f"주고받기 {len(msgs) // 2}쌍 → {out_dir / 'shots_messages.json'} (기억 블록 뒤, 대화 앞에 넣는다)"
        )
    (out_dir / "persona_spec_log.txt").write_text(
        "\n".join(log + ["", "생성기 판단:"] + spec.notes) + "\n", encoding="utf-8"
    )
    print("\n".join(log))
    print(
        f"판단 기록 {len(spec.notes)}줄, 사망 정책 {spec.death_policy}({spec.death_policy_source})"
    )
    print(f"→ {out_dir / 'c_system_prompt_layered.txt'} ({len(text):,}자)")


if __name__ == "__main__":
    main()
