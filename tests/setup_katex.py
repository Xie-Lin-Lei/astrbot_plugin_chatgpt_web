"""下载 KaTeX 静态资源到 assets/katex（只保留 woff2 字体，约 1MB）。开发期运行一次即可。"""

import io
import os
import sys
import tarfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "assets" / "katex"
URL = "https://registry.npmjs.org/katex/-/katex-0.16.11.tgz"


def main() -> None:
    print("downloading", URL)
    resp = httpx.get(URL, proxy=os.environ.get("CHATGPT_WEB_PROXY") or None, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    DEST.mkdir(parents=True, exist_ok=True)
    kept = 0
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        for member in tar.getmembers():
            name = member.name
            if not name.startswith("package/dist/"):
                continue
            rel = name[len("package/dist/") :]
            if not rel or member.isdir():
                continue
            if rel.startswith("contrib/"):
                continue
            if rel.endswith((".woff", ".ttf", ".map")):
                continue
            if rel not in ("katex.min.css", "katex.min.js") and not rel.startswith("fonts/"):
                continue
            if rel.startswith("fonts/") and not rel.endswith(".woff2"):
                continue
            target = DEST / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                continue
            target.write_bytes(source.read())
            kept += 1
    total = sum(p.stat().st_size for p in DEST.rglob("*") if p.is_file())
    print(f"kept {kept} files, {total / 1024:.0f} KB -> {DEST}")


if __name__ == "__main__":
    sys.exit(main())
