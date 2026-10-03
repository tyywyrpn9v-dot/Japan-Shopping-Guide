#!/usr/bin/env python3
"""Add LocationSmart and NAVITIME shops that are not already in the catalog.

Official rows in stores.ts and more-stores.ts always win.
This script does not edit those files.
NAVITIME currently returns 403 to a plain request, so those shops are not invented.
LocationSmart's public map has a name, hours text, coordinates, and sometimes an
official URL. It does not give a street address.
"""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "data" / "external-stores.ts"
REPORT = ROOT / "data" / "external-fill-report.json"
CTX = ssl.create_default_context()
UA = {"User-Agent": "joukyucho-external-fill/1.0", "Accept": "application/json", "Referer": "https://www.locationsmart.org/"}

# tag, fallback brand id. Official brand names override the fallback.
TAGS = [
    ("_shopping_100kin", "ls-hundred"),
    ("_shopping_drug", "ls-drugstore"),
    ("_shopping_homecenter", "ls-home"),
    ("_shopping_super", "ls-market"),
    ("_shopping_discount", "ls-discount"),
    ("_shopping_recycle", "ls-recycle"),
    ("_shopping_shoes", "ls-shoes"),
    ("_shopping_kaden", "ls-appliance"),
    ("_shopping_furniture", "ls-furniture"),
    ("_shopping_zakka", "ls-zakka"),
    ("_shopping_handcraft", "ls-craft"),
    ("_shopping_book", "ls-books"),
    ("_shopping_comic", "ls-hobby"),
    ("_shopping_gift", "ls-gift"),
    ("_shopping_tea", "ls-tea"),
]
NAVI_URLS = [
    "https://japantravel.navitime.com/zh-tw/area/jp/destinations/A00/spot/?categoryCode=0206",
    "https://japantravel.navitime.com/zh-tw/area/jp/destinations/A00/spot/?categoryCode=0206018",
]
CENTROIDS = [
    ("01", 43.4, 142.4), ("02", 40.8, 140.7), ("03", 39.6, 141.4), ("04", 38.3, 140.9),
    ("05", 39.7, 140.4), ("06", 38.4, 140.1), ("07", 37.4, 140.2), ("08", 36.3, 140.3),
    ("09", 36.6, 139.8), ("10", 36.4, 138.9), ("11", 35.9, 139.4), ("12", 35.5, 140.1),
    ("13", 35.7, 139.5), ("14", 35.4, 139.4), ("15", 37.5, 138.9), ("16", 36.6, 137.2),
    ("17", 36.7, 136.7), ("18", 35.8, 136.2), ("19", 35.6, 138.6), ("20", 36.2, 138.0),
    ("21", 35.8, 137.0), ("22", 35.0, 138.3), ("23", 35.0, 137.2), ("24", 34.5, 136.5),
    ("25", 35.2, 136.1), ("26", 35.1, 135.5), ("27", 34.6, 135.5), ("28", 34.8, 134.8),
    ("29", 34.3, 135.8), ("30", 33.9, 135.4), ("31", 35.4, 133.8), ("32", 35.0, 132.5),
    ("33", 34.7, 133.8), ("34", 34.4, 132.7), ("35", 34.2, 131.5), ("36", 33.9, 134.2),
    ("37", 34.2, 134.0), ("38", 33.6, 132.8), ("39", 33.4, 133.3), ("40", 33.6, 130.6),
    ("41", 33.3, 130.1), ("42", 32.9, 129.8), ("43", 32.6, 130.8), ("44", 33.2, 131.4),
    ("45", 32.0, 131.3), ("46", 31.5, 130.5), ("47", 26.3, 127.8),
]


def get(url: str, accept: str = "application/json") -> tuple[int, bytes]:
    headers = dict(UA)
    headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25, context=CTX) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as err:
        return err.code, b""
    except Exception:
        return 0, b""


def shops_in(tag: str, north: float, south: float, west: float, east: float) -> list[dict]:
    url = (
        "https://www.locationsmart.org/ttypeg/php/g.php"
        f"?tag={tag}&n={north}&s={south}&w={west}&e={east}&z=17&epoch=1"
    )
    status, body = get(url)
    if status != 200 or not body:
        return []
    try:
        return json.loads(body).get("shops") or []
    except json.JSONDecodeError:
        return []


def detail(shop_id: int) -> dict | None:
    status, body = get(f"https://www.locationsmart.org/ttypeg/php/d.php?id={abs(shop_id)}")
    if status != 200 or not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def cells() -> list[tuple[float, float, float, float]]:
    found = []
    lat = 31.0
    while lat < 45.6:
        lon = 129.0
        while lon < 146.0:
            found.append((lat + 0.45, lat, lon, lon + 0.45))
            lon += 0.45
        lat += 0.45
    lat = 24.0
    while lat < 28.4:
        lon = 123.0
        while lon < 131.5:
            found.append((lat + 0.5, lat, lon, lon + 0.5))
            lon += 0.5
        lat += 0.5
    return found


def norm(value: str) -> str:
    return re.sub(r"[\s・･.\-]+", "", value).casefold()


def load_official() -> tuple[set[str], dict[str, set[str]], list[tuple[str, str]]]:
    urls: set[str] = set()
    names: dict[str, set[str]] = {}
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src/data/stores.ts", ROOT / "src/data/more-stores.ts")
    )
    for chunk in re.split(r"\n  \{", text)[1:]:
        block = chunk.split("\n  },")[0]
        brand = re.search(r'brandId: "([^"]+)"', block)
        name = re.search(r'nameJa: "((?:\\.|[^"\\])*)"', block)
        url = re.search(r'url: "((?:\\.|[^"\\])*)"', block)
        if brand and name:
            names.setdefault(brand.group(1), set()).add(norm(name.group(1).replace('\\"', '"')))
        if url:
            urls.add(url.group(1).replace("\\/", "/"))
    brands = (ROOT / "src/data/brands.ts").read_text(encoding="utf-8")
    aliases: list[tuple[str, str]] = []
    for chunk in re.split(r"\n  \{", brands)[1:]:
        block = chunk.split("\n  },")[0]
        brand_id = re.search(r'id: "([^"]+)"', block)
        if not brand_id or brand_id.group(1).startswith("ls-"):
            continue
        for key in ("ja", "en", "zh"):
            value = re.search(rf'{key}: "((?:\\.|[^"\\])*)"', block)
            if value:
                aliases.append((norm(value.group(1)), brand_id.group(1)))
    return urls, names, aliases


def match_brand(tag_name: str, english: str, aliases: list[tuple[str, str]]) -> str | None:
    for label in (tag_name, english):
        key = norm(label)
        if not key:
            continue
        for alias, brand_id in aliases:
            if alias and (alias == key or alias in key or key in alias):
                return brand_id
    return None


def pref_of(lat: float, lon: float) -> str:
    if lat < 28.5 and lon < 132:
        return "47"
    if lat > 41.3 and lon > 139.2:
        return "01"
    best, dist = "13", 999.0
    for code, clat, clon in CENTROIDS:
        delta = (lat - clat) ** 2 + (lon - clon) ** 2
        if delta < dist:
            best, dist = code, delta
    return best


def clock(hour: int, minute: int, afternoon: bool) -> str:
    if afternoon and hour < 12:
        hour += 12
    if hour == 24:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def parse_hours(info: str) -> str:
    text = info or ""
    if "24時間" in text or "24H" in text.upper():
        return "H24"
    match = re.search(r"(\d{1,2})[:：](\d{2})\s*[~〜～\-－]\s*(\d{1,2})[:：](\d{2})", text)
    if match:
        return f'daily("{int(match.group(1)):02d}:{match.group(2)}", "{int(match.group(3)):02d}:{match.group(4)}")'
    match = re.search(
        r"(午前|午後)?\s*(\d{1,2})時(?:(\d{1,2})分)?\s*[~〜～\-－]\s*(午前|午後)?\s*(\d{1,2})時(?:(\d{1,2})分)?",
        text,
    )
    if match:
        open_ = clock(int(match.group(2)), int(match.group(3) or 0), match.group(1) == "午後")
        close = clock(int(match.group(5)), int(match.group(6) or 0), match.group(4) == "午後" or (match.group(4) is None and match.group(1) == "午後"))
        return f'daily("{open_}", "{close}")'
    return "UNKNOWN"


def esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def collect_ids() -> dict[int, str]:
    boxes = cells()
    found: dict[int, str] = {}
    jobs = [(tag, brand, *box) for tag, brand in TAGS for box in boxes]
    print(f"map requests {len(jobs)}", flush=True)
    done = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(shops_in, tag, n, s, w, e): brand for tag, brand, n, s, w, e in jobs}
        for future in as_completed(futures):
            brand = futures[future]
            done += 1
            for shop in future.result():
                shop_id = shop.get("shop_id")
                if isinstance(shop_id, int):
                    found.setdefault(abs(shop_id), brand)
            if done % 400 == 0:
                print(f"tiles {done}/{len(jobs)} ids {len(found)}", flush=True)
    return found


def main() -> None:
    navitime = []
    for url in NAVI_URLS:
        status, body = get(url, "text/html")
        navitime.append({"url": url, "fetchStatus": status, "bytes": len(body)})
        print("navitime", status, url, flush=True)

    urls, names, aliases = load_official()
    ids = collect_ids()
    # Spread brands instead of taking only the first category.
    by_brand: dict[str, list[int]] = {}
    for shop_id, brand in ids.items():
        by_brand.setdefault(brand, []).append(shop_id)
    ordered: list[tuple[int, str]] = []
    while any(by_brand.values()):
        for brand in list(by_brand):
            bucket = by_brand[brand]
            if bucket:
                ordered.append((bucket.pop(), brand))
    ordered = ordered[:8000]
    print(f"details {len(ordered)} of {len(ids)}", flush=True)

    kept = []
    skipped = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(detail, shop_id): (shop_id, brand) for shop_id, brand in ordered}
        finished = 0
        for future in as_completed(futures):
            shop_id, fallback = futures[future]
            finished += 1
            row = future.result()
            if finished % 500 == 0:
                print(f"details done {finished} kept {len(kept)}", flush=True)
            if not row or "lat" not in row or "name" not in row:
                continue
            official = (row.get("url") or "").replace("\\/", "/")
            if official and official in urls:
                skipped += 1
                continue
            brand_id = match_brand(row.get("tag_name") or "", row.get("tag_e_name") or "", aliases) or fallback
            store_name = (row.get("name") or "").strip()
            chain = (row.get("tag_name") or "").strip()
            full_name = store_name if chain and chain in store_name else f"{chain} {store_name}".strip()
            if norm(full_name) in names.get(brand_id, set()) or norm(store_name) in names.get(brand_id, set()):
                skipped += 1
                continue
            info = row.get("info") or ""
            kept.append(
                {
                    "id": f"ls-{shop_id}",
                    "brandId": brand_id,
                    "brandLabel": chain,
                    "name": full_name,
                    "pref": pref_of(float(row["lat"]), float(row["lon"])),
                    "lat": float(row["lat"]),
                    "lon": float(row["lon"]),
                    "hours": parse_hours(info),
                    "info": info,
                    "url": official,
                }
            )
    kept.sort(key=lambda item: (item["pref"], item["name"]))
    lines = [
        'import type { Hours, Store } from "./types";',
        "",
        'const H24: Hours = { kind: "24h" };',
        'const UNKNOWN: Hours = { kind: "unknown" };',
        'const daily = (open: string, close: string): Hours => ({ kind: "daily", open, close });',
        "",
        "/** LocationSmart 公開地圖補入。與官網名冊衝突的店已略過。沒有街道地址。 */",
        "export const EXTERNAL_STORES: Store[] = [",
    ]
    for item in kept:
        hours = item["hours"] if item["hours"] in {"H24", "UNKNOWN"} or item["hours"].startswith("daily(") else "UNKNOWN"
        url_line = f'\n    url: "{esc(item["url"])}",' if item["url"] else ""
        lines.append(
            "  {\n"
            f'    id: "{item["id"]}",\n'
            f'    brandId: "{item["brandId"]}",\n'
            f'    brandLabel: "{esc(item["brandLabel"])}",\n'
            f'    nameJa: "{esc(item["name"])}",\n'
            f'    nameZh: "{esc(item["name"])}",\n'
            f'    prefCode: "{item["pref"]}",\n'
            f'    city: "",\n'
            f'    address: "{item["lat"]:.5f},{item["lon"]:.5f}",\n'
            f"    lat: {item['lat']},\n"
            f"    lon: {item['lon']},\n"
            f"    hours: {hours},\n"
            f'    hoursNote: "非官網 LocationSmart。官網已有的店維持官網資料。",\n'
            f'    notice: "{esc(item["info"])}",\n'
            f"{url_line}\n"
            f'    source: "listing",\n'
            f'    asOf: "2026-10-03",\n'
            "  },"
        )
    lines.append("];")
    lines.append("")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    report = {
        "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "policy": "LocationSmart and NAVITIME may fill gaps. Official catalog wins on the same URL or same brand and store name.",
        "navitime": navitime,
        "locationSmartIds": len(ids),
        "detailRequests": len(ordered),
        "added": len(kept),
        "skippedAsOfficial": skipped,
        "streetAddress": False,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"added": len(kept), "skipped": skipped, "ids": len(ids)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
