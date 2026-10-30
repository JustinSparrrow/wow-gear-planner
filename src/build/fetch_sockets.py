"""抓装备的插槽信息（能插几颗宝石）。

为什么要单独抓
--------------
Blizzard 的 equippable-items.json 里 `hasSockets` / `socketInfo` 两个字段
只对一部分物品有值——12.x 新加的戒指项链大多没有这两个字段。照它判断，
"所有新戒指都没孔"，实际游戏里显然不是这样。

真正权威的是游戏里的物品说明，最后一行会写：

    Prismatic Socket            ← 一个孔
    Prismatic Socket ×2         ← 两个孔（说明里分行写两次）

所以从 wowhead 的 tooltip 抓这一行，数次数就是孔数。

只抓哪些
--------
当前赛季所有戒指和项链。别的部位这个版本不能插宝石（老版本有，比如
TBC 的 Spellfire 系列，那些不进口袋清单，也不该给孔）。

输出
----
data/raw/sockets.json   {"<item_id>": {"count": 1, "type": "PRISMATIC"}}

build_db 读到这个文件就以它为准，覆盖 API 给的 has_sockets。

用法
----
    python src/build/fetch_sockets.py --proxy http://127.0.0.1:7897
    python src/build/fetch_sockets.py --proxy http://127.0.0.1:7897 --limit 5
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(ROOT, "data", "wowgear.db")
OUT = os.path.join(ROOT, "data", "raw", "sockets.json")

API = "https://nether.wowhead.com/tooltip/item/{id}?dataEnv=1&locale=0"

# 说明里的孔名。现在只有棱彩孔一种，但老装备有红/黄/蓝，留着方便以后扩展。
SOCKET_WORDS = {
    "prismatic socket": "PRISMATIC",
    "red socket": "RED",
    "yellow socket": "YELLOW",
    "blue socket": "BLUE",
    "meta socket": "META",
}
SOCKET_RE = re.compile(
    r"\b(" + "|".join(SOCKET_WORDS) + r")\b(?:\s*[x×]\s*(\d+))?", re.I)

OPENER = None


def opener(proxy=""):
    """wowhead 对国内 IP 直接 403，必须走代理。"""
    proxy = proxy or os.environ.get("WG_PROXY") or ""
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener()


def plain(html):
    t = re.sub(r"<[^>]+>", " ", html or "")
    t = t.replace("&amp;", "&").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", t).strip()


def fetch(item_id, tries=3):
    req = urllib.request.Request(API.format(id=item_id),
                                 headers={"User-Agent": "Mozilla/5.0"})
    for i in range(tries):
        try:
            with OPENER.open(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(0.8 * (i + 1))
    return None


def parse_sockets(text):
    """数说明里的孔。返回 (孔数, 类型)。

    注意不能只看有没有 "Socketable"——tooltip 里插了宝石的装备会多一行
    "Socketed: xxx"，那不是孔，是已经插进去的东西。
    """
    types = []
    for m in SOCKET_RE.finditer(text):
        kind = SOCKET_WORDS[m.group(1).lower()]
        n = int(m.group(2) or 1)
        types.extend([kind] * n)
    return len(types), (types[0] if types else "")


def load_targets():
    """当前赛季能插宝石的部位。"""
    db = sqlite3.connect(DB_PATH)
    rows = db.execute("""
        SELECT id, name FROM items
         WHERE is_current = 1 AND slot IN ('finger', 'neck')
         ORDER BY id""").fetchall()
    db.close()
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="抓装备插槽数量")
    ap.add_argument("--proxy", default="")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    global OPENER
    OPENER = opener(args.proxy)
    if not (args.proxy or os.environ.get("WG_PROXY")):
        print("提示：没指定代理，国内直连 wowhead 会 403\n")

    targets = load_targets()
    if args.limit:
        targets = targets[:args.limit]
    print(f"戒指项链 {len(targets)} 件待查")

    out, errors = {}, []

    def one(pair):
        iid, name = pair
        d = fetch(iid)
        if not d:
            return iid, name, None
        count, kind = parse_sockets(plain(d.get("tooltip", "")))
        return iid, name, {"count": count, "type": kind, "name": d.get("name") or name}

    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, (iid, name, val) in enumerate(ex.map(one, targets), 1):
            if val is None:
                errors.append(iid)
            else:
                out[str(iid)] = val
            if i % 20 == 0:
                print(f"  {i}/{len(targets)}")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)

    withsock = [v for v in out.values() if v["count"]]
    print(f"\n写入 {len(out)} 条 -> {OUT}")
    print(f"有孔 {len(withsock)} 件，无孔 {len(out) - len(withsock)} 件")
    if errors:
        print(f"抓失败 {len(errors)} 件：{errors[:10]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
