"""账号、登录会话、simc 历史、配装存档。

单独一个库（data/accounts.db），不和 wowgear.db 混在一起：
战绩数据是"游戏数据"，会随赛季重建；账号是"人的数据"，重建时必须留着。
两者放在同一个文件里，早晚会有人跑一次 build_db 把用户存档清光。

只依赖标准库：hashlib.pbkdf2_hmac 存密码、secrets 发令牌、sqlite3 存数据。
不做第三方登录、不做找回密码——本地工具，导出 simc 就能重建，密码丢了
直接删账号重注册比做一套找回流程划算。
"""

import hashlib
import json
import os
import re
import secrets
import sqlite3
import time

from .paths import ACCOUNT_DB, DATA
from .const import SPEC_CN

# PBKDF2 迭代次数。10 万次在本地机器上约 60ms，登录感觉不到；
# 但把库文件拷走暴力破解的成本高四个数量级。
PBKDF2_ROUNDS = 120_000
SESSION_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pass_hash  TEXT NOT NULL,
    salt       TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

-- 每次导入的 simc 原文。sha 用来去重：同一份配置反复导入只更新时间，
-- 否则列表里全是重复条目。
CREATE TABLE IF NOT EXISTS simc_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    sha        TEXT NOT NULL,
    label      TEXT NOT NULL,
    char_name  TEXT,
    class_key  TEXT,
    class_cn   TEXT,
    spec       TEXT,
    level      INTEGER,
    avg_ilevel REAL,
    simc_text  TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(user_id, sha)
);

-- 配装存档：base_simc 是导入的原文，changes 是"换了哪几件"。
-- 不存最终结果而存"原文 + 改动"，是因为改动的语义更稳定：
-- 以后属性公式再调整，重新算一遍就行；存死数值就永远是旧的。
CREATE TABLE IF NOT EXISTS loadouts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    note       TEXT DEFAULT '',
    base_simc  TEXT NOT NULL,
    changes    TEXT NOT NULL,
    class_cn   TEXT,
    char_name  TEXT,
    spec_cn    TEXT,
    avg_ilevel REAL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hist_user ON simc_history(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_load_user ON loadouts(user_id, updated_at DESC);
"""


def connect():
    os.makedirs(DATA, exist_ok=True)
    conn = sqlite3.connect(ACCOUNT_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # 老库升级：char_name 是后加的列，已有账号库不会重建，只能补列。
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(simc_history)")}
    if "char_name" not in cols:
        conn.execute("ALTER TABLE simc_history ADD COLUMN char_name TEXT")
    # 配装存档也一样：按角色分组需要单独一列
    lcols = {r["name"] for r in conn.execute("PRAGMA table_info(loadouts)")}
    for col in ("char_name", "spec_cn"):
        if col not in lcols:
            conn.execute(f"ALTER TABLE loadouts ADD COLUMN {col} TEXT")
    conn.commit()
    _backfill_char_names(conn)
    _backfill_loadout_names(conn)
    return conn


def _backfill_loadout_names(conn):
    """老配装没有 char_name：从存档的 simc 原文里抠 `职业="角色名"`。

    比历史那边更可靠——原文第一行就是 `hunter="名字"`，不用猜。
    """
    rows = conn.execute(
        "SELECT id, base_simc FROM loadouts WHERE char_name IS NULL OR char_name = ''"
    ).fetchall()
    for r in rows:
        m = re.search(r'^[a-z_]+="([^"]+)"', (r["base_simc"] or ""), re.M)
        if m:
            conn.execute("UPDATE loadouts SET char_name = ? WHERE id = ?",
                         (m.group(1), r["id"]))
    conn.commit()


def _backfill_char_names(conn):
    """老记录没有 char_name：从 label（「职业 · 专精 · 名字 · 装等」）里补回来。

    只在升级时跑一次；补完之后抽屉里就不会再出现"未命名角色"。
    """
    rows = conn.execute(
        "SELECT id, label FROM simc_history WHERE char_name IS NULL OR char_name = ''"
    ).fetchall()
    for r in rows:
        parts = [p.strip() for p in (r["label"] or "").split(" · ")]
        # 有专精时 4 段：职业 · 专精 · 名字 · 装等；没专精时 3 段
        name = ""
        if len(parts) >= 4:
            name = parts[2]
        elif len(parts) == 3:
            name = parts[1]
        if name:
            conn.execute("UPDATE simc_history SET char_name = ? WHERE id = ?",
                         (name, r["id"]))
    conn.commit()


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               bytes.fromhex(salt), PBKDF2_ROUNDS).hex()


def register(conn, username, password):
    """新建账号。用户名重复 / 太短都会抛 ValueError，由上层转成 400。"""
    username = (username or "").strip()
    if not 2 <= len(username) <= 20:
        raise ValueError("用户名要 2–20 个字符")
    if len(password or "") < 4:
        raise ValueError("密码至少 4 位")
    if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        raise ValueError("这个用户名已经注册了")
    salt = secrets.token_hex(16)
    cur = conn.execute(
        "INSERT INTO users (username, pass_hash, salt, created_at) VALUES (?,?,?,?)",
        (username, _hash(password, salt), salt, int(time.time())))
    conn.commit()
    return cur.lastrowid


def login(conn, username, password):
    """校验密码，成功返回 (user_id, username)，失败返回 None。

    用户不存在和密码错误返回同一个结果——不然接口就成了用户名探测器。
    """
    row = conn.execute("SELECT id, username, pass_hash, salt FROM users WHERE username = ?",
                       ((username or "").strip(),)).fetchone()
    if not row:
        return None
    if not secrets.compare_digest(row["pass_hash"], _hash(password or "", row["salt"])):
        return None
    return row["id"], row["username"]


def new_session(conn, user_id):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) "
                 "VALUES (?,?,?,?)",
                 (token, user_id, now, now + SESSION_DAYS * 86400))
    conn.commit()
    return token


def user_of(conn, token):
    """令牌 -> (id, username)。过期的不认，顺手删掉。"""
    if not token:
        return None
    row = conn.execute("SELECT user_id, expires_at FROM sessions WHERE token = ?",
                       (token,)).fetchone()
    if not row:
        return None
    if row["expires_at"] < time.time():
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        return None
    u = conn.execute("SELECT id, username FROM users WHERE id = ?",
                     (row["user_id"],)).fetchone()
    return (u["id"], u["username"]) if u else None


def drop_session(conn, token):
    if token:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()


# ------------------------------------------------------------------ 历史

def record_history(conn, user_id, simc_text, prof, avg_ilevel):
    """导入时自动记一条。同一份 simc 只更新时间，不重复插。

    label 是列表里显示的一行摘要：职业 · 专精 · 名字 · 装等。
    **专精必须带上**：同一个猎人换专精（兽王/射击/生存），装备和数字都不一样，
    只写"猎人"的话列表里几条记录长得一模一样，找不回来。
    """
    sha = hashlib.sha256(simc_text.encode("utf-8")).hexdigest()[:32]
    spec_cn = SPEC_CN.get((prof.spec or "").lower(), prof.spec or "")
    label = " · ".join(p for p in (prof.class_cn, spec_cn, prof.char_name,
                                   f"{avg_ilevel:.1f} 装等" if avg_ilevel else "") if p)
    now = int(time.time())
    row = conn.execute("SELECT id FROM simc_history WHERE user_id = ? AND sha = ?",
                       (user_id, sha)).fetchone()
    if row:
        conn.execute("UPDATE simc_history SET updated_at = ?, label = ?, avg_ilevel = ?, "
                     "level = ?, spec = ?, char_name = ? WHERE id = ?",
                     (now, label, avg_ilevel, prof.level, prof.spec,
                      prof.char_name, row["id"]))
        conn.commit()
        return row["id"]
    cur = conn.execute(
        "INSERT INTO simc_history (user_id, sha, label, char_name, class_key, class_cn,"
        " spec, level, avg_ilevel, simc_text, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, sha, label, prof.char_name, prof.class_key, prof.class_cn, prof.spec,
         prof.level, avg_ilevel, simc_text, now, now))
    conn.commit()
    # 只留最近 40 条，再老的自动清掉——历史是给自己找回上周那套，不是归档
    conn.execute("DELETE FROM simc_history WHERE user_id = ? AND id NOT IN "
                 "(SELECT id FROM simc_history WHERE user_id = ? "
                 " ORDER BY updated_at DESC LIMIT 40)", (user_id, user_id))
    conn.commit()
    return cur.lastrowid


def list_history(conn, user_id):
    rows = conn.execute(
        "SELECT id, label, char_name, class_key, class_cn, spec, level, avg_ilevel, created_at, updated_at"
        " FROM simc_history WHERE user_id = ? ORDER BY updated_at DESC", (user_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # 老记录可能没有 spec，直接给空串，前端不用判断 null
        d["spec_cn"] = SPEC_CN.get((d.get("spec") or "").lower(), d.get("spec") or "")
        out.append(d)
    return out


def get_history_text(conn, user_id, row_id):
    row = conn.execute("SELECT simc_text FROM simc_history WHERE id = ? AND user_id = ?",
                       (row_id, user_id)).fetchone()
    return row["simc_text"] if row else None


def delete_history(conn, user_id, row_id):
    conn.execute("DELETE FROM simc_history WHERE id = ? AND user_id = ?", (row_id, user_id))
    conn.commit()


# ------------------------------------------------------------------ 配装

def save_loadout(conn, user_id, name, base_simc, changes, class_cn, avg_ilevel,
                 note="", char_name="", spec_cn=""):
    """同名就覆盖。用户点两次保存，不该得到两套一模一样的配装。

    存 `char_name` 是为了和 simc 历史一样按角色分组：一个人多个角色、
    多个专精，平铺一列等存档多了就找不着。
    """
    name = (name or "").strip()
    if not 1 <= len(name) <= 30:
        raise ValueError("配装名要 1–30 个字符")
    now = int(time.time())
    payload = json.dumps(changes, ensure_ascii=False)
    row = conn.execute("SELECT id FROM loadouts WHERE user_id = ? AND name = ?",
                       (user_id, name)).fetchone()
    if row:
        conn.execute("UPDATE loadouts SET base_simc = ?, changes = ?, note = ?, "
                     "class_cn = ?, avg_ilevel = ?, char_name = ?, spec_cn = ?,"
                     " updated_at = ? WHERE id = ?",
                     (base_simc, payload, note, class_cn, avg_ilevel, char_name,
                      spec_cn, now, row["id"]))
        conn.commit()
        return row["id"], True
    cur = conn.execute(
        "INSERT INTO loadouts (user_id, name, note, base_simc, changes, class_cn,"
        " char_name, spec_cn, avg_ilevel, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, name, note, base_simc, payload, class_cn, char_name, spec_cn,
         avg_ilevel, now, now))
    conn.commit()
    return cur.lastrowid, False


def list_loadouts(conn, user_id):
    rows = conn.execute(
        "SELECT id, name, note, class_cn, char_name, spec_cn, avg_ilevel, changes,"
        " created_at, updated_at"
        " FROM loadouts WHERE user_id = ? ORDER BY updated_at DESC", (user_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["change_count"] = len(json.loads(d.pop("changes") or "[]"))
        except (json.JSONDecodeError, TypeError):
            d["change_count"] = 0
        out.append(d)
    return out


def get_loadout(conn, user_id, row_id):
    row = conn.execute("SELECT * FROM loadouts WHERE id = ? AND user_id = ?",
                       (row_id, user_id)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["changes"] = json.loads(d.get("changes") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["changes"] = []
    return d


def delete_loadout(conn, user_id, row_id):
    conn.execute("DELETE FROM loadouts WHERE id = ? AND user_id = ?", (row_id, user_id))
    conn.commit()
