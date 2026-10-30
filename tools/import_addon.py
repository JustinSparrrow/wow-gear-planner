"""把 WoWGear 插件导出的数据变成 spec_stats 条目。

用法：
    python tools/import_addon.py "v=1;class=3;spec=255;..."
    python tools/import_addon.py -                    # 从标准输入读
    python tools/import_addon.py "..." --write        # 校验通过后写进 spec_stats.json

为什么要先校验再写：这批数会长期影响所有用这个专精的人。如果换算曲线和游戏
对不上（说明我们的公式有问题），把反推出来的基线写进去，等于把错误固化下来。
所以默认只打印、不落地，`--write` 还要先过 cross_check。
"""

import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from wowlib import addon  # noqa: E402
from wowlib.session import dr_convert  # noqa: E402

CURVES = os.path.join(ROOT, "data", "raw", "rating_curves.json")
MASTERY = os.path.join(ROOT, "data", "raw", "spec_mastery.json")
STATS = os.path.join(ROOT, "data", "raw", "spec_stats.json")

sys.stdout.reconfigure(encoding="utf-8")


def load_json(path, default=None):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def show(tag, d, order=("crit", "haste", "mastery", "vers"), ints=False):
    if ints:
        parts = [f"{k}={d[k]:.0f}" for k in order if k in d]
    else:
        parts = [f"{k}={d[k]:+.2f}" for k in order if k in d]
    print(f"  {tag:<6} " + "  ".join(parts))


def main(argv=None):
    ap = argparse.ArgumentParser(description="导入 WoWGear 插件导出的角色数据")
    ap.add_argument("data", help="插件导出的那行文本；给 - 表示从标准输入读")
    ap.add_argument("--write", action="store_true", help="校验通过后写进 spec_stats.json")
    ap.add_argument("--force", action="store_true", help="校验没过也写（不建议）")
    ap.add_argument("--vers", type=float,
                    help="手动指定面板上的全能百分比。插件拿到的全能换算是评级换算值，"
                         "和面板显示可能不是一个口径，对不上时用它")
    ap.add_argument("--note", default="", help="给这条数据加个备注")
    args = ap.parse_args(argv)

    text = sys.stdin.read() if args.data == "-" else args.data
    data = addon.parse(text)
    if not data:
        print("没解析出内容。整行应该是 v=1;class=...;spec=... 这样用分号连起来的")
        return 2

    if args.vers is not None:
        # 换算值作废，改用玩家眼睛看到的面板值——这是权威口径
        data["versB"] = f"{args.vers}"
        data["versT"] = f"{args.vers}"
        data["_vers_manual"] = "1"

    key = addon.spec_key(data)
    if not key:
        print(f"认不出专精（classKey={data.get('classKey')} specKey={data.get('specKey')}）。"
              "如果游戏版本刚更新过专精，可能是 SPEC_KEY 表缺了新 ID")
        return 2

    curves = load_json(CURVES, {})
    mastery = (load_json(MASTERY, {}) or {}).get("levels") or {}
    coeff = (mastery.get(key) or {}).get("coeff")

    print(f"\n{addon.summarize(data)}")
    if addon.spec_key(data) and coeff is None:
        print("  ⚠ 这张表里没有该专精的精通系数，精通那一项算不了")
    show("评级", addon.ratings(data), ints=True)
    show("面板", addon.panel_pct(data))
    if args.vers is not None:
        print(f"  · 全能用了你手填的 {args.vers}%，没用插件读到的换算值")

    base, notes = addon.baselines(data, curves, coeff, dr_convert)
    show("基线", base)
    for n in notes:
        print(f"  · {n}")

    diffs = addon.cross_check(data, curves, dr_convert)
    check_data = data
    if args.vers is not None:
        # 手填的是面板值，和 GetCombatRatingBonus 不是一个口径，不能直接对比。
        # 但全能的分母一直存疑（54 还是 64），干脆两种都试给用户看：
        # 哪个算出来的基线更接近 0 / 更像整数，就说明哪个是游戏在用的。
        r = addon.ratings(data)
        print("\n  · 全能分母试算（基线越接近 0 越可信）：")
        for den in (54, 64):
            print(f"      若分母 {den}: 基线 {args.vers - r['vers'] / den:+.2f}")
        check_data = {k: v for k, v in data.items() if not k.startswith("vers")}
        diffs = addon.cross_check(check_data, curves, dr_convert)
    ok = True
    if diffs:
        ok = False
        print("\n  ✗ 换算对不上，先别写：")
        for d in diffs:
            print(f"    {d['stat']:<6} {d['rating']:>6.0f} 评级  "
                  f"我们算 {d['ours']:.2f}%  游戏给 {d['game']:.2f}%  差 {d['diff']:+.2f}")
        print("    这不是面板抄错——插件的数直接来自游戏。说明 rating_curves 的")
        print("    分母或递减档位和当前版本不一致，要先修那个，再回来写基线。")
    else:
        checks = [k for k in ("crit", "haste", "vers") if k + "B" in data]
        print(f"\n  ✓ 换算对上了（{len(checks)} 项），这份数据可以用")

    if not args.write:
        print("\n（只打印没写入。确认没问题加 --write）")
        return 0 if ok else 1

    if not ok and not args.force:
        print("\n拒绝写入。原因见上面。确实要写就加 --force，但先想清楚为什么。")
        return 1

    payload = load_json(STATS, {}) or {}
    specs = payload.setdefault("specs", {})
    entry = dict(base)
    entry["source"] = "addon"
    entry["confidence"] = "high" if ok else "low"
    if args.note:
        entry["note"] = args.note
    old = specs.get(key)
    specs[key] = entry
    payload.setdefault("build", data.get("build"))
    with io.open(STATS, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n已写入 {STATS}")
    print(f"  {key}: {'（覆盖了原来的值）' if old else '（新增）'}")
    print("  下一步：python main.py --build 重建数据库")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
