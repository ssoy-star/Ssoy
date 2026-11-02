"""아이패드에서 올린 원본을 받아온다: 구글 드라이브/드롭박스 공유 링크 또는 직접 URL."""

import re
import subprocess
from pathlib import Path


def normalize_url(url):
    m = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?id=)([\w-]+)", url)
    if m:
        return f"https://drive.usercontent.google.com/download?id={m.group(1)}&export=download&confirm=t"
    if "dropbox.com" in url:
        url = re.sub(r"([?&])dl=0", r"\1dl=1", url)
        if "dl=1" not in url and "raw=1" not in url:
            url += ("&" if "?" in url else "?") + "dl=1"
    return url


def fetch(url, dest_dir):
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    before = set(dest_dir.iterdir())
    subprocess.run(
        ["curl", "-fL", "--retry", "3", "-J", "-O", "--output-dir", str(dest_dir),
         normalize_url(url)],
        check=True,
    )
    new = sorted(set(dest_dir.iterdir()) - before, key=lambda p: p.stat().st_mtime)
    if not new:
        raise RuntimeError("다운로드된 파일이 없습니다 (같은 이름의 파일이 이미 있을 수 있음).")
    return new[-1]
