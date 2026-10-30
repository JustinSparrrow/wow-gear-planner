"""账号、历史、配装存档的验收测试。

    python tests/test_accounts.py

用临时库跑，不碰 data/accounts.db —— 测试要是能改到真账号，早晚会有人
跑一次测试把自己攒的配装清空。

重点验三件容易出错的事：
  1. 密码只存哈希（偷看库文件也拿不到明文）
  2. 同一份 simc 反复导入只留一条（否则历史列表全是一样的）
  3. 别人的存档读不到（越权检查，写完接口最容易忘）
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.stdout.reconfigure(encoding="utf-8")

from wowgear import accounts          # noqa: E402
from wowlib.simc import parse                # noqa: E402
from wowlib.session import Session, connect, load_budget   # noqa: E402

results = []


def check(group, name, pairs):
    results.append((group, name, pairs))


def main():
    tmp = tempfile.mkdtemp(prefix="wgacc")
    accounts.ACCOUNT_DB = os.path.join(tmp, "accounts.db")
    c = accounts.connect()

    # ── 注册 / 登录 ──────────────────────────────────────────
    uid = accounts.register(c, "moqi", "1234")
    check("注册登录", "注册返回用户 id", [("id > 0", True, uid > 0)])
    try:
        accounts.register(c, "MOQI", "xxxx")
        dup = "没报错"
    except ValueError:
        dup = "报错"
    check("注册登录", "用户名不区分大小写地查重", [("重复注册", "报错", dup)])
    try:
        accounts.register(c, "abcd", "12")
        short_pw = "通过"
    except ValueError:
        short_pw = "被拒"
    check("注册登录", "密码太短被拒", [("2 位密码", "被拒", short_pw)])

    row = c.execute("SELECT pass_hash, salt FROM users WHERE id = ?", (uid,)).fetchone()
    check("注册登录", "库里不存明文密码", [
        ("pass_hash 不等于原密码", True, row["pass_hash"] != "1234"),
        ("hash 长度", 64, len(row["pass_hash"])),
        ("salt 长度", 32, len(row["salt"])),
    ])
    check("注册登录", "密码错 / 用户不存在都返回 None", [
        ("错密码", None, accounts.login(c, "moqi", "wrong")),
        ("不存在", None, accounts.login(c, "nobody", "1234")),
        ("正确", "moqi", (accounts.login(c, "moqi", "1234") or (0, None))[1]),
    ])

    # ── 令牌 ────────────────────────────────────────────────
    token = accounts.new_session(c, uid)
    check("登录令牌", "令牌能换回用户", [
        ("user_of", (uid, "moqi"), accounts.user_of(c, token)),
        ("空令牌", None, accounts.user_of(c, "")),
        ("乱令牌", None, accounts.user_of(c, "not-a-token")),
    ])
    # 手工把令牌改成过期，验证过期就不认
    c.execute("UPDATE sessions SET expires_at = 1 WHERE token = ?", (token,))
    c.commit()
    check("登录令牌", "过期令牌失效", [("user_of", None, accounts.user_of(c, token))])

    token = accounts.new_session(c, uid)
    accounts.drop_session(c, token)
    check("登录令牌", "登出后令牌作废", [("user_of", None, accounts.user_of(c, token))])

    # ── 历史 ────────────────────────────────────────────────
    # 历史用哪份样例：本机真实导出优先，没有就用入库的脱敏版（内容一样）
    sample = next((p for p in (os.path.join(HERE, "my_profile.txt"),
                               os.path.join(HERE, "demo_profile.txt"))
                   if os.path.exists(p)), None)
    if not sample:
        print("找不到样例导出（tests/demo_profile.txt 应该跟着仓库走）")
        return 2
    text = open(sample, encoding="utf-8").read()
    prof = parse(text)
    s = Session(connect(), prof, load_budget(), raw_text=text)
    avg = s.avg_ilevel()[0]
    h1 = accounts.record_history(c, uid, text, prof, avg)
    h2 = accounts.record_history(c, uid, text, prof, avg)
    hist = accounts.list_history(c, uid)
    # 角色名来自样例文件第一行的注释，跟着样例走（脱敏版叫 Demo）
    want_name = "Demo" if os.path.basename(sample) == "demo_profile.txt" else "尛柒誒"
    check("simc 历史", "同一份只留一条", [
        ("两次返回同 id", True, h1 == h2),
        ("条目数", 1, len(hist)),
        ("装等一致", avg, hist[0]["avg_ilevel"]),
        ("名字进标签", True, want_name in hist[0]["label"]),
        # 抽屉按角色分组：只靠 label 里的字符串切不可靠，得单独存一列
        ("角色名单独存", want_name, hist[0]["char_name"]),
        ("专精中文名", "野兽控制", hist[0]["spec_cn"]),
    ])
    check("simc 历史", "换一份内容则新增一条", [
        ("条目数", 2, len(accounts.list_history(c, uid))
         if accounts.record_history(c, uid, text + "\n# x", prof, avg) else -1),
    ])
    check("simc 历史", "原文能取回", [
        ("长度一致", len(text), len(accounts.get_history_text(c, uid, h1) or "")),
    ])

    # ── 配装 ────────────────────────────────────────────────
    changes = [{"slot": "head", "id": 268230, "ilevel": 334, "crafted": []}]
    lid, replaced = accounts.save_loadout(c, uid, "团本", text, changes,
                                          "猎人", 333.5, "首杀用",
                                          "尛柒誒", "野兽控制")
    check("配装存档", "新建", [("replaced", False, replaced), ("id > 0", True, lid > 0)])
    lid2, replaced2 = accounts.save_loadout(c, uid, "团本", text, changes,
                                            "猎人", 334.1, "",
                                            "尛柒誒", "野兽控制")
    lst = accounts.list_loadouts(c, uid)
    check("配装存档", "同名覆盖而不是新增", [
        ("replaced", True, replaced2),
        ("同 id", lid, lid2),
        ("条目数", 1, len(lst)),
        ("装等已更新", 334.1, lst[0]["avg_ilevel"]),
        ("改动件数", 1, lst[0]["change_count"]),
        # 配装也要能按角色分组，不能只靠名字里带
        ("角色名", "尛柒誒", lst[0]["char_name"]),
        ("专精", "野兽控制", lst[0]["spec_cn"]),
    ])
    got = accounts.get_loadout(c, uid, lid)
    check("配装存档", "改动能读回", [
        ("条数", 1, len(got["changes"])),
        ("部位", "head", got["changes"][0]["slot"]),
        ("原文在", True, bool(got["base_simc"])),
    ])
    try:
        accounts.save_loadout(c, uid, "   ", text, changes, "猎人", 1)
        empty_name = "通过"
    except ValueError:
        empty_name = "被拒"
    check("配装存档", "空名字被拒", [("名字", "被拒", empty_name)])

    # ── 越权 ────────────────────────────────────────────────
    uid2 = accounts.register(c, "other", "abcd")
    check("越权隔离", "别人的读不到", [
        ("get_loadout", None, accounts.get_loadout(c, uid2, lid)),
        ("get_history", None, accounts.get_history_text(c, uid2, h1)),
        ("list_loadouts", 0, len(accounts.list_loadouts(c, uid2))),
        ("list_history", 0, len(accounts.list_history(c, uid2))),
    ])
    accounts.delete_loadout(c, uid2, lid)
    check("越权隔离", "别人的删不掉", [
        ("条目还在", 1, len(accounts.list_loadouts(c, uid))),
    ])

    # ── 输出 ────────────────────────────────────────────────
    total = failed = 0
    last = None
    for group, name, pairs in results:
        if group != last:
            print(f"\n=== {group} ===")
            last = group
        print(f"\n[{name}]")
        for field, expect, actual in pairs:
            total += 1
            ok = expect == actual
            if not ok:
                failed += 1
                print(f"  ✗ {field:22} 期望 {expect!r:26} 实际 {actual!r}")
            else:
                print(f"  ✓ {field:22} {actual!r}")

    print("\n" + "-" * 66)
    if failed:
        print(f"{total - failed}/{total} 通过，{failed} 项不符")
        return 1
    print(f"{total}/{total} 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
