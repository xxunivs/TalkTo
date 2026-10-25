"""fixture(jsonl)로 중간계층 실행 (모듈 하나만 볼 때는 --until 로 그 단계에서 정지)

fixture 한 줄: {"id": "...", "turn": {TurnInput}, "state": {SessionState}(선택)}  또는 TurnInput 자체
  state 없고 session_id 가 같으면 줄 순서대로 세션 연결 (여러 턴 대화 재현)

예)
  python -m orchestrator.cli test/orchestrator/fixtures/turns_sample.jsonl --until assemble
  python -m orchestrator.cli test/orchestrator/fixtures/turns_sample.jsonl --mock
  python -m orchestrator.cli <fixture> --memories <memories.json>
  (--mock 없이 끝까지 돌리면 .env 의 OpenAI 실호출, 비용 발생)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from orchestrator.contracts import SessionState, TurnInput
from orchestrator.llm import FakeLLM, OpenAIChatLLM
from orchestrator.memory import ExportFileMemoryStore
from orchestrator.pipeline import STAGES, TurnRun
from orchestrator.service import (
    build_assembler,
    build_default,
    build_guard,
    build_precheck,
    build_registry,
    build_selector,
)

REPO = Path(__file__).resolve().parents[1]


def dump_run(row_id: str, run: TurnRun) -> dict:
    out = {"id": row_id, "trace": [t.model_dump() for t in run.trace]}
    for name in (
        "state",
        "selection",
        "precheck",
        "bundle",
        "generation",
        "guard",
        "next_state",
        "result",
    ):
        v = getattr(run, name)
        if v is not None:
            out[name] = v.model_dump(exclude={"trace"}) if name == "result" else v.model_dump()
    return out


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("fixture", type=Path)
    ap.add_argument("--until", choices=STAGES)
    ap.add_argument("--mock", action="store_true", help="LLM 대신 고정 답(비용 0)")
    ap.add_argument(
        "--memories",
        type=Path,
        help="Neon memories export JSON — BE top-5 대신 이 파일 전부를 원본으로",
    )
    ap.add_argument(
        "--assembler", choices=["legacy", "structured"], help="기본: .env ORCHESTRATOR_ASSEMBLER"
    )
    ap.add_argument(
        "--precheck",
        choices=["noop", "coverage", "coverage_v11"],
        help="기본: .env ORCHESTRATOR_PRECHECK",
    )
    ap.add_argument(
        "--guard",
        choices=["pass", "v1", "v11"],
        help="기본: .env ORCHESTRATOR_GUARD (v1·v11 + structured = 근거 헤더)",
    )
    ap.add_argument(
        "--persona-dir", type=Path, help="페르소나 등록부(profile.json·말투 카드·실제 발화)"
    )
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)

    from infra.setting import load_settings

    settings = load_settings()
    if args.persona_dir:
        settings = settings.model_copy(update={"orchestrator_persona_dir": args.persona_dir})
    if args.guard:
        settings = settings.model_copy(update={"orchestrator_guard": args.guard})
    orch = build_default(settings)
    if args.assembler or args.precheck or args.persona_dir or args.guard:
        registry = build_registry(settings)
        orch.assembler = build_assembler(settings, args.assembler, registry)
        orch.selector = build_selector(settings, args.assembler, registry)
        orch.precheck = build_precheck(settings, args.precheck, registry)
        orch.guard = build_guard(settings, args.guard, registry)
    needs_llm = args.until is None or STAGES.index(args.until) >= STAGES.index("generate")
    if args.mock or not needs_llm:
        orch.llm = FakeLLM()
    else:
        orch.llm = OpenAIChatLLM(settings)
    if args.memories:
        orch.memories = ExportFileMemoryStore(default=args.memories)

    tag = (
        f"_{orch.assembler.impl}_{orch.precheck.impl}_{orch.guard.impl}"
        + (f"_{args.until}" if args.until else "")
        + ("_MOCK" if args.mock else "")
    )
    out = (
        args.out
        or REPO / "experiments" / "orchestrator" / "results" / f"{args.fixture.stem}{tag}.jsonl"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"구성: {orch.describe()}")
    n = 0
    with args.fixture.open(encoding="utf-8") as fin, out.open("w", encoding="utf-8") as fout:
        for i, line in enumerate(fin):
            if not line.strip():
                continue
            row = json.loads(line)
            turn = TurnInput.model_validate(row.get("turn", row))
            state = SessionState.model_validate(row["state"]) if row.get("state") else None
            run = orch.run(turn, until=args.until, state=state)
            rid = str(row.get("id", i))
            fout.write(json.dumps(dump_run(rid, run), ensure_ascii=False) + "\n")
            ms = " ".join(f"{t.stage}={t.ms}ms" for t in run.trace)
            ans = f" → {run.result.content[:40]!r}" if run.result else ""
            print(f"[{rid}] {ms}{ans}")
            n += 1
    print(f"{n}턴 끝. 결과: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
