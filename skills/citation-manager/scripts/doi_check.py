#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doi_check.py — DOI 归一化与在线核验（可选，默认离线）。

在线方式：HEAD https://doi.org/<doi>，3xx 视为已注册，404 视为不存在，超时/断网记 offline。
结果写进 library.tsv 的 doi_online 列，供模型判断条目真伪；模型不编造检索结果。

用法：
  python doi_check.py --lib work/lib/library.json --out work/lib/doi_status.json --online
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def normalize(doi: str) -> str:
    d = (doi or "").strip()
    d = re.sub(r"(?i)^(https?://(dx\.)?doi\.org/|doi:\s*)", "", d)
    return d.rstrip(".").strip()


def check(doi: str, timeout: float = 5.0) -> dict:
    d = normalize(doi)
    if not d:
        return {"doi": "", "status": "missing"}
    if not DOI_RE.match(d):
        return {"doi": d, "status": "malformed"}
    req = urllib.request.Request("https://doi.org/" + d, method="HEAD",
                                 headers={"User-Agent": "citation-manager/1.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"doi": d, "status": "registered", "http": r.status}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"doi": d, "status": "not_found", "http": 404}
        if 300 <= e.code < 400:
            return {"doi": d, "status": "registered", "http": e.code}
        return {"doi": d, "status": "error", "http": e.code}
    except Exception as ex:
        return {"doi": d, "status": "offline", "error": type(ex).__name__}


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description="DOI 核验")
    ap.add_argument("--lib", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--online", action="store_true", help="联网核验（默认只做形态检查）")
    args = ap.parse_args(argv)

    lib = json.loads(Path(args.lib).read_text(encoding="utf-8-sig"))
    result = {}
    for e in lib.get("entries", []):
        doi = e.get("doi") or ""
        result[e["id"]] = check(doi) if args.online else {
            "doi": normalize(doi),
            "status": "not_checked_online" if doi else "missing",
            **({"malformed": True} if doi and not DOI_RE.match(normalize(doi)) else {}),
        }
    out = Path(args.out) if args.out else Path(args.lib).with_name("doi_status.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    counts = {}
    for v in result.values():
        counts[v["status"]] = counts.get(v["status"], 0) + 1
    print(json.dumps({"ok": True, "n": len(result), "counts": counts, "out": str(out),
                      "online": bool(args.online)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
