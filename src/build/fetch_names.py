"""从客户端 ItemSparse 抽中文名（装备 / 宝石 / 附魔）。

为什么需要这一步
----------------
库里 `items.name_cn` 一列从建库第一天就留着，但一直是空的。界面上只有
"身上穿着的那几件"有中文名——那是 simc 导出注释里带的，候选列表里的装备
全是英文名。中文玩家在拍卖行、副本掉落里看到的是中文名，扫一眼卡片
"Crown of the Eternal Fang" 根本对不上号。

宝石和附魔更彻底：连注释都没有，75 颗宝石全是 "Quick Peridot"。

为什么不走 wowhead
------------------
装备走 wowhead tooltip 的 locale=4 是可行的（实测 249305 → 午夜烈焰便鞋），
但**宝石和附魔卷轴在 wowhead 上查不到**：240855、243946 这些 id 直接 404，
Midnight 的这批物品还没进那边的库。所以唯一可靠的来源是客户端表
`ItemSparse` 的 zhCN 版本（wago.tools 直接提供 CSV）。

顺带解决一个界面上的老问题
--------------------------
宝石那 75 条里有 12 对"名字、品质、图标完全相同，数值差 1 点"的条目，
一直分不清。原因在 ItemSparse 里一眼可见：它们是**同一颗宝石的两个装等版本**
（278 / 295），同一颗宝石在不同装等下给的属性本来就不一样。把 `ilvl`
一起抽出来，界面上写"278"和"295"就分得开了，不用再靠猜。

用法
----
    python src/build/fetch_names.py                 # 没有 CSV 就先下载，然后抽取
    python src/build/fetch_names.py --keep-raw      # 抽完保留 48MB 的 CSV
    python src/build/fetch_names.py --proxy http://127.0.0.1:7897

产物 `data/raw/names_zhcn.json`：

    {"items":    {"249305": "午夜烈焰便鞋", …},
     "gems":     {"240855": {"name": "迅捷翠榴石", "ilvl": 278}, …},
     "enchants": {"243946": "附魔戒指 - 阿曼尼精通", …}}   # 键是卷轴 item id

只抽"用得到的 id"：装备取 `equippable-items.json` 里有属性的那批（build_db
只会入库这些），宝石取 `gems.json` 的 id，附魔取 `enchants.json` 的卷轴 id。
全表 20 万行都存下来要十几 MB，没必要。
"""

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(ROOT, "data", "raw")

CSV_PATH = os.path.join(RAW, ".item-sparse-zhcn.csv")
OUT_PATH = os.path.join(RAW, "names_zhcn.json")

ITEMS_IN = os.path.join(RAW, "equippable-items.json")
GEMS_IN = os.path.join(RAW, "gems.json")
ENCH_IN = os.path.join(RAW, "enchants.json")
META_IN = os.path.join(RAW, "meta.json")

URL = ("https://wago.tools/db2/ItemSparse/csv"
       "?build={build}&locale=zhCN")


def _build_id():
    """客户端 build 号。写死在 URL 里的话，下个补丁就得回来改一次，
    而 meta.json 里本来就有这个值。"""
    try:
        with io.open(META_IN, encoding="utf-8") as f:
            return json.load(f).get("wowBuild") or ""
    except OSError:
        return ""


def download(proxy=""):
    """拉 zhCN 的 ItemSparse。

    这个文件是整表导出（48MB），Range 请求会被忽略，只能整份下。
    实测直连约 40 秒，代理反而更慢，所以默认直连；连不上再挂 --proxy。
    """
    build = _build_id()
    if not build:
        print("读不到 data/raw/meta.json 里的 build 号，无法拼下载地址")
        return False
    url = URL.format(build=build)
    print(f"下载 {url}")
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    t0 = time.time()
    try:
        with opener.open(urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0"}), timeout=300) as r, \
                io.open(CSV_PATH, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as exc:      # 网络问题不该留半个文件骗到下一次
        print(f"下载失败：{exc}")
        if os.path.exists(CSV_PATH):
            os.remove(CSV_PATH)
        return False
    size = os.path.getsize(CSV_PATH)
    print(f"  完成 {size / 1048576:.1f} MB，{time.time() - t0:.0f} 秒")
    return size > 1000000


def _json(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def wanted_ids():
    """要抽哪几类 id。返回 (items, gems, enchants)。"""
    items = set()
    try:
        for it in _json(ITEMS_IN):
            # build_db 只给"有属性"的装备入库，其余（纯外观、背包）不会出现在
            # 界面上，抽它们只是白占体积
            if it.get("stats"):
                items.add(int(it["id"]))
    except OSError:
        print(f"⚠ 缺少 {ITEMS_IN}，装备中文名抽不了")

    gems = set(int(k) for k in _json(GEMS_IN)) if os.path.exists(GEMS_IN) else set()

    ench = set()
    if os.path.exists(ENCH_IN):
        for v in _json(ENCH_IN).values():
            if v.get("item_id"):
                ench.add(int(v["item_id"]))
    return items, gems, ench


def extract(want_items, want_gems, want_ench):
    """扫一遍 CSV，只留需要的 id。

    一次遍历拿三个字典：20 万行 × 上百列，扫三遍不如扫一遍。
    """
    csv.field_size_limit(10 ** 9)
    items, gems, enchants = {}, {}, {}
    total = 0
    with io.open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            total += 1
            raw_id = row.get("ID")
            try:
                iid = int(raw_id)
            except (TypeError, ValueError):
                continue
            name = (row.get("Display_lang") or "").strip()
            if not name:
                continue
            if iid in want_items:
                items[str(iid)] = name
            if iid in want_gems:
                lvl = row.get("ItemLevel") or ""
                gems[str(iid)] = {"name": name,
                                  "ilvl": int(lvl) if lvl.isdigit() else None}
            if iid in want_ench:
                enchants[str(iid)] = name
    return items, gems, enchants, total


def main(argv=None):
    ap = argparse.ArgumentParser(description="从 ItemSparse 抽中文名")
    ap.add_argument("--proxy", default=os.environ.get("WG_PROXY", ""),
                    help="下载用的代理，例如 http://127.0.0.1:7897")
    ap.add_argument("--keep-raw", action="store_true",
                    help="抽完保留 48MB 的 CSV（默认删掉，它只在这一步有用）")
    args = ap.parse_args(argv)

    if not os.path.exists(CSV_PATH):
        if not download(args.proxy):
            return 1
    else:
        print(f"用已有的 {os.path.relpath(CSV_PATH, ROOT)}"
              f"（{os.path.getsize(CSV_PATH) / 1048576:.1f} MB）")

    want_items, want_gems, want_ench = wanted_ids()
    if not (want_items or want_gems or want_ench):
        print("没有可抽的 id，先跑 build_db / fetch_gems_enchants")
        return 1
    print(f"待抽：装备 {len(want_items)} / 宝石 {len(want_gems)} / 附魔卷轴 {len(want_ench)}")

    items, gems, enchants, total = extract(want_items, want_gems, want_ench)
    print(f"CSV 共 {total} 行，命中：装备 {len(items)} / 宝石 {len(gems)} / 附魔 {len(enchants)}")

    miss = [("装备", len(want_items) - len(items)),
            ("宝石", len(want_gems) - len(gems)),
            ("附魔", len(want_ench) - len(enchants))]
    for label, n in miss:
        if n:
            print(f"  ⚠ {label}有 {n} 个 id 在 ItemSparse 里没有中文名")

    out = {"items": items, "gems": gems, "enchants": enchants}
    with io.open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"写入 {os.path.relpath(OUT_PATH, ROOT)}")

    if not args.keep_raw:
        os.remove(CSV_PATH)
        print("已删除 CSV（加 --keep-raw 可保留）")
    print("\n下一步：python main.py --build   把中文名写进数据库")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
