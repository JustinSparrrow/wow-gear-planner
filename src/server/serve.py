"""配装规划器网页服务。

    python main.py --web          或者   python src/serve.py

浏览器打开 http://127.0.0.1:8765

状态存在服务端（前端只发操作指令），这样"预览换装"用的是和最终结算完全相同的
计算函数——如果前端自己算一遍，两边迟早会漂移，界面显示 +186 而实际只有 +120，
这种错不会报异常，只会让人做出错误决定。

账号：登录后当前会话挂到用户名下，导入的 simc 自动进历史，配装可以命名存档。
没登录也能用（挂在 anon 下），只是不能存东西——先让人试，再劝登录。
"""

import http.server
import json
import os
import secrets
import socketserver
import sys
import threading
from urllib.parse import urlparse, parse_qs

# 这个文件在 src/<包>/ 下，把 src 本身放进 path 才能 `import wowgear`。
# 不用相对 import：那样 `python src/wowlib/calibrate.py` 直接跑会炸。
_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from wowlib.simc import parse                                   # noqa: E402
from wowgear.const import SLOT_LABELS, SPEC_CN                # noqa: E402
from wowlib.session import Session, connect, load_budget        # noqa: E402
from wowgear import accounts                             # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

from wowgear.paths import DB_PATH, ICONS as ICON_DIR, ROOT, TESTS, WEB as WEB_DIR
SAMPLE_PROFILE = os.path.join(TESTS, "my_profile.txt")       # 本机真实导出，不入库
SAMPLE_DEMO = os.path.join(TESTS, "demo_profile.txt")         # 同一份的脱敏版，入库
SAMPLE_FALLBACK = os.path.join(TESTS, "sample_profile.txt")   # 构造样例（含故意缺的装备）
PORT = 8765
COOKIE = "wg_sid"            # 登录令牌
ANON = "wg_aid"              # 未登录浏览器的身份，用来隔离各自的会话

SLOTS = [(slot, label) for slot, (label, _) in
         sorted(((k, v) for k, v in SLOT_LABELS.items() if v[1] < 90),
                key=lambda kv: kv[1][1])]

VERSION_FILE = os.path.join(ROOT, "VERSION")


def read_version():
    """内测包版本号。文件不存在就当开发环境，前端不显示。"""
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""

# 每个浏览器（严格说是每个登录令牌）一份会话。anon 是没登录时的默认位。
# 以前只有一个全局 STATE["session"]，一旦支持账号就必须按人分开，
# 否则 A 登录着，B 打开页面就把 A 的装备顶掉了。
STATE = {}                      # key: u<uid> 或 a<浏览器id> -> {"conn","s","user"}
LOCK = threading.Lock()

ACCOUNTS = accounts.connect()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    # ---------------------------------------------------------------- 基础

    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        data = body if isinstance(body, bytes) else json.dumps(
            body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for line in getattr(self, "_cookie_out", ()):
            self.send_header("Set-Cookie", line)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _cookie(self, name):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def _anon_id(self):
        """给没登录的浏览器发一个长期 id，用来把各自的会话分开。

        以前所有匿名用户共用一个 STATE["anon"]：A 在自己机器上粘了一身装备，
        B 打开页面看到的就是 A 的装备；更糟的是 B 一登录，这套装备会被
        "过继"进 B 的账号——账号里凭空多出一身不认识的装备。
        """
        if getattr(self, "_aid", None):
            return self._aid
        aid = self._cookie(ANON)
        if not aid:
            aid = secrets.token_hex(8)
            self._cookie_out.append(
                f"{ANON}={aid}; Path=/; Max-Age=31536000; SameSite=Lax")
        self._aid = aid
        return aid

    def _ctx(self):
        """(会话键, 用户, 会话条目)。用户是 (id, 用户名) 或 None。"""
        token = self._cookie(COOKIE)
        user = accounts.user_of(ACCOUNTS, token) if token else None
        if user:
            key = f"u{user[0]}"
        else:
            key = f"a{self._anon_id()}"
        return key, user, STATE.get(key)

    def _session(self):
        """当前这套装备。没有就回 409，前端据此弹导入框。"""
        _, _, entry = self._ctx()
        if not entry:
            self._send(409, {"error": "还没载入角色，先粘贴 simc 字符串"})
            return None
        return entry["s"]

    # ---------------------------------------------------------------- GET

    def do_GET(self):
        self._cookie_out = []
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}

        if u.path in ("/", "/index.html"):
            with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")

        if u.path == "/api/bootstrap":
            conn = connect()
            meta = {r["key"]: r["value"] for r in conn.execute("SELECT * FROM meta")}
            total = conn.execute(
                "SELECT COUNT(*) FROM items WHERE is_current = 1").fetchone()[0]
            conn.close()
            key, user, entry = self._ctx()
            return self._send(200, {
                "slots": [{"key": k, "name": n} for k, n in SLOTS],
                "total_items": total,
                "season": meta.get("season"),
                "build": meta.get("wow_build"),
                # 内测包的版本号，落地页右下角显示。文件不存在就留空
                "version": read_version(),
                "has_session": entry is not None,
                "user": {"id": user[0], "username": user[1]} if user else None,
            })

        if u.path == "/api/me":
            _, user, _ = self._ctx()
            if not user:
                return self._send(200, {"user": None, "history": [], "loadouts": []})
            return self._send(200, {
                "user": {"id": user[0], "username": user[1]},
                "history": accounts.list_history(ACCOUNTS, user[0]),
                "loadouts": accounts.list_loadouts(ACCOUNTS, user[0]),
            })

        if u.path == "/api/snapshot":
            s = self._session()
            if not s:
                return
            with LOCK:
                return self._send(200, s.snapshot())

        if u.path == "/api/sample":
            # my_profile.txt 是本机真实导出（含角色名/服务器），不进版本库。
            # 别人 clone 下来先用 demo_profile.txt——同一份数据，只是名字换成了 Demo；
            # 再没有才退到构造样例（那份故意塞了几件库里不存在的装备，用来测容错）。
            for path in (SAMPLE_PROFILE, SAMPLE_DEMO, SAMPLE_FALLBACK):
                if os.path.exists(path):
                    with open(path, encoding="utf-8") as f:
                        return self._send(200, {"simc": f.read(),
                                                "real": path != SAMPLE_FALLBACK})
            return self._send(404, {"error": "样例文件不存在"})

        if u.path == "/api/export-simc":
            s = self._session()
            if not s:
                return
            with LOCK:
                text = s.to_simc()
                name = s.prof.char_name or ""
                changed = len([k for k in s.prof.items if s._changed(k)])
                # 打孔器开的孔在 simc 格式里表示不出来（SimC 的 item 选项里
                # 没有 sockets=，孔的来源是它自己的物品数据），所以要告诉玩家
                drilled = [k for k, it in s.prof.items.items() if it.drilled]
            return self._send(200, {
                "simc": text,
                "name": name,
                "changed": changed,
                "drilled": drilled,
                # 改过东西就没带 checksum（那是"插件原样输出"的凭证），
                # 前端据此说明"为什么这段进不了 Top Gear 的 addon 输入框"
                "checksum": "# Checksum" in text,
                "meta": (f"{name} · 平均装等 {s.avg_ilevel()[0]} · "
                         f"改过 {changed} 件"),
            })

        if u.path == "/api/gem-options":
            s = self._session()
            if not s:
                return
            with LOCK:
                return self._send(200, {"gems": s.gem_options()})
        if u.path == "/api/enchant-options":
            slot_key = (parse_qs(u.query).get("slot") or [""])[0]
            if not slot_key:
                return self._send(400, {"error": "缺 slot"})
            s = self._session()
            if not s:
                return
            with LOCK:
                item = s.prof.items.get(slot_key)
                if item is None:
                    return self._send(400, {"error": "这个部位还没有装备"})
                return self._send(200, {
                    "slot": slot_key,
                    "db_slot": item.db_slot,
                    "enchants": s.enchant_options(item.db_slot),
                    # 本赛季哪些部位有附魔、各几条。某部位一条都没有时，
                    # 前端要靠它区分"游戏里本来就没有"和"这条没收录"。
                    "slots": s.enchant_slots(),
                })
        if u.path == "/api/candidates":
            slot_key = q.get("slot")
            s = self._session()
            if not s:
                return
            if not slot_key:
                return self._send(400, {"error": "缺 slot 参数"})
            ilevel = q.get("ilevel")
            try:
                ilevel = int(ilevel) if ilevel else None
            except ValueError:
                ilevel = None
            with LOCK:
                items = s.candidates(slot_key, ilevel)
            return self._send(200, {"slot": slot_key, "ilevel": ilevel,
                                    "items": items})

        if u.path.startswith("/icon/"):
            # /icon/<名字>        装备
            # /icon/gems/<名字>   宝石
            # /icon/enchants/<名> 附魔
            # 只认这两个子目录，别的一律当装备图标处理，避免路径穿越取到别的文件。
            rel = u.path[len("/icon/"):]
            folder = ICON_DIR
            bits = rel.split("/")
            if len(bits) == 2:
                if bits[0] not in ("gems", "enchants"):
                    return self._send(404, b"", "image/png")
                folder, rel = os.path.join(ICON_DIR, bits[0]), bits[1]
            elif len(bits) > 2:
                return self._send(404, b"", "image/png")
            for ext in (".png", ".jpg"):
                p = os.path.join(folder, rel + ext)
                if os.path.isfile(p):
                    with open(p, "rb") as f:
                        ctype = "image/png" if ext == ".png" else "image/jpeg"
                        return self._send(200, f.read(), ctype)
            return self._send(404, b"", "image/png")

        # web 目录下的其它静态文件（方便拆 js/css 出去）。
        # 只认目录内的文件，路径里带 .. 一律拒绝。
        if not u.path.startswith("/api/"):
            name = u.path.lstrip("/")
            p = os.path.abspath(os.path.join(WEB_DIR, name))
            if p.startswith(WEB_DIR + os.sep) and os.path.isfile(p):
                ctype = {".html": "text/html; charset=utf-8",
                         ".js": "application/javascript; charset=utf-8",
                         ".css": "text/css; charset=utf-8",
                         ".png": "image/png", ".jpg": "image/jpeg",
                         ".svg": "image/svg+xml"}.get(
                             os.path.splitext(p)[1].lower(), "application/octet-stream")
                with open(p, "rb") as f:
                    return self._send(200, f.read(), ctype)

        return self._send(404, {"error": "not found"})

    # ---------------------------------------------------------------- POST

    def do_POST(self):
        self._cookie_out = []
        u = urlparse(self.path)
        body = self._body()

        # 账号相关不需要已有角色
        if u.path == "/api/register":
            return self._auth_register(body)
        if u.path == "/api/login":
            return self._auth_login(body)
        if u.path == "/api/logout":
            return self._auth_logout()

        if u.path == "/api/profile":
            return self._load_profile(body)

        # 历史 / 配装：载入存档时还没有"当前会话"，所以必须排在 _session() 之前
        if u.path.startswith("/api/history"):
            return self._history(u.path, body)
        if u.path.startswith("/api/loadout"):
            return self._loadout(u.path, body)

        s = self._session()
        if not s:
            return

        if u.path == "/api/preview":
            return self._preview(s, body)
        if u.path == "/api/preview-gem":
            # 插上这颗宝石属性会变成多少。宝石模式以前只能点了才知道，
            # 而"为了 +16 精通要不要拔掉 +7 暴击"正是要在这儿判断的事。
            slot_key = body.get("slot")
            if not slot_key:
                return self._send(400, {"error": "缺 slot"})
            try:
                with LOCK:
                    totals = s.preview_gem(slot_key, body.get("index", 0),
                                           body.get("gem_id") or 0)
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            if totals is None:
                return self._send(400, {"error": "这个孔上没有宝石可拔"})
            return self._send(200, {
                "totals": {k: round(v, 1) for k, v in totals.items()},
                "delta": {k: round(totals.get(k, 0) - s.totals.get(k, 0), 1)
                          for k in set(totals) | set(s.totals)},
            })
        if u.path == "/api/preview-enchant":
            slot_key = body.get("slot")
            if not slot_key:
                return self._send(400, {"error": "缺 slot"})
            try:
                with LOCK:
                    totals = s.preview_enchant(slot_key, body.get("enchant_id") or 0)
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            return self._send(200, {
                "totals": {k: round(v, 1) for k, v in totals.items()},
                "delta": {k: round(totals.get(k, 0) - s.totals.get(k, 0), 1)
                          for k in set(totals) | set(s.totals)},
            })
        if u.path == "/api/equip":
            return self._equip(s, body)
        if u.path == "/api/reset":
            slot_key = body.get("slot")
            if not slot_key:
                return self._send(400, {"error": "缺 slot"})
            with LOCK:
                s.reset(slot_key)
                return self._send(200, s.snapshot())
        if u.path == "/api/reset-all":
            with LOCK:
                s.reset_all()
                return self._send(200, s.snapshot())
        if u.path == "/api/drill":
            # 用打孔器开/关一个孔。这是玩家状态而不是物品数据（同一件装备开没开过
            # 孔，游戏里是两个状态），只能由玩家在界面上说一声。
            slot_key = body.get("slot")
            if not slot_key:
                return self._send(400, {"error": "缺 slot"})
            try:
                with LOCK:
                    s.set_drilled(slot_key, bool(body.get("on", True)))
                    return self._send(200, s.snapshot())
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
        if u.path == "/api/gem":
            slot_key = body.get("slot")
            if not slot_key:
                return self._send(400, {"error": "缺 slot"})
            try:
                with LOCK:
                    s.set_gem(slot_key, body.get("index", 0), body.get("gem_id") or 0)
                    return self._send(200, s.snapshot())
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        if u.path == "/api/enchant":
            slot_key = body.get("slot")
            if not slot_key:
                return self._send(400, {"error": "缺 slot"})
            try:
                with LOCK:
                    s.set_enchant(slot_key, body.get("enchant_id") or 0)
                    return self._send(200, s.snapshot())
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        if u.path == "/api/undo":
            with LOCK:
                slot = s.undo()
                if slot is None:
                    return self._send(400, {"error": "没有可撤销的操作"})
                return self._send(200, s.snapshot())
        if u.path == "/api/choice":
            return self._save_choice(s, body)
        if u.path == "/api/panel":
            # 面板值由玩家自己填：游戏里鼠标悬停看到的评级。
            # 判断不了就直接用，填错了显示也会跟着错，这是用户自己的镜子。
            panel = body.get("panel") or {}
            with LOCK:
                s.set_panel(panel)
                return self._send(200, s.snapshot())

        return self._send(404, {"error": "not found"})

    # ---------------------------------------------------------------- 账号

    def _auth_register(self, body):
        try:
            uid = accounts.register(ACCOUNTS, body.get("username"),
                                    body.get("password"))
        except ValueError as exc:
            return self._send(400, {"error": str(exc)})
        return self._issue(uid, body.get("username"))

    def _auth_login(self, body):
        hit = accounts.login(ACCOUNTS, body.get("username"), body.get("password"))
        if not hit:
            return self._send(400, {"error": "用户名或密码不对"})
        return self._issue(hit[0], hit[1])

    def _issue(self, uid, username):
        """发令牌。

        以前这里会把"未登录时那套装备"过继给账号，理由是"先粘 simc 再想起登录，
        登录后装备没了很恼火"。但匿名会话是全局共享的，结果就是：账号里凭空
        多出一身别人的装备，或者自己昨天试的样例——登录后第一眼看到的不是
        自己的角色，会以为工具在乱用数据。

        现在两边完全分开：登录就进账号自己的状态，没有就弹导入框。
        游客模式的东西还在原处，退出登录能回去接着看。
        """
        token = accounts.new_session(ACCOUNTS, uid)
        return self._send(200, {"user": {"id": uid, "username": username}},
                          headers={"Set-Cookie":
                                   f"{COOKIE}={token}; Path=/; Max-Age=2592000; HttpOnly; "
                                   "SameSite=Lax"})

    def _auth_logout(self):
        token = self._cookie(COOKIE)
        _, user, _ = self._ctx()
        # 退出登录只是失效令牌，账号自己那套装备留在 STATE 里，
        # 下次登录还能接上；未登录那边的会话也一点不动。
        accounts.drop_session(ACCOUNTS, token)
        return self._send(200, {"user": None},
                          headers={"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0"})

    # ---------------------------------------------------------------- 载入

    def _build(self, text):
        """把 simc 原文变成一套会话，挂到当前用户名下，并记一条历史。"""
        prof = parse(text)
        if not prof.class_key:
            raise ValueError("没识别出职业。第一行应该是 warrior=\"名字\" 这种格式，"
                             "请确认粘贴的是 /simc 的完整输出")
        conn = connect()
        s = Session(conn, prof, load_budget(), raw_text=text)
        key, user, old = self._ctx()
        with LOCK:
            if old:
                try:
                    old["conn"].close()
                except Exception:
                    pass
            STATE[key] = {"conn": conn, "s": s, "user": user}
            if user:
                accounts.record_history(ACCOUNTS, user[0], text, prof,
                                        s.avg_ilevel()[0])
        return s

    def _load_profile(self, body):
        text = (body.get("simc") or "").strip().lstrip("\ufeff")
        if not text:
            return self._send(400, {"error": "simc 字符串是空的"})
        try:
            s = self._build(text)
        except ValueError as exc:
            return self._send(400, {"error": str(exc)})
        with LOCK:
            return self._send(200, s.snapshot())

    # ---------------------------------------------------------------- 计算

    def _crafted_of(self, body):
        raw = body.get("crafted") or []
        return tuple(int(x) for x in raw if str(x).isdigit())

    def _preview(self, s, body):
        slot_key = body.get("slot")
        item_id = body.get("id")
        if not slot_key or not item_id:
            return self._send(400, {"error": "缺 slot 或 id"})
        ilevel = body.get("ilevel")
        try:
            ilevel = int(ilevel) if ilevel else None
        except (TypeError, ValueError):
            ilevel = None

        with LOCK:
            if ilevel is None:
                cur = s.prof.items.get(slot_key)
                ilevel = (cur.comment_ilevel or cur.ilevel) if cur else None
            if not ilevel:
                return self._send(400, {"error": "这件装备没有装等，需要指定一个"})
            try:
                totals, avg = s.preview_full(slot_key, int(item_id), ilevel,
                                             self._crafted_of(body))
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            base = s.totals
            return self._send(200, {
                "totals": {k: round(v, 1) for k, v in totals.items()},
                "delta": {k: round(totals.get(k, 0) - base.get(k, 0), 1)
                          for k in set(totals) | set(base)},
                "ilevel": ilevel,
                "avg_ilevel": avg,
            })

    def _equip(self, s, body):
        slot_key = body.get("slot")
        item_id = body.get("id")
        if not slot_key or not item_id:
            return self._send(400, {"error": "缺 slot 或 id"})
        ilevel = body.get("ilevel")
        try:
            ilevel = int(ilevel) if ilevel else None
        except (TypeError, ValueError):
            ilevel = None
        with LOCK:
            if ilevel is None:
                cur = s.prof.items.get(slot_key)
                ilevel = (cur.comment_ilevel or cur.ilevel) if cur else None
            if not ilevel:
                return self._send(400, {"error": "这件装备没有装等，需要指定一个"})
            s.equip(slot_key, int(item_id), ilevel, self._crafted_of(body))
            return self._send(200, s.snapshot())

    def _save_choice(self, s, body):
        item_id = body.get("id")
        crafted = self._crafted_of(body)
        if not item_id or len(crafted) not in (1, 2):
            return self._send(400, {"error": "需要 id 和 1~2 个属性"})
        with LOCK:
            s.choices[int(item_id)] = crafted
            for it in s.prof.items.values():
                if it.item_id == int(item_id):
                    it.crafted_stats = crafted
            from wowlib.session import save_choices
            save_choices(s.choices)
            s.recompute()
            return self._send(200, s.snapshot())

    # ---------------------------------------------------------------- 历史

    def _history(self, path, body):
        _, user, _ = self._ctx()
        if not user:
            return self._send(401, {"error": "先登录"})
        if path == "/api/history/delete":
            accounts.delete_history(ACCOUNTS, user[0], int(body.get("id") or 0))
            return self._send(200, {"history": accounts.list_history(ACCOUNTS, user[0])})
        if path == "/api/history/load":
            text = accounts.get_history_text(ACCOUNTS, user[0],
                                             int(body.get("id") or 0))
            if not text:
                return self._send(404, {"error": "这条记录不在你名下"})
            try:
                s = self._build(text)
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            with LOCK:
                return self._send(200, s.snapshot())
        return self._send(404, {"error": "not found"})

    # ---------------------------------------------------------------- 配装

    def _loadout(self, path, body):
        _, user, _ = self._ctx()
        if not user:
            return self._send(401, {"error": "先登录才能存配装"})
        uid = user[0]

        if path == "/api/loadout/save":
            s = self._session()
            if not s:
                return
            if not s.raw_text:
                return self._send(400, {"error": "这套装备不是从 simc 导入的，存不了"})
            with LOCK:
                changes = s.changes()
                avg = s.avg_ilevel()[0]
                class_cn = s.prof.class_cn
            try:
                row_id, replaced = accounts.save_loadout(
                    ACCOUNTS, uid, body.get("name"), s.raw_text, changes,
                    class_cn, avg, (body.get("note") or "").strip(),
                    s.prof.char_name or "",
                    SPEC_CN.get((s.prof.spec or "").lower(), s.prof.spec or ""))
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            return self._send(200, {"id": row_id, "replaced": replaced,
                                    "loadouts": accounts.list_loadouts(ACCOUNTS, uid)})

        if path == "/api/loadout/load":
            row = accounts.get_loadout(ACCOUNTS, uid, int(body.get("id") or 0))
            if not row:
                return self._send(404, {"error": "这套配装不在你名下"})
            try:
                s2 = self._build(row["base_simc"])
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            with LOCK:
                skipped = s2.apply_changes(row["changes"])
                snap = s2.snapshot()
            snap["loaded"] = {"name": row["name"], "skipped": skipped,
                              "changes": len(row["changes"])}
            return self._send(200, snap)

        if path == "/api/loadout/delete":
            accounts.delete_loadout(ACCOUNTS, uid, int(body.get("id") or 0))
            return self._send(200, {"loadouts": accounts.list_loadouts(ACCOUNTS, uid)})

        return self._send(404, {"error": "not found"})


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    if not os.path.exists(DB_PATH):
        raise SystemExit("找不到数据库，先运行 python main.py --build")
    srv = Server(("127.0.0.1", PORT), Handler)
    print(f"配装规划器已启动：http://127.0.0.1:{PORT}")
    print("按 Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
