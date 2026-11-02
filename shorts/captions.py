"""트렌디한 쇼츠 자막(ASS) 생성: 짧은 덩어리, 말하는 단어 하이라이트, 팝 애니메이션, 상단 제목."""

import re

PLAY_W, PLAY_H = 1080, 1920

DEFAULT_EMPHASIS = [
    "진짜", "대박", "무조건", "절대", "꼭", "완전", "최고", "미쳤", "레전드", "꿀팁",
    "비밀", "손해", "공짜", "무료", "필수", "충격", "실화", "역대급", "주의", "핵심",
]


# ---------- 색/시간 유틸 ----------

def ass_color(hex_color, alpha=None):
    """'#RRGGBB' 또는 '#RRGGBBAA'(AA=불투명도) → ASS '&HAABBGGRR'."""
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    if alpha is None:
        a = 255 - int(h[6:8], 16) if len(h) == 8 else 0
    else:
        a = alpha
    return f"&H{a:02X}{b}{g}{r}".upper()


def inline_color(hex_color):
    h = hex_color.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def ass_time(t):
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def srt_time(t):
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def escape(text):
    return text.replace("\\", "＼").replace("{", "(").replace("}", ")")


# ---------- 텍스트 정리 ----------

def clean_word(text, replace=None):
    """ASR 오타 교정 + 쉼표/마침표 제거 (?! 는 살린다)."""
    for k, v in (replace or {}).items():
        text = text.replace(k, v)
    text = text.strip()
    text = re.sub(r"[.,…·~\"'“”‘’]+$", "", text)
    text = re.sub(r"^[,.\s]+", "", text)
    return text


def is_emphasis(text, terms):
    if re.search(r"\d", text):
        return True
    return any(t and t in text for t in terms)


def ends_sentence(raw):
    return bool(re.search(r"[.?!…]$", raw.strip()))


# ---------- 덩어리(한 화면에 뜨는 자막 단위) 만들기 ----------

def build_chunks(words, cap, replace=None, emphasis=None):
    terms = list(DEFAULT_EMPHASIS) + list(emphasis or [])
    max_chars = cap.get("max_chars", 14)
    max_words = cap.get("max_words", 4)
    gap_break = cap.get("gap_break", 0.35)

    items = []
    for w in words:
        t = clean_word(w["text"], replace)
        if not t:
            continue
        items.append({**w, "disp": t, "emph": is_emphasis(t, terms),
                      "eos": ends_sentence(w["text"])})

    chunks, cur = [], []

    def flush():
        if cur:
            chunks.append({"words": list(cur)})
            cur.clear()

    for it in items:
        if cur:
            length = sum(len(x["disp"]) for x in cur) + len(cur) + len(it["disp"])
            over = length > max_chars or len(cur) >= max_words
            # '한 번에', '첫 번째', '두 개' 처럼 한 글자 관형어 뒤에서는 끊지 않는다
            if len(cur[-1]["disp"]) == 1 and length <= max_chars + 5:
                over = False
            if over or it["start"] - cur[-1]["end"] > gap_break or cur[-1]["eos"]:
                flush()
        cur.append(it)
    flush()

    for i, c in enumerate(chunks):
        ws = c["words"]
        c["start"] = ws[0]["start"]
        last = ws[-1]["end"]
        nxt = chunks[i + 1]["words"][0]["start"] if i + 1 < len(chunks) else None
        if nxt is None:
            end = last + 0.3
        elif nxt - last < 0.6:  # 짧은 틈은 다음 자막까지 이어 붙여 깜빡임 방지
            end = nxt
        else:
            end = min(last + 0.25, nxt)
        c["end"] = max(end, c["start"] + 0.2)
        c["lines"] = split_lines(ws, cap)
    return chunks


def split_lines(ws, cap):
    """2줄 허용 스타일이면 글자 수가 균형 맞게 줄을 나눈다. 반환: [[word_idx...], ...]."""
    idx = list(range(len(ws)))
    max_lines = cap.get("max_lines", 1)
    line_chars = cap.get("line_chars", 99)
    total = sum(len(w["disp"]) for w in ws) + len(ws) - 1
    if max_lines < 2 or total <= line_chars or len(ws) < 2:
        return [idx]
    best, best_cost = 1, None
    for k in range(1, len(ws)):
        a = sum(len(w["disp"]) for w in ws[:k]) + k - 1
        b = total - a - 1
        cost = abs(a - b) + (100 if max(a, b) > line_chars + 3 else 0)
        if len(ws[k - 1]["disp"]) == 1:
            cost += 50
        if best_cost is None or cost < best_cost:
            best, best_cost = k, cost
    return [idx[:best], idx[best:]]


# ---------- ASS 출력 ----------

def _style_line(name, font, size, primary, outline_c, back_c, outline, shadow,
                border_style=1, align=5, spacing=0, bold=0):
    return (f"Style: {name},{font},{size},{primary},&H000000FF,{outline_c},{back_c},"
            f"{bold},0,0,0,100,100,{spacing},0,{border_style},{outline},{shadow},{align},40,40,0,1")


def _chunk_text(chunk, active, cap, first_event):
    base = inline_color(cap["color"])
    act = inline_color(cap.get("active_color", cap["color"]))
    emc = inline_color(cap.get("emphasis_color", cap.get("active_color", cap["color"])))
    pop_word = cap.get("pop") == "word"
    parts_lines = []
    for line in chunk["lines"]:
        parts = []
        for i in line:
            w = chunk["words"][i]
            text = escape(w["disp"])
            if i == active and cap.get("karaoke", True):
                tag = f"\\1c{act}"
                if pop_word:
                    s = cap.get("word_pop_scale", 118)
                    tag += f"\\fscx{s}\\fscy{s}\\t(0,110,\\fscx104\\fscy104)"
                    parts.append(f"{{{tag}}}{text}{{\\1c{base}\\fscx100\\fscy100}}")
                else:
                    parts.append(f"{{{tag}}}{text}{{\\1c{base}}}")
            elif w["emph"]:
                parts.append(f"{{\\1c{emc}}}{text}{{\\1c{base}}}")
            else:
                parts.append(text)
        parts_lines.append(" ".join(parts))
    return "\\N".join(parts_lines)


def _anim_prefix(cap, first_event):
    pop = cap.get("pop", "chunk")
    if pop == "chunk" and first_event:
        return "\\fscx72\\fscy72\\t(0,80,\\fscx108\\fscy108)\\t(80,150,\\fscx100\\fscy100)"
    if pop == "fade" and first_event:
        return "\\fad(120,0)"
    return ""


def title_text(title, tcfg):
    """'아이패드로 *쇼츠 편집*|끝내는 법' → 강조색/줄바꿈 적용된 ASS 텍스트."""
    base = inline_color(tcfg["color"])
    acc = inline_color(tcfg.get("accent_color", tcfg["color"]))
    lines = re.split(r"\s*(?:\||\\n|\n)\s*", title.strip())
    out = []
    for line in lines:
        line = escape(line)
        line = re.sub(r"\*(.+?)\*", lambda m: f"{{\\1c{acc}}}{m.group(1)}{{\\1c{base}}}", line)
        out.append(line)
    return "\\N".join(out)


def write_ass(path, chunks, style, geom, duration, title=None):
    cap = style["caption"]
    tcfg = style.get("title", {})
    cbox = cap.get("box", False)
    shadow_c = ass_color(cap.get("shadow_color", "#00000099"))
    cap_style = _style_line(
        "Cap", cap["font"], cap["size"], ass_color(cap["color"]),
        ass_color(cap.get("box_color", "#000000") if cbox else cap.get("outline_color", "#000000")),
        shadow_c, cap.get("outline", 6), cap.get("shadow", 0),
        border_style=3 if cbox else 1, spacing=cap.get("spacing", 0),
    )
    tbox = tcfg.get("box", False)
    title_style = _style_line(
        "Title", tcfg.get("font", cap["font"]), tcfg.get("size", 96),
        ass_color(tcfg.get("color", "#FFFFFF")),
        ass_color(tcfg.get("box_color", "#000000") if tbox else tcfg.get("outline_color", "#000000")),
        ass_color(tcfg.get("shadow_color", "#00000099")),
        tcfg.get("outline", 8), tcfg.get("shadow", 0),
        border_style=3 if tbox else 1, spacing=tcfg.get("spacing", 0),
    )

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {PLAY_W}",
        f"PlayResY: {PLAY_H}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        cap_style,
        title_style,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    if title:
        an, x, y = geom["title_pos"]
        mode = tcfg.get("mode", "persistent")
        end = duration if mode == "persistent" else min(duration, tcfg.get("hook_seconds", 3.0))
        anim = "\\fscx80\\fscy80\\t(0,120,\\fscx104\\fscy104)\\t(120,200,\\fscx100\\fscy100)"
        if mode != "persistent":
            anim += "\\fad(0,250)"
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(end)},Title,,0,0,0,,"
                     f"{{\\an{an}\\pos({x},{y}){anim}}}{title_text(title, tcfg)}")

    an, x, y = geom["caption_pos"]
    for c in chunks:
        ws = c["words"]
        if cap.get("karaoke", True):
            for i, w in enumerate(ws):
                s = c["start"] if i == 0 else w["start"]
                e = ws[i + 1]["start"] if i + 1 < len(ws) else c["end"]
                if e - s < 0.02:
                    continue
                pre = f"\\an{an}\\pos({x},{y})" + _anim_prefix(cap, i == 0)
                lines.append(f"Dialogue: 2,{ass_time(s)},{ass_time(e)},Cap,,0,0,0,,"
                             f"{{{pre}}}{_chunk_text(c, i, cap, i == 0)}")
        else:
            pre = f"\\an{an}\\pos({x},{y})" + _anim_prefix(cap, True)
            lines.append(f"Dialogue: 2,{ass_time(c['start'])},{ass_time(c['end'])},Cap,,0,0,0,,"
                         f"{{{pre}}}{_chunk_text(c, -1, cap, True)}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_srt(path, chunks):
    out = []
    for n, c in enumerate(chunks, 1):
        text = "\n".join(" ".join(c["words"][i]["disp"] for i in line) for line in c["lines"])
        out.append(f"{n}\n{srt_time(c['start'])} --> {srt_time(c['end'])}\n{text}\n")
    path.write_text("\n".join(out), encoding="utf-8")
