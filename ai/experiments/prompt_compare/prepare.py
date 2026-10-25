"""비교 실험 준비 (OpenAI 호출 없음) — 프롬프트 변형·질문 세트·기억 선택을 미리 만들어 data/ 에 둔다.

    .venv\\Scripts\\python.exe experiments\\prompt_compare\\prepare.py

만드는 것
  variants/ops.txt         운영 DB 에 있는 손으로 쓴 프롬프트(운영 export personas_<tag>.json)
  variants/auto_now.txt    지금 파이프라인 자동 프롬프트(assemble_persona_prompt: 설문 작성본 + 통화 전사 재료 + 기억 통찰)
  variants/auto_layered.txt 4층 자동 프롬프트(pipeline/persona_prompt: 설문 + 전사 재료 + 말투 카드 + 실제 주고받기 30)
                           주고받기는 평가 문항(통화·세그먼트) 앞뒤 8개 구간을 빼고 고른다(실제 답을 베끼지 않게).
  variants/auto_layered_*.txt   4층 변형: shots0/shots60(주고받기 0·60개), nocard(말투 카드 없음), nocommon(L1 없음, 중간계층용),
                           iv(사람이 확인한 인터뷰 후보 data/interview_candidates.json), ivauto(확인 안 한 자동 후보)
  variants/ops_L1.txt      운영 수제본 앞에 4층 공통 규칙(L1)만 얹은 것 — 차이가 공통 규칙에서 오는지 보려고
  data/shots_auto_layered.json  auto_layered 의 주고받기 30개(메시지로 넣는 조건에서 씀)
  variants/<이름>.txt      그 밖에 이 폴더에 넣은 .txt 는 전부 변형으로 쓴다(계층 생성기 결과 auto_layered.txt 등)
  data/sets.jsonl          문항. set=orig(원본: 실제 통화 가족 질문 fact·absent) / ext(확장: 기억 뺀 loo + 탐침 probe)
                           문항마다 넘길 기억 id(mem_ids)를 미리 정해 둔다 → 모든 변형이 같은 기억을 받는다.
  data/style_real.json     실제 대상자 발화(통화 전사 대상자 발화) 말투 특징 — 말투 거리 기준선

기억 선택은 중간계층 TieredSelector 와 같다(장기 전부 + 단기는 분해 단서 임베딩 top-5, 운영 DB 벡터).
질의 임베딩은 캐시(data/cue_emb.json)를 쓰고, 캐시에 없는 질문(탐침)은 장기 기억만 넘긴다.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))
DATA, VAR = HERE / "data", HERE / "variants"
from subject import subject  # noqa: E402

S = subject()
SUBJECT = S["name"]
OUT, MANUAL, INPUT = REPO / "output" / SUBJECT, REPO / "output" / S["manual_dir"], REPO / "input" / SUBJECT


def load_memories() -> list[dict]:
    rows = [json.loads(x["j"]) for x in json.loads((DATA / f"memories_{SUBJECT}_{S['export_tag']}.json").read_text(encoding="utf-8"))]
    return [{"id": r["id"], "mtype": r["mtype"], "title": r.get("title") or "", "body": r["body"]} for r in rows
            if r.get("status", "ACTIVE") in ("ACTIVE", "active") and r["body"].strip()]


def ops_prompt() -> str:
    for x in json.loads((DATA / f"personas_{S['export_tag']}.json").read_text(encoding="utf-8")):
        d = json.loads(x["j"]) if "j" in x else x
        if d.get("subj") == SUBJECT or SUBJECT in (d.get("systemPrompt") or "")[:400]:
            return d["systemPrompt"]
    raise SystemExit(f"운영 export 에 {SUBJECT} 프롬프트가 없음")


def auto_now_prompt() -> str:
    """run_pipeline step_s3_c 와 같은 재료·합치기(Ctx.prompt_material)를 그대로 따른다."""
    from app.schema.persona import PersonaAssemblyRequest
    from app.schema.subject import FamilyNote, IntakeContext, SpeechStyle, SubjectContext, TabooTopic
    from pipeline.s3_synthesis.c_build_system_prompt import assemble_persona_instructions
    intake = json.loads((MANUAL / "s2_extraction/b_intake_prompt.json").read_text(encoding="utf-8"))
    rec = json.loads((OUT / "s2_extraction/d_recording_prompt.json").read_text(encoding="utf-8"))
    insights = json.loads((OUT / "s3_synthesis/b_insight.json").read_text(encoding="utf-8"))
    sc, ic = SubjectContext(**intake["subjectContext"]), IntakeContext(**intake["intakeContext"])
    known = {f.name for f in ic.family_map}
    ic.family_map += [FamilyNote(**r) for r in rec.get("familyMap", []) if r.get("name") and r["name"] not in known]
    style = rec.get("speechStyle") or {}
    if isinstance(ic.speech_style, SpeechStyle):
        found = ic.speech_style.interjections
        ic.speech_style.interjections = found + [w for w in style.get("interjections", []) if w not in found]
        ic.speech_style.summary = ic.speech_style.summary or style.get("summary")
    if all(isinstance(t, TabooTopic) for t in ic.taboo_topics):
        topics = {t.topic for t in ic.taboo_topics}
        ic.taboo_topics += [TabooTopic(**r) for r in rec.get("tabooTopics", []) if r.get("topic") and r["topic"] not in topics]
    examples = [r["text"] for r in rec.get("speechExamples") or [] if r.get("text")][:40]
    return assemble_persona_instructions(PersonaAssemblyRequest(
        subject_context=sc, intake_context=ic, speech_examples=examples, persona_insights=insights)).instructions


def subject_labels() -> dict:
    """통화별 대상자 화자 라벨(통화 전사 통계 utterances.csv 기준, 모든 통화)."""
    lab: dict[str, str] = {}
    for r in csv.DictReader((DATA / "utterances.csv").open(encoding="utf-8-sig")):
        if r["who"] == "target":
            lab.setdefault(r["call"], r["label"])
    return lab


def item_window(item_cids: list[str]):
    """평가 문항 구간(같은 통화 세그먼트 ±8)이면 True — 주고받기에서 뺀다(녹음 속 실제 답을 베끼지 않게)."""
    stem = lambda name: name.split(".", 1)[0]  # noqa: E731 — "통화12.m4a" / "통화01" → 확장자 없이
    window: dict[str, list[int]] = {}
    for cid in item_cids:  # "통화01_7", "통화12.m4a_272"
        call, seg = cid.rsplit("_", 1)
        window.setdefault(stem(call), []).append(int(seg))
    return lambda rec, seg: any(abs(seg - s) <= 8 for s in window.get(stem(rec), []))


def layered_spec(item_cids: list[str], interview: dict | None = None, max_shots: int = 30):
    from pipeline.persona_prompt import build
    return build.for_local(OUT, INPUT, intake_path=MANUAL / "s2_extraction/b_intake_prompt.json",
                           labels=subject_labels(), interview=interview, max_shots=max_shots,
                           shot_exclude=item_window(item_cids))


def layered_variants(item_cids: list[str]) -> dict[str, str]:
    """4층과 그 변형(개선 후보·절제 실험). 이름 → 프롬프트."""
    from pipeline.persona_prompt.render import common_rules, render_layered_prompt
    out: dict[str, str] = {}
    spec, log = layered_spec(item_cids)
    (DATA / "auto_layered_log.txt").write_text("\n".join(log + spec.notes) + "\n", encoding="utf-8")
    out["auto_layered"] = render_layered_prompt(spec)
    out["auto_layered_nocommon"] = render_layered_prompt(spec, include_common=False)  # 중간계층이 L1 을 붙일 때
    (DATA / "shots_auto_layered.json").write_text(
        json.dumps([sh.model_dump() for sh in spec.style.shots], ensure_ascii=False, indent=1), encoding="utf-8")
    out["auto_layered_shots0"] = render_layered_prompt(spec, shots_mode="messages")  # 주고받기를 메시지로 넣는 조건의 바탕
    out["auto_layered_nocard"] = render_layered_prompt(spec, drop=["style_card"])
    spec60, _ = layered_spec(item_cids, max_shots=60)
    out["auto_layered_shots60"] = render_layered_prompt(spec60)
    out["ops_L1"] = common_rules(spec) + "\n\n" + ops_prompt()  # 운영 수제본에 4층 공통 규칙만 얹기
    for f, name in ((DATA / "interview_candidates.json", "auto_layered_iv"),
                    (DATA / "interview_candidates_auto.json", "auto_layered_ivauto")):
        if f.exists():
            sp, lg = layered_spec(item_cids, interview=json.loads(f.read_text(encoding="utf-8")))
            (DATA / f"{name}_log.txt").write_text("\n".join(lg + sp.notes) + "\n", encoding="utf-8")
            out[name] = render_layered_prompt(sp)
    return out


def selector_and_store(mems: list[dict]):
    from orchestrator.contracts import MemoryItem
    from orchestrator.memory import ExportVectors, FileEmbedder, TieredSelector
    from orchestrator.persona import PersonaRegistry
    emb = FileEmbedder()
    emb.table = json.loads((DATA / "cue_emb.json").read_text(encoding="utf-8"))
    vectors = ExportVectors(*sorted(DATA.glob(f"memory_embeddings_{S['export_tag']}_p*.json")))
    reg = PersonaRegistry(REPO / "personas_local")
    items = [MemoryItem(id=m["id"], title=m["title"], text=m["body"], memory_type=m["mtype"]) for m in mems]
    return TieredSelector(reg, emb, vectors), items, emb.table


def select(tiered, items, table, message: str, exclude: list[str], instr: str) -> tuple[list[str], str]:
    from orchestrator.contracts import SessionState, TurnInput
    keep = [m for m in items if m.id not in set(exclude)]
    turn = TurnInput(subject_id=SUBJECT, message=message, persona_instructions=instr)
    cue = tiered.cue(turn)
    if cue and cue not in table:  # 캐시에 없는 질문(탐침) → 장기만
        lt = [m for m in keep if m.memory_type == "LONG_TERM"]
        return [m.id for m in lt], f"lt{len(lt)}+st0|nocache"

    class Store:
        def list_for_subject(self, _):
            return keep
    sel = tiered.select(turn, SessionState(session_id=None, subject_id=SUBJECT), Store())
    return [m.id for m in sel.memories], sel.strategy


def main() -> None:
    from probes import load_probes
    from style import features
    VAR.mkdir(exist_ok=True)
    ops = ops_prompt()
    (VAR / "ops.txt").write_text(ops, encoding="utf-8")
    auto = auto_now_prompt()
    (VAR / "auto_now.txt").write_text(auto, encoding="utf-8")
    old = [json.loads(l) for l in (DATA / S["items_file"]).read_text(encoding="utf-8").splitlines() if l.strip()]
    for name, text in layered_variants([it["cid"] for it in old]).items():
        (VAR / f"{name}.txt").write_text(text, encoding="utf-8")
    print("변형:", ", ".join(f"{p.stem} {len(p.read_text(encoding='utf-8')):,}자" for p in sorted(VAR.glob("*.txt"))))

    mems = load_memories()
    tiered, items, table = selector_and_store(mems)
    rows = []
    for it in old:
        ids, strat = select(tiered, items, table, it["q"], it.get("exclude") or [], ops)
        rows.append({"iid": it["iid"], "set": "ext" if it["type"] == "loo" else "orig", "type": it["type"],
                     "turns": [it["q"]], "history": it["history"], "reply": it["reply"],
                     "evidence": it.get("evidence") or it.get("removed") or [], "key_terms": it.get("key_terms") or [],
                     "mem_ids": ids, "sel": strat})
    for p in load_probes():
        ids, strat = select(tiered, items, table, p["turns"][0], [], ops)
        rows.append({"iid": p["pid"], "set": "ext", "type": "probe", "turns": p["turns"], "history": [],
                     "rubric": p["rubric"], "must_any": p.get("must_any", []), "must_not": p.get("must_not", []),
                     "korean": p.get("korean", False), "mem_ids": ids, "sel": strat})
    (DATA / "sets.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    from collections import Counter
    c = Counter((r["set"], r["type"]) for r in rows)
    print("문항:", ", ".join(f"{s}/{t} {n}" for (s, t), n in sorted(c.items())),
          f"| 턴 수 합 {sum(len(r['turns']) for r in rows)}")
    print("기억 선택 예:", Counter(r["sel"].split("|")[0][:12] for r in rows).most_common(4))

    utt = list(csv.DictReader((DATA / "utterances.csv").open(encoding="utf-8-sig")))
    real = [r["text"] for r in utt if r["who"] == "target" and len(r["text"].strip()) >= 4]
    f = features(real)
    (DATA / "style_real.json").write_text(json.dumps(f, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"실제 말투 기준선: 대상자 발화 {f['n_utts']}개, 문장당 {f['chars_per_sent_mean']}자, 존댓말 {f['honorific_share']}")


if __name__ == "__main__":
    main()
