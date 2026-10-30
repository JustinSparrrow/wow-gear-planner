# src/server — HTTP 层

把 `Session` 暴露成 JSON 接口，顺带服务前端静态文件。只有一个文件 `serve.py`。

## 为什么用标准库 `http.server` 而不是框架

单人项目，接口就二三十个，`http.server` + 手写路由完全够用，引 Flask 反而多一层依赖。
状态本来就存在服务端，不需要 ORM、不需要会话中间件。

## 启动

```bash
python src/server/serve.py        # 或 python main.py --web
# http://127.0.0.1:8765
```

## 接口概览

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/bootstrap` | 槽位、赛季、build、登录状态、有无会话 |
| POST | `/api/profile` | 载入 simc 文本，建会话 |
| GET | `/api/snapshot` | 整份状态（前端渲染全靠它） |
| GET | `/api/candidates?slot=` | 某部位候选列表 |
| POST | `/api/preview` / `/api/equip` | 换装预览 / 换装 |
| POST | `/api/preview-gem` / `/api/preview-enchant` | 宝石/附魔的悬停预览（不改状态） |
| POST | `/api/gem` / `/api/enchant` | 插宝石 / 附魔 |
| POST | `/api/drill` | 用打孔器开/取消一个孔 |
| GET | `/api/gem-options` / `/api/enchant-options` | 宝石 / 附魔选项 |
| POST | `/api/undo` / `/api/reset` / `/api/reset-all` | 撤销 / 还原 |
| POST | `/api/register` / `/api/login` / `/api/logout` | 账号 |
| POST | `/api/loadout/*` / `/api/history/*` | 配装存档 / simc 历史 |
| GET | `/icon/<名字>` / `/icon/gems/` / `/icon/enchants/` | 图标 |

**预览类接口（`preview-*`）都不落状态**：算完立刻回滚，返回的是"如果换成这样，
属性会变成多少"。悬停划过候选卡时前端每秒可能发好几个，所以它们必须无副作用。

## 几个关键设计

- **状态按身份分**：`STATE` 字典的 key 是 `u<uid>`（登录）或 `a<浏览器id>`（未登录），
  不是全局一个。细节见根 README「多账号怎么隔开」。
- **`_session()` 返回 None 就回 409**，前端据此弹导入框。
- **静态文件只认 web 目录内**，路径带 `..` 一律拒绝。
- **图标路由只认 `gems` / `enchants` 两个子目录**，别的路径当装备图标处理，防路径穿越。

## 线程模型

`ThreadingMixIn`，每个请求一个线程。写操作都套 `LOCK`（一把全局锁），因为 SQLite
连接和 `STATE` 字典都不是线程安全的。计算本身很快，锁不会成为瓶颈。
