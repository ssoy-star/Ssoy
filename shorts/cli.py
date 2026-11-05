"""명령줄 진입점.

  python -m shorts transcribe inbox/raw.mp4
  python -m shorts edit inbox/raw.mp4 --style variety --title "아이패드로 *쇼츠 편집*|끝내는 법"
  python -m shorts edit inbox/raw.mp4 --plan work/raw/plan.json
  python -m shorts analyze refs/hot.mp4 --transcript
  python -m shorts fetch "https://drive.google.com/file/d/.../view"
"""

import argparse
import copy
import json
import re
import sys
import time
from pathlib import Path

from . import FONTS_DIR, INBOX_DIR, OUTPUT_DIR, STYLES_DIR, WORK_DIR
from . import captions, edit, reframe, render
from .probe import probe

PLAN_KEYS = ["style", "title", "layout", "pace", "ranges", "emphasis", "replace", "bgm",
             "bgm_db", "zoom", "zoom_amount", "model", "vocab", "face", "cut_fillers",
             "title_mode", "style_overrides", "zooms", "audio", "texts", "speeds", "fx"]


def slug(name):
    s = re.sub(r"[^\w\-]+", "_", name).strip("_")
    return s[:60] or "video"


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_style(name_or_path, overrides=None):
    p = Path(name_or_path)
    if not p.suffix:
        p = STYLES_DIR / f"{name_or_path}.json"
    if not p.exists():
        names = ", ".join(sorted(x.stem for x in STYLES_DIR.glob("*.json")))
        sys.exit(f"스타일을 찾을 수 없습니다: {name_or_path} (사용 가능: {names})")
    return deep_merge(json.loads(p.read_text()), overrides)


def parse_kv(spec):
    if not spec:
        return {}
    if isinstance(spec, dict):
        return spec
    out = {}
    for part in spec.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def parse_list(spec):
    if not spec:
        return []
    if isinstance(spec, list):
        return spec
    return [x.strip() for x in spec.split(",") if x.strip()]


def workdir_for(video):
    d = WORK_DIR / slug(Path(video).stem)
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_transcript(video, info, model, vocab, force=False):
    from . import transcribe as tr_mod

    wd = workdir_for(video)
    jp, tp = wd / "transcript.json", wd / "transcript.txt"
    if jp.exists() and not force:
        tr = json.loads(jp.read_text())
        if tr.get("model") == model or tr.get("edited"):
            return tr
    if not info["has_audio"]:
        return {"words": [], "segments": [], "model": model, "duration": info["duration"]}
    t0 = time.time()
    print(f"▶ 음성 인식 중 (whisper {model})…", flush=True)
    tr = tr_mod.transcribe(video, model=model, vocab=vocab)
    tr_mod.save(tr, jp, tp)
    print(f"  {len(tr['words'])}단어, {time.time() - t0:.0f}초 → {tp}", flush=True)
    return tr


# ---------------- commands ----------------

def cmd_transcribe(a):
    info = probe(a.video)
    tr = get_transcript(a.video, info, a.model, parse_list(a.vocab), force=True)
    print((workdir_for(a.video) / "transcript.txt").read_text())
    return tr


def cmd_edit(a):
    video = Path(a.video)
    plan = {}
    if a.plan:
        plan = json.loads(Path(a.plan).read_text())
    for k in PLAN_KEYS:
        v = getattr(a, k, None)
        if v is not None:
            plan[k] = v
    plan.setdefault("style", "variety")
    style = load_style(plan["style"], plan.get("style_overrides"))
    if plan.get("title_mode"):
        style["title"]["mode"] = plan["title_mode"]

    info = probe(video)
    model = plan.get("model", "small")
    tr = get_transcript(video, info, model, parse_list(plan.get("vocab")))
    words = tr["words"]

    pace = plan.get("pace") or style.get("pace", "tight")
    if not words and not plan.get("pace"):
        pace = "off"  # 말이 없는 영상(음원 입힐 챌린지·코디 영상)은 무음 기준으로 자르지 않는다
        print("  말소리가 없어 무음 컷을 끕니다 (원하면 --ranges 로 구간 지정).", flush=True)
    silences = edit.detect_silences(video) if info["has_audio"] and pace != "off" else []
    ranges = edit.parse_ranges(plan.get("ranges"))
    cut_fillers = plan.get("cut_fillers", True)
    segments = edit.plan_segments(info["duration"], words, ranges, silences, pace, cut_fillers)
    if not segments:
        sys.exit("남길 구간이 없습니다. --ranges 또는 --pace off 를 확인하세요.")

    speeds = [float(x) for x in plan["speeds"]] if plan.get("speeds") else [1.0] * len(segments)
    if len(speeds) != len(segments) or min(speeds) < 0.5 or max(speeds) > 2.0:
        sys.exit(f"speeds 는 컷 개수({len(segments)})만큼, 0.5~2.0 사이로 적어주세요.")
    out_words, duration = edit.map_words(words, segments, cut_fillers, speeds)
    duration += float((plan.get("fx") or {}).get("freeze_end", 0))  # 끝 장면 정지

    zcfg = style.get("zoom", {})
    zmode = plan.get("zoom") or zcfg.get("mode", "alternate")
    zamt = float(plan.get("zoom_amount") or zcfg.get("amount", 1.12))
    zooms = reframe.zoom_pattern(len(segments), zmode, zamt)
    if plan.get("zooms"):  # 컷별 줌 직접 지정 (예: 디테일 컷만 확대)
        explicit = [float(z) for z in plan["zooms"]]
        if len(explicit) != len(segments):
            sys.exit(f"zooms 개수({len(explicit)})가 컷 개수({len(segments)})와 다릅니다. "
                     "말소리 컷이 켜져 있으면 컷 수가 바뀌니 --pace off 와 함께 쓰세요.")
        zooms = explicit

    layout = render.resolve_layout(plan.get("layout"), info, style)
    # 가로 원본을 세로로 자를 때, 또는 세로 원본이라도 확대 컷이 있을 때 얼굴 위치를 찾는다
    use_face = (plan.get("face", True) and layout == "crop"
                and (info["aspect"] > 0.8 or max(zooms) > 1.0))
    centers = (reframe.face_centers(video, segments) if use_face
               else [reframe.DEFAULT_CENTER] * len(segments))
    if layout == "crop" and not use_face:
        centers = [(0.5, 0.5)] * len(segments)

    audio_mode = plan.get("audio") or ("normalize" if words else "keep")

    geom = render.geometry(layout, info, style)
    chunks = captions.build_chunks(out_words, style["caption"], parse_kv(plan.get("replace")),
                                   parse_list(plan.get("emphasis")))

    wd = workdir_for(video)
    ass_path, srt_path = wd / "captions.ass", wd / "captions.srt"
    overlays = captions.resolve_overlays(plan.get("texts"), segments, speeds)
    captions.write_ass(ass_path, chunks, style, geom, duration, plan.get("title"), overlays)
    captions.write_srt(srt_path, chunks)

    W, H = (720, 1280) if a.draft else (render.OUT_W, render.OUT_H)
    bgm = plan.get("bgm")
    fgraph = render.build_filter(info, segments, zooms, centers, geom, ass_path, FONTS_DIR,
                                 duration, style, has_bgm=bool(bgm),
                                 bgm_db=float(plan.get("bgm_db", -20)), W=W, H=H,
                                 audio_mode=audio_mode, speeds=speeds, fx=plan.get("fx"))
    fpath = wd / "filter.txt"
    fpath.write_text(fgraph)

    OUTPUT_DIR.mkdir(exist_ok=True)
    name = a.out or f"{slug(video.stem)}_{style['name']}{'_draft' if a.draft else ''}"
    out_mp4 = OUTPUT_DIR / f"{name}.mp4"
    print(f"▶ 렌더링: {info['width']}x{info['height']} {info['duration']:.1f}s → "
          f"{W}x{H} {duration:.1f}s, 컷 {len(segments)}개, 레이아웃 {layout}, 스타일 {style['name']}",
          flush=True)
    t0 = time.time()
    render.render(video, fpath, out_mp4, bgm=bgm, draft=a.draft, log_path=wd / "ffmpeg.log")
    cover = render.extract_cover(out_mp4, OUTPUT_DIR / f"{name}_cover.jpg", t=min(1.0, duration / 2))
    (OUTPUT_DIR / f"{name}.srt").write_text(srt_path.read_text())

    resolved = {"source": str(video), **plan, "style": style["name"], "layout": layout,
                "pace": pace, "zoom": zmode, "zoom_amount": zamt, "model": model,
                "zooms": zooms, "audio": audio_mode}
    (wd / "plan.json").write_text(json.dumps(resolved, ensure_ascii=False, indent=1))
    report = {
        "output": str(out_mp4), "cover": str(cover),
        "source_duration": round(info["duration"], 2), "output_duration": round(duration, 2),
        "removed_seconds": round(sum(e - s for s, e in (ranges or [(0, info["duration"])])) - duration, 2),
        "cuts": len(segments), "caption_chunks": len(chunks), "layout": layout,
        "style": style["name"], "hdr_tonemapped": info["hdr"], "render_seconds": round(time.time() - t0, 1),
        "segments": segments,
    }
    (wd / "edit_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"✔ 완료: {out_mp4}  ({report['render_seconds']}초)")
    print(f"  원본 {report['source_duration']}s → 결과 {report['output_duration']}s "
          f"(잘라낸 공백 {report['removed_seconds']}s), 커버: {cover}")
    if duration > 180:
        print("  ⚠ 3분 초과: 쇼츠/릴스 길이 제한(최대 3분)을 넘습니다. --ranges 로 하이라이트를 골라주세요.")
    return report


def cmd_analyze(a):
    from .analyze import analyze

    out = WORK_DIR / "refs" / slug(Path(a.video).stem)
    rep = analyze(Path(a.video), out, with_transcript=a.transcript, model=a.model,
                  threshold=a.threshold)
    print(json.dumps({k: v for k, v in rep.items() if k != "cut_times"}, ensure_ascii=False, indent=1))
    print(f"프레임 시트: {out / 'sheet.jpg'}, 훅(첫 3초): {out / 'hook.jpg'}")


def cmd_fetch(a):
    from .fetch import fetch

    for p in fetch(a.url, Path(a.dest) if a.dest else INBOX_DIR):
        try:
            info = probe(p)
            print(f"✔ 받음: {p}  ({info['width']}x{info['height']}, {info['duration']:.1f}s, "
                  f"{'HDR' if info['hdr'] else 'SDR'}, {p.stat().st_size / 1e6:.0f}MB)")
        except Exception:
            print(f"✔ 받음: {p}  (영상이 아니거나 읽을 수 없음)")


def cmd_background(a):
    from .matte import run_config

    t0 = time.time()
    out = run_config(a.config)
    print(f"✔ 배경 합성 완료: {out}  ({time.time() - t0:.0f}초)")


def cmd_voiceover(a):
    from .voice import build

    cfg = json.loads(Path(a.config).read_text())
    t0 = time.time()
    print("▶ 내레이션 생성·배치:")
    out = build(cfg, WORK_DIR / "voice" / slug(Path(cfg["output"]).stem))
    print(f"✔ 완료: {out}  ({time.time() - t0:.0f}초)")


def cmd_styles(_a):
    for p in sorted(STYLES_DIR.glob("*.json")):
        s = json.loads(p.read_text())
        print(f"- {p.stem}: {s.get('description', '')}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="shorts", description="쇼츠/릴스/틱톡 자동 편집기")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("transcribe", help="음성 인식 → work/<이름>/transcript.txt")
    t.add_argument("video")
    t.add_argument("--model", default="small", help="tiny|base|small|medium|large-v3-turbo")
    t.add_argument("--vocab", help="인식 힌트 단어 (쉼표 구분)")
    t.set_defaults(func=cmd_transcribe)

    e = sub.add_parser("edit", help="원본 → 세로 쇼츠 완성본")
    e.add_argument("video")
    e.add_argument("--plan", help="편집 계획 JSON (아래 옵션이 덮어씀)")
    e.add_argument("--style", help="variety | pop | clean | box | 커스텀 JSON 경로")
    e.add_argument("--title", help="상단 제목. *강조* , | 로 줄바꿈")
    e.add_argument("--title-mode", dest="title_mode", choices=["persistent", "hook"])
    e.add_argument("--layout", choices=["auto", "crop", "letterbox", "blur"])
    e.add_argument("--pace", choices=list(edit.PACES))
    e.add_argument("--ranges", help="사용할 구간(순서대로 이어붙임). 예: '0:12-0:20,0:03-0:09'")
    e.add_argument("--emphasis", help="강조 색을 줄 단어 (쉼표 구분)")
    e.add_argument("--replace", help="자막 오타 교정. 예: '슈츠=쇼츠,손해에요=손해예요'")
    e.add_argument("--bgm", help="배경음악 파일 (목소리 나올 때 자동으로 줄어듦)")
    e.add_argument("--bgm-db", dest="bgm_db", type=float)
    e.add_argument("--zoom", choices=["alternate", "punch", "none"])
    e.add_argument("--zoom-amount", dest="zoom_amount", type=float)
    e.add_argument("--zooms", type=lambda s: [float(x) for x in s.split(",")],
                   help="컷별 줌 직접 지정. 예: '1,1,1.3,1' (--ranges 와 개수 맞춤)")
    e.add_argument("--audio", choices=["normalize", "keep", "mute"],
                   help="normalize=-14LUFS(말 있는 영상 기본), keep=원본 그대로(말 없는 영상 기본), mute=무음")
    e.add_argument("--model", help="whisper 모델 (기본 small)")
    e.add_argument("--vocab", help="인식 힌트 단어 (쉼표 구분)")
    e.add_argument("--no-face", dest="face", action="store_false", default=None)
    e.add_argument("--keep-fillers", dest="cut_fillers", action="store_false", default=None)
    e.add_argument("--draft", action="store_true", help="720p 빠른 미리보기")
    e.add_argument("--out", help="출력 파일 이름 (확장자 제외)")
    e.set_defaults(func=cmd_edit)

    r = sub.add_parser("analyze", help="레퍼런스 쇼츠의 컷 리듬/훅/음량 분석")
    r.add_argument("video")
    r.add_argument("--transcript", action="store_true")
    r.add_argument("--model", default="small")
    r.add_argument("--threshold", type=float, default=0.30, help="장면 전환 민감도 (낮을수록 민감)")
    r.set_defaults(func=cmd_analyze)

    f = sub.add_parser("fetch", help="구글드라이브/드롭박스/URL에서 원본 받기 → inbox/")
    f.add_argument("url")
    f.add_argument("--dest")
    f.set_defaults(func=cmd_fetch)

    b = sub.add_parser("background", help="사람만 오려 다른 배경에 합성 (설정 JSON)")
    b.add_argument("config")
    b.set_defaults(func=cmd_background)

    v = sub.add_parser("voiceover", help="대본을 장면 시간에 맞춰 목소리로 얹기 (설정 JSON)")
    v.add_argument("config")
    v.set_defaults(func=cmd_voiceover)

    s = sub.add_parser("styles", help="스타일 목록")
    s.set_defaults(func=cmd_styles)

    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
