"""从 wowhead 采样属性曲线，生成 data/stat_curve.csv。

为什么要采样而不是直接查表：游戏的 `RandPropPoints` 只覆盖主属性，绿字和耐力
是另外两条曲线，而且比值随装等变化（详见 src/wowgear/curve.py 的说明）。

做法：拿三件代表性装备（大件/小件/武器），对每个装等问一次 wowhead 的 tooltip
接口，把返回值反算成"每万点权重折多少属性"，存成表。

    python tools/sample_curve.py            # 默认 250-346，每 4 级
    python tools/sample_curve.py --step 2   # 更密
    python tools/sample_curve.py --proxy http://127.0.0.1:7897

wowhead 部分地区直连会被 CloudFront 403，需要代理。装备 id 换了赛季要跟着换，
换成同部位、同 alloc 结构的任意一件即可（alloc 从库里读，不写死）。
"""

import argparse
import csv
import io
import json
import os
import re
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from wowgear.paths import DB_PATH, STAT_CURVE  # noqa: E402

# 五类部位各一件样本。**必须是对的部位**——我第一版拿 "Soulslither Spaulders"（肩）
# 当大件，结果头胸腿全用成中件系数，腿的绿字少算 25%。
# big=头(239033) mid=肩(268231) small=护腕(268217) jewel=戒指(251093, 无主属性) weapon=主手
SAMPLES = {
    "big": 239033,      # Hood of the Slithering Loa（头）
    "mid": 268231,      # Soulslither Spaulders（肩）
    "small": 268217,    # Rising Tide Wristguards（护腕）
    "jewel": 251093,    # Omission of Light（戒指，没有主属性）
    "weapon": 268201,   # Venomous Boneglaive（主手）
}
PRI_IDS = (3, 4, 5, 71, 72, 73, 74)
URL = "https://nether.wowhead.com/tooltip/item/{id}?dataEnv=1&locale=0&ilvl={ilvl}"


def fetch(item_id, ilvl, proxy=None):
    cmd = ["curl", "-s", "--max-time", "25", URL.format(id=item_id, ilvl=ilvl)]
    if proxy:
        cmd[1:1] = ["-x", proxy]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    try:
        raw = json.loads(out).get("tooltip", "")
    except (json.JSONDecodeError, AttributeError):
        return None
    txt = re.sub(r"&nbsp;|&amp;", " ", re.sub(r"<[^>]+>", " ", raw))
    sec = sum(int(v.replace(",", "")) for v in
              re.findall(r"\+\s*([\d,]+)\s+(?:Critical Strike|Haste|Mastery|Versatility)", txt))
    mp = re.search(r"\+\s*([\d,]+)\s+\[?(Agility|Strength|Intellect)", txt)
    ms = re.search(r"\+\s*([\d,]+)\s+Stamina", txt)
    out = {"pri": int(mp.group(1).replace(",", "")) if mp else 0, "sec": sec,
           "sta": int(ms.group(1).replace(",", "")) if ms else 0}
    # 戒指/项链没有主属性，不能因为"没匹配到敏捷"就判失败——那样整类采样全是空的
    if not (out["pri"] or out["sec"] or out["sta"]):
        return None
    return out


def allocs_of(conn, item_id):
    rows = conn.execute("SELECT stat_id, alloc FROM item_stats WHERE item_id = ?",
                        (item_id,)).fetchall()
    pri = sum(r["alloc"] for r in rows if r["stat_id"] in PRI_IDS)
    sta = sum(r["alloc"] for r in rows if r["stat_id"] == 7)
    sec = sum(r["alloc"] for r in rows if r["stat_id"] in (24, 25) or 31 <= r["stat_id"] <= 49)
    return pri, sta, sec


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=4)
    ap.add_argument("--lo", type=int, default=250)
    ap.add_argument("--hi", type=int, default=346)
    ap.add_argument("--proxy", default=os.environ.get("WOW_PROXY"))
    args = ap.parse_args(argv)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    base = {k: allocs_of(conn, v) for k, v in SAMPLES.items()}
    conn.close()
    print("alloc 结构:", base, flush=True)

    rows = []
    for ilvl in range(args.lo, args.hi + 1, args.step):
        row = {"ilvl": ilvl}
        for kind, item_id in SAMPLES.items():
            r = fetch(item_id, ilvl, args.proxy)
            if not r:
                print(f"  {ilvl} {kind} 抓不到，跳过", flush=True)
                continue
            pri, sta, sec = base[kind]
            row[f"main_{kind}"] = round(r["pri"] / pri * 1e4, 2) if pri else 0
            row[f"sta_{kind}"] = round(r["sta"] / sta * 1e4, 2) if sta else 0
            row[f"sec_{kind}"] = round(r["sec"] / sec * 1e4, 2) if sec else 0
        rows.append(row)
        print(row, flush=True)

    fields = ["ilvl"] + [f"{m}_{k}" for k in SAMPLES for m in ("main", "sta", "sec")]
    with io.open(STAT_CURVE, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"\n写入 {STAT_CURVE}，共 {len(rows)} 个装等")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
