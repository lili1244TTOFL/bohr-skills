#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""preview.py — 把成品 docx 渲染成 PDF/PNG 预览；渲染器不存在就明说，不假装。

渲染器查找顺序：
  1 DSH LibreOffice Kit（$DSH_LO_NODE + $DSH_LO_CLI，或本机 DSH 安装的默认路径）
  2 环境声明的 office-renderer（/environment/resources.yaml）
  3 soffice（$SOFFICE_BIN / $LIBREOFFICE_BIN / PATH / 常见路径）

Kit 用 render 直接出 PNG（带 manifest），失败再 convert 出 PDF；soffice 走 --convert-to pdf。

用法：python preview.py --docx out/x_cited.docx --out out/preview
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DSH_NODE = Path(r"C:\Users\1244\AppData\Local\Programs\DeepSeek Harness\resources\runtime"
                r"\primary-runtime\dependencies\node\bin\node.exe")
DSH_CLI = Path(r"C:\Users\1244\AppData\Local\Programs\DeepSeek Harness\resources"
               r"\app.asar.unpacked\dsh\node_modules\@deepseek-ai\libreoffice-kit\lib\cli.js")
WINDOWS = [r"C:\Program Files\LibreOffice\program\soffice.exe",
           r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"]
POSIX = ["/usr/bin/soffice", "/usr/local/bin/soffice", "/opt/libreoffice/program/soffice",
         "/Applications/LibreOffice.app/Contents/MacOS/soffice"]


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def find_kit() -> tuple[str, str] | None:
    node = os.environ.get("DSH_LO_NODE")
    cli = os.environ.get("DSH_LO_CLI")
    if node and cli and Path(node).exists() and Path(cli).exists():
        return node, cli
    if DSH_NODE.exists() and DSH_CLI.exists():
        return str(DSH_NODE), str(DSH_CLI)
    return None


def _declared_renderer() -> str | None:
    for p in (Path("/environment/resources.yaml"),
              Path(os.environ.get("DSH_RESOURCES", "")) / "resources.yaml"):
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        m = re.search(r"name\s*:\s*office-renderer(.{0,400})", text, re.S)
        if m:
            q = re.search(r"path\s*:\s*(\S+)", m.group(1))
            if q and Path(q.group(1)).exists():
                return q.group(1)
    return None


def find_soffice(explicit: str | None) -> str | None:
    for cand in (explicit, os.environ.get("SOFFICE_BIN"), os.environ.get("LIBREOFFICE_BIN"),
                 _declared_renderer()):
        if cand and Path(cand).exists():
            return cand
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    for cand in WINDOWS + POSIX:
        if Path(cand).exists():
            return cand
    return None


def render_kit(node: str, cli: str, src: Path, out: Path) -> dict:
    """先 convert 出 PDF（版式可靠），再从 PDF 渲染 PNG（pdfium，页边距正确）。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    target = out
    n = 1
    while target.exists():
        target = out.parent / f"{out.name}-{n}"
        n += 1
    target.mkdir(parents=True, exist_ok=True)
    pdf = target / (src.stem + ".pdf")
    rc = subprocess.run([node, cli, "convert", "--input", str(src), "--output", str(pdf)],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    produced: list[str] = [str(pdf)] if pdf.exists() else []
    if not produced:
        return {"ok": False, "produced": [], "method": "libreoffice-kit", "png": 0,
                "skipped": "kit_convert_failed",
                "stderr": (rc.stderr or rc.stdout or "")[-300:]}
    png_dir = target / "png"
    rr = subprocess.run([node, cli, "render", "--input", str(pdf),
                         "--output-dir", str(png_dir), "--dpi", "120"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    pngs: list[str] = []
    try:
        man = json.loads(rr.stdout.strip().splitlines()[-1])
        for it in man.get("images") or []:
            p = it.get("path") if isinstance(it, dict) else it
            if p and Path(p).exists():
                pngs.append(str(p))
    except Exception:
        pngs = [str(p) for p in sorted(png_dir.glob("*.png"))] if png_dir.exists() else []
    produced.extend(pngs)
    return {"ok": True, "produced": produced, "method": "libreoffice-kit",
            "png": len(pngs), "skipped": None}


def render_soffice(exe: str, src: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", str(out), str(src)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    pdf = out / (src.stem + ".pdf")
    produced = [str(pdf)] if pdf.exists() else []
    pngs = []
    if produced and shutil.which("pdftoppm"):
        subprocess.run(["pdftoppm", "-r", "100", "-png", "-f", "1", "-l", "3", str(pdf),
                        str(out / "page")], capture_output=True, text=True)
        pngs = sorted(str(p) for p in out.glob("page*.png"))
        produced.extend(pngs)
    ok = bool(produced)
    return {"ok": ok, "produced": produced, "method": Path(exe).name,
            "png": len(pngs), "skipped": None if ok else "convert_failed",
            "stderr": (proc.stderr or "").strip()[-300:]}


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="渲染预览")
    ap.add_argument("--docx", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--soffice", default=None)
    args = ap.parse_args(argv)

    src = Path(args.docx)
    out = Path(args.out)
    kit = find_kit()
    if kit:
        res = render_kit(kit[0], kit[1], src, out)
        if res["ok"]:
            print(json.dumps(res, ensure_ascii=False))
            return 0
    exe = find_soffice(args.soffice)
    if not exe:
        print(json.dumps({"ok": True, "produced": [], "method": None,
                          "skipped": "no_renderer" if kit is None else "kit_render_failed",
                          "note": "未找到可用渲染器，未生成预览；已记入 material_gaps"},
                         ensure_ascii=False))
        return 0
    res = render_soffice(exe, src, out)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
