"""核心层：数据与规则。

这一层只依赖标准库和自己的 data/，**不 import 任何上层**
（wowlib / server / build）。看到本包里出现 `import simc` 就是分层破了。

    paths        所有路径的唯一来源，其它模块一律 from .paths import
    const        职业 / 槽位 / 属性 / 中文名
    curve        属性曲线：装等 -> 系数
    stats        单件属性换算（主属性 / 耐力 / 绿字三条线）
    source       装备来源（副本 / boss / 专业 / 套装）
    store        玩家自己填的配置（自选属性、面板基准）
    accounts     账号 / 登录令牌 / simc 历史 / 配装存档（独立的 accounts.db）

`catalog`（装备目录）原来也在这里，但它要判断职业能不能穿，得用 simc 里的
`class_primary`——数据层反过来依赖解析层。现在挪到了 `wowlib/catalog.py`。

这里故意不 import 子模块：`accounts` 要 `from .paths import`，一层 `__init__`
里再 import 子模块就会绕成环。调用方按需 `from wowgear import stats` 即可。
"""

__version__ = "3.0"
