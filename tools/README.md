# tools — 一次性脚本

和 `src/build/` 的区别：`build/` 是"每次换赛季都要重跑"的常规流程，`tools/` 是
"踩坑时写的探针"，跑一次就够，之后留作参考。

## 文件

| 文件 | 作用 | 还常用吗 |
|---|---|---|
| `sample_curve.py` | 从 wowhead 采样属性曲线，生成 `data/stat_curve.csv` | 换资料片/曲线变时要重采 |
| `package_beta.py` | 打内测包（`dist/魔兽装备规划器-内测版-v*.zip`） | 每次发版跑一次 |

## sample_curve.py 的教训（写在代码注释里）

采样时**必须核对 `slot` 字段，不能看英文名猜**。第一版拿 `Soulslither Spaulders`
（肩）当"大件"，得出"头和脚同档"的错误结论，结果头胸腿全用成中件系数，腿的绿字
少算 25%。样本部位对错了，整条曲线都是歪的。

## package_beta.py：内测包里故意不带的东西

打包的原则是「别人拿到能开起来、但看不到你的数据」：

| 不带的 | 为什么 |
|---|---|
| `data/accounts.db` | 你的账号、历史、配装存档 |
| `data/panel.json` / `player_crafted.json` | 你的面板校准和自选属性，会让别人的角色算歪 |
| `data/raw/`（57MB） | 只有 `--build` 重建库才用，运行时不需要 |
| `tests/my_profile.txt` | 本机真实角色导出（含角色名和服务器，不入库） |
| `tests/demo_profile.txt` | 同一份的脱敏版，**入库**，别人 clone 后用它 |
| `src/build/` `tools/` `ref/` | 开发期的东西 |

带的是运行界面真正需要的那部分：`main.py`、`src/`（前端+服务端+两个运行库）、
`data/wowgear.db`、`data/icons/`、`data/stat_curve.csv`，加一个 `启动.bat`。

`--keep` 会保留解压后的目录，方便发出去之前自己先点一遍。

