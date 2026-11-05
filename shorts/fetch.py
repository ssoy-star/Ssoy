"""아이패드에서 올린 원본을 받아온다: 구글 드라이브(파일/폴더)·드롭박스 공유 링크 또는 직접 URL.

Claude 앱 첨부는 30MB 제한이 있어서, 큰 원본은 이 경로로 받는다.
"""

import re
import subprocess
from pathlib import Path


def _drive_folder_id(url):
    m = re.search(r"drive\.google\.com/drive/(?:u/\d+/)?folders/([\w-]+)", url)
    return m.group(1) if m else None


def _drive_file_id(url):
    m = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?id=)([\w-]+)", url)
    return m.group(1) if m else None


def normalize_url(url):
    if "dropbox.com" in url:
        url = re.sub(r"([?&])dl=0", r"\1dl=1", url)
        if "dl=1" not in url and "raw=1" not in url:
            url += ("&" if "?" in url else "?") + "dl=1"
    return url


def fetch(url, dest_dir):
    """받은 파일 경로 리스트를 돌려준다. 폴더 링크는 폴더 안 파일을 전부 받는다."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    folder = _drive_folder_id(url)
    file_id = _drive_file_id(url)
    if folder or file_id:
        import gdown  # 대용량 파일의 '바이러스 검사 불가' 확인 페이지까지 처리해 준다

        if folder:
            out = dest_dir / folder
            files = gdown.download_folder(url, output=str(out), quiet=False)
            if not files:
                raise RuntimeError("폴더에서 받은 파일이 없습니다. 공유가 '링크가 있는 모든 사용자'인지 확인하세요.")
            return [Path(f) for f in files]
        path = gdown.download(id=file_id, output=str(dest_dir) + "/", quiet=False)
        if not path:
            raise RuntimeError("다운로드 실패. 공유가 '링크가 있는 모든 사용자'인지 확인하세요.")
        return [Path(path)]

    before = set(dest_dir.iterdir())
    subprocess.run(
        ["curl", "-fL", "--retry", "3", "-J", "-O", "--output-dir", str(dest_dir),
         normalize_url(url)],
        check=True,
    )
    new = sorted(set(dest_dir.iterdir()) - before, key=lambda p: p.stat().st_mtime)
    if not new:
        raise RuntimeError("다운로드된 파일이 없습니다 (같은 이름의 파일이 이미 있을 수 있음).")
    return [new[-1]]
