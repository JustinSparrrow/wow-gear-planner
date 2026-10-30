"""数据集版本清单：哪些数据对应哪个游戏版本，什么时候更新的。

为什么要这个
------------
这个项目里其实有三层东西，混在一起就会"越补越乱"：

1. **引擎**（代码）：怎么算属性、怎么导出 simc——和版本无关，不该随赛季改；
2. **数据集**（data/raw/*.json）：装备、名字、宝石、换算率、精通系数——
   每个版本都会过期，但**只换数据，不换代码**；
3. **角色状态**（panel.json / accounts.db）：玩家自己的面板和存档，
   任何时候都不用重建，除非他自己换了装备或天赋。

这个文件管的是第 2 层：每个数据集属于哪个 build、从哪来、什么时候拉的。
`--check-update` 拿它和 wago 上的最新 build 比，告诉你哪个数据集过期了；
`--update` 只重拉过期的那些，然后重建库。代码一行都不用动。

build 的比较用「主.次.补丁」前缀：12.1.5 和 12.1.7 算同一个大版本（12.1），
数据通常还能用，只是可能少了新装备；12.2 才算真的换代。
"""

import io
import json
import os
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(ROOT, "data", "raw")
VERSIONS = os.path.join(ROOT, "data", "versions.json")

BUILDS_API = "https://wago.tools/api/builds"
# 零售客户端的产品代号。怀旧服是 wow_classic / wow_classic_era，不混用
RETAIL_PRODUCTS = ("wowt", "wow")

# 数据集 -> 用什么脚本刷新。None 表示要人工核对。
DATASETS = {
    "items": ("装备 / 副本 / 赛季", "fetch_static.py", "Raidbots 静态数据"),
    "names": ("物品中文名", "fetch_names.py", "wago ItemSparse"),
    "gems_enchants": ("宝石 / 附魔", "fetch_gems_enchants.py", "wowhead + SimC"),
    "sockets": ("装备孔位", "fetch_sockets.py", "wowhead"),
    "class_base": ("裸装基础属性", "fetch_class_base.py", "SimC gt 表"),
    "spec_mastery": ("专精精通系数", None, "客户端法术表"),
    "rating_curves": ("绿字换算 / 递减", None, "Maxroll"),
}


def read():
    if not os.path.exists(VERSIONS):
        return {"datasets": {}}
    with io.open(VERSIONS, encoding="utf-8") as f:
        return json.load(f)


def write(data):
    with io.open(VERSIONS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


def mark(dataset, build, note=""):
    """记一个数据集已经更新到哪个 build。"""
    data = read()
    entry = data.setdefault("datasets", {}).setdefault(dataset, {})
    entry["build"] = build
    entry["updated"] = time.strftime("%Y-%m-%d")
    if note:
        entry["source"] = note
    write(data)


def latest_build(proxy="", timeout=40):
    """wago 上的最新零售 build。拿不到返回空串——离线时不该让更新流程崩掉。"""
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(BUILDS_API, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with opener.open(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:                                     # noqa: BLE001
        print(f"  ⚠ 查不到最新 build（{e}），跳过版本比对")
        return ""
    best = ""
    for product in RETAIL_PRODUCTS:
        for row in data.get(product) or []:
            v = row.get("version") or ""
            if v and _key(v) > _key(best):
                best = v
    return best


def _key(build):
    """12.1.7.70668 -> (12, 1, 7, 70668)，用来比大小。"""
    out = []
    for part in (build or "").split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out + [0] * (4 - len(out)))


def major(build):
    """12.1.7.70668 -> '12.1'。数据在这个粒度上过期才算真过期。"""
    return ".".join((build or "").split(".")[:2])


def status(proxy=""):
    """每个数据集的状态： (名字, 本地 build, 最新 build, 是否过期)。"""
    data = read()
    latest = latest_build(proxy)
    out = []
    for key, (label, script, source) in DATASETS.items():
        entry = (data.get("datasets") or {}).get(key) or {}
        local = entry.get("build") or "—"
        stale = bool(latest) and major(local) != major(latest) and local != latest
        out.append({
            "key": key, "label": label, "local": local, "latest": latest or "—",
            "stale": stale, "script": script, "source": source,
            "updated": entry.get("updated") or "—",
        })
    return [s for s in out if s["stale"]], out, latest


def print_status(proxy=""):
    stale, rows, latest = status(proxy)
    print(f"\n最新零售 build：{latest or '（没查到，检查网络/代理）'}")
    print(f"{'数据集':16}{'本地':>16}{'最新':>16}  {'更新时间':12} 状态")
    for r in rows:
        flag = "⚠ 过期" if r["stale"] else "✓"
        print(f"{r['label']:16}{r['local']:>16}{r['latest']:>16}  {r['updated']:12} {flag}")
    if not stale:
        print("\n所有数据集都是当前版本的。")
    else:
        print(f"\n{len(stale)} 个数据集过期：{', '.join(r['label'] for r in stale)}")
        print("跑 python main.py --update 自动刷新；带 ⚠ 的具体看上一列的提示。")
    return stale


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="数据集版本清单")
    ap.add_argument("--check", action="store_true", help="打印状态")
    ap.add_argument("--json", action="store_true", help="机器可读（给 --update 用）")
    ap.add_argument("--proxy", default=os.environ.get("WG_PROXY", ""))
    ap.add_argument("--mark", nargs=2, metavar=("DATASET", "BUILD"))
    args = ap.parse_args(argv)
    if args.mark:
        mark(args.mark[0], args.mark[1])
        print(f"已记录 {args.mark[0]} -> {args.mark[1]}")
        return 0
    stale, rows, latest = status(args.proxy)
    if args.json:
        print(json.dumps({"latest": latest,
                          "stale": [r["key"] for r in stale],
                          "scripts": {r["key"]: r["script"] for r in stale}},
                         ensure_ascii=False))
        return 0
    print_status(args.proxy)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
