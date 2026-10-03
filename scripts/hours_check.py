#!/usr/bin/env python3
"""Check a few official store-hour pages and write a report.

No AI. Standard library only. This does not edit src/data/stores.ts.
A wrong parse must not overwrite the guide people actually see.

What it can read:
  - Tokyo Shirts store list (BRICK HOUSE rows)
  - ちいかわもぐもぐ本舗 store pages

What it only probes:
  - chiikawa-info.jp and 世界堂 often return 403 to a plain script
  - Kiddy Land, Itoya, Kawachi answer, but each layout is different
"""

from __future__ import annotations

import gzip
import json
import re
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORES = ROOT / "src" / "data" / "stores.ts"
OUT = ROOT / "data" / "hours-check" / "latest.json"
UA = "joukyucho-hours-check/1.0 (monthly catalog check; contact: store-hours)"
BRICK_LIST = "https://www.tokyo-shirt.co.jp/shop/store/list.aspx"
PROBES = [
    ("chiikawa-info", "https://chiikawa-info.jp/ck_land.html", "頁面多是圖片，程式也常被 403。"),
    ("sekaido", "https://www.sekaido.co.jp/store/77/", "店頁會擋簡單程式。"),
    ("kiddyland", "https://www.kiddyland.co.jp/shoplist/", "HTML 讀得到，但每店版面不同，未自動改時間。"),
    ("itoya", "https://www.ito-ya.co.jp/ginza", "HTML 讀得到，未自動改時間。"),
    ("kawachi", "https://gakubuchi.jp/shop/", "HTML 讀得到，未自動改時間。"),
    ("bakery", "https://chiikawabakery.jp/", "可能被 403。讀得到也不自動改，以免快閃店結束仍被寫進名冊。"),
    ("kiddyland-list", "https://www.kiddyland.co.jp/shoplist/", "只記錄頁面能不能打開，不覆寫名冊。"),
    ("daiso", "https://www.daiso-sangyo.co.jp/shop", "搜尋頁，沒有一次列出全國地址。"),
    ("navitime-0206", "https://japantravel.navitime.com/zh-tw/area/jp/destinations/A00/spot/?categoryCode=0206", "常回 403。"),
    ("3coins", "https://www.palcloset.jp/addons/pal/shoplist/?b=3coins", "店名列表，地址多在另一頁。"),
    ("nitori", "https://www.nitori.co.jp/en/shop/", "英文搜尋頁，不是全國清單。"),
    ("yodobashi", "https://www.yodobashi.com/ec/store/list/", "可能逾時。名冊已有集團清單。"),
    ("bic", "https://www.biccamera.com/bc/i/shop/shoplist/index.jsp", "可能逾時。"),
    ("hands", "https://info.hands.net/ch2/list/", "繁中列表頁，地址未必在同一頁。"),
    ("loft", "https://www.loft.co.jp/shop_list_en/", "英文店址頁。"),
    ("seria", "https://shop.seria-group.com/seria/arealist", "地圖頁，程式讀不到地址。"),
    ("watts", "https://www.watts-jp.com/shop/", "搜尋頁。"),
    ("cando", "https://en.shopinfo.cando-web.co.jp/all/?page=1", "分頁清單，每月只記第一頁是否打得開。"),
]


def fetch(url: str) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "text/html", "Accept-Encoding": "identity"},
    )
    try:
        with urllib.request.urlopen(req, timeout=25, context=ssl.create_default_context()) as res:
            raw = res.read()
            if raw[:2] == b"\x1f\x8b" or (res.headers.get("content-encoding") or "").lower() == "gzip":
                raw = gzip.decompress(raw)
            charset = res.headers.get_content_charset() or "utf-8"
            return res.status, raw.decode(charset, "replace")
    except urllib.error.HTTPError as err:
        return err.code, ""
    except Exception as err:  # noqa: BLE001 — report any network failure
        return 0, str(err)


def norm(text: str) -> str:
    table = str.maketrans(
        {
            "：": ":",
            "－": "-",
            "ー": "-",
            "―": "-",
            "〜": "-",
            "～": "-",
            "ｰ": "-",
            "０": "0",
            "１": "1",
            "２": "2",
            "３": "3",
            "４": "4",
            "５": "5",
            "６": "6",
            "７": "7",
            "８": "8",
            "９": "9",
        }
    )
    return text.translate(table)


def pairs_from(text: str) -> list[tuple[str, str]]:
    times = re.findall(r"(\d{1,2}):(\d{2})", norm(text))
    clock = [f"{int(h):02d}:{m}" for h, m in times]
    return [(clock[i], clock[i + 1]) for i in range(0, len(clock) - 1, 2)]


def catalog_rows() -> list[dict]:
    text = STORES.read_text(encoding="utf-8")
    rows = []
    for chunk in re.split(r"\n  \{", text)[1:]:
        block = chunk.split("\n  },")[0]
        id_m = re.search(r'id: "([^"]+)"', block)
        brand_m = re.search(r'brandId: "([^"]+)"', block)
        url_m = re.search(r'url: "([^"]+)"', block)
        if not (id_m and brand_m and url_m):
            continue
        if "hours: UNKNOWN" in block:
            spans: list[tuple[str, str]] = []
        else:
            daily = re.search(r'daily\("(\d{2}:\d{2})", "(\d{2}:\d{2})"\)', block)
            if daily:
                spans = [(daily.group(1), daily.group(2))]
            else:
                found = re.findall(r'open: "(\d{2}:\d{2})", close: "(\d{2}:\d{2})"', block)
                spans = list(dict.fromkeys(found))
        rows.append({"id": id_m.group(1), "brandId": brand_m.group(1), "url": url_m.group(1), "spans": spans})
    return rows


def brick_site_rows(html: str) -> dict[str, dict]:
    found: dict[str, dict] = {}
    pattern = re.compile(
        r'href="(/shop/store/o\d+/)"[\s\S]*?'
        r'class="block-store-list--store-name">\s*<a[^>]*>([^<]+)</a>[\s\S]*?'
        r'class="block-store-list--store-address">\s*<div>(.*?)</div>[\s\S]*?'
        r'class="white-space-pre-wrap">(.*?)</div>',
        re.S,
    )
    for href, name, address, hours_html in pattern.findall(html):
        if "BRICK" not in address.upper() and "ブリック" not in address:
            continue
        lines = [re.sub(r"<[^>]+>", "", line).strip() for line in re.split(r"\n|<br\s*/?>", hours_html)]
        lines = [line for line in lines if line and not line.startswith("※")]
        first = lines[0] if lines else ""
        found[href] = {
            "name": re.sub(r"\s+", " ", name).strip(),
            "address": re.sub(r"\s+", " ", address).strip(),
            "hoursText": first,
            "spans": pairs_from(first),
        }
    return found


def mogu_hours(html: str) -> tuple[str, list[tuple[str, str]]]:
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    for i, line in enumerate(lines):
        if line.startswith("営業時間"):
            window = " ".join(lines[i : i + 2])
            spans = pairs_from(window)
            if spans:
                return window, spans[:1]
    return "", []


def compare(catalog: list[tuple[str, str]], site: list[tuple[str, str]]) -> str:
    if not site:
        return "site-unparsed"
    if not catalog:
        return "catalog-unknown"
    if set(catalog) == set(site):
        return "match"
    return "changed"


def main() -> None:
    rows = catalog_rows()
    report: dict = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "rewritesCatalog": False,
        "brickhouse": [],
        "mogumogu": [],
        "probes": [],
        "summary": {},
    }

    status, html = fetch(BRICK_LIST)
    site = brick_site_rows(html) if status == 200 else {}
    for row in rows:
        if row["brandId"] != "brickhouse":
            continue
        path = re.search(r"(/shop/store/o\d+/)", row["url"])
        key = path.group(1) if path else ""
        live = site.get(key)
        report["brickhouse"].append(
            {
                "id": row["id"],
                "url": row["url"],
                "fetchStatus": status,
                "catalog": row["spans"],
                "siteHours": None if not live else live["hoursText"],
                "siteSpans": [] if not live else live["spans"],
                "status": "fetch-failed" if status != 200 else ("missing-on-page" if not live else compare(row["spans"], live["spans"])),
            }
        )
        time.sleep(0.2)

    for row in rows:
        if row["brandId"] != "ckmogu":
            continue
        status, html = fetch(row["url"])
        text, spans = mogu_hours(html) if status == 200 else ("", [])
        report["mogumogu"].append(
            {
                "id": row["id"],
                "url": row["url"],
                "fetchStatus": status,
                "catalog": row["spans"],
                "siteHours": text,
                "siteSpans": spans,
                "status": "fetch-failed" if status != 200 else compare(row["spans"], spans),
            }
        )
        time.sleep(1)

    for name, url, note in PROBES:
        status, body = fetch(url)
        report["probes"].append(
            {
                "name": name,
                "url": url,
                "fetchStatus": status,
                "bytes": len(body),
                "note": note,
                "parsed": False,
            }
        )
        time.sleep(1)

    counts: dict[str, int] = {}
    for item in report["brickhouse"] + report["mogumogu"]:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    report["summary"] = counts
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"wrote": str(OUT.relative_to(ROOT)), "summary": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
