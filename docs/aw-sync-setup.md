# 跨机 AW 同步（派生快照 + Syncthing）

多台机器各自把「当天 AW 观测」导出成 `<host>-<date>.json`，丢进一个 Syncthing 共享文件夹；
汇集端（跑 timeplanner 的机器）读本机实时 AW + 其它机器的快照，合并出 ③观测线。
幂等、无冲突（每台机器只写自己 host 的文件）、绝不碰 AW 的 SQLite。

架构与取舍见对话；这里只记落地步骤。

## 数据流

```
[Win]  AW → scripts/aw_export.py ─┐
                                  ├─→  Syncthing 共享文件夹 ──→  [Win 汇集端] timeplanner
[Mac]  AW → scripts/aw_export.py ─┘        (timeplanner-aw)       merged_observe(): 本机实时 + 各机快照
```

快照 schema（v1，`timeplanner/core/aw_sync.py` 与 `scripts/aw_export.py` 保持一致）：
```json
{"schema":1,"host":"...","date":"YYYY-MM-DD","active_minutes":0.0,
 "focus_blocks":[["<iso>","<iso>"]],"top_apps":[["app",min]],"exported_at":"<iso>"}
```

## ⚠️ host 名必须稳定

合并按 `<host>-<date>.json` 认机器，`merged_observe()` 靠 `host` 跳过「自己那份」（本机走实时 AW）。
所以 **host 名一变，同一台机器同一天就会留下两份快照，合并时把自己加两遍**。

`socket.gethostname()` 在 DHCP 派生主机名的网络里就是不稳定的 —— NYU wireless 上这台 Mac 报
`10-20-89-245.dynapool.wireless.nyu.edu`，随 IP 变。实测：真实 137min 被合并成 273min，
`summary` 还会显示「合并 2 台」而其实只有一台。

因此每台同步的机器都要显式钉住名字：

- timeplanner 端：`.env` 里 `TIMEPLANNER_AW_HOST_NAME="JuanitadeMacBook-Air"`
- 裸脚本端：`scripts/aw_export.py --host JuanitadeMacBook-Air`（或 `AW_HOST_NAME` 环境变量）

两边必须**用同一个值**，否则汇集端认不出「自己那份」。名字会被 slug 化（非 `[A-Za-z0-9_.]` → `-`）。

> 改过某台机器的 host 名？去共享文件夹把它**旧名字的快照删掉**——钉名字只防新文件，
> 已经落盘的旧快照仍会被当成另一台机器加进来。

---

## 本机 Windows（汇集端）— 已完成

- Syncthing 2.1.2（scoop 装），GUI **http://127.0.0.1:8384**
- 设备 ID：`UAAIR6J-VG5U7QW-7K2PMIJ-2O35VVZ-G32HAPX-UVQ7KUA-P25L26N-N2OWPAB`
- 共享文件夹：ID `timeplanner-aw` → `C:\Users\scat\ActivityWatchSync`
- 开机自启：`…\Startup\syncthing-timeplanner.vbs`（隐藏窗口，无需管理员）
- `.env`：`TIMEPLANNER_AW_SYNC_DIR="C:/Users/scat/ActivityWatchSync"`
- 导出：`timeplanner aw-export`（或 `python scripts/aw_export.py --sync-dir C:\Users\scat\ActivityWatchSync`）
- 合并已生效：`timeplanner summary` 的 ③ AW 列 / app 占比会把各机数据合并

> 汇集端本机看自己的数据走**实时 AW**，不依赖自己的快照；导出只是为了「别的机器也能看到 Windows 的数据」，可选。

---

## macOS（记录端）— 进行中

- 设备 ID：`6ZIQFEW-U6L6P7L-AQHPICM-V25V7DL-5SQA3GP-LNSIZZ4-YGB5D63-5HJNUA3`
- 稳定 host 名：`JuanitadeMacBook-Air`（取自 `scutil --get LocalHostName`）
- 共享文件夹本地路径：`~/ActivityWatchSync`

### 1. ActivityWatch — ✅ 已在跑
`curl -s localhost:5600/api/0/info` 有响应即可（本机 v0.13.2）。

### 2. Syncthing — ✅ 已装并自启
```sh
brew install --formula syncthing        # 注意 --formula：`syncthing` 同时是 cask 名
brew services start syncthing           # 开机自启
open http://127.0.0.1:8384              # GUI
syncthing cli show system               # 里面的 myID 就是设备 ID（新版没有 --device-id）
```

### 3. `.env` — ✅ 已配
```ini
TIMEPLANNER_AW_SYNC_DIR="/Users/juanitahowe/ActivityWatchSync"
TIMEPLANNER_AW_HOST_NAME="JuanitadeMacBook-Air"     # 见上面「host 名必须稳定」
```
验证导出与合并：
```sh
timeplanner aw-export      # → 📤 已导出 … JuanitadeMacBook-Air-<date>.json
timeplanner summary        # ③ AW 分钟数应等于本机实时 AW，且 note 不出现「合并 2 台」
```

### 4. 配对（把 Windows 和 Mac 连起来）— ⬜ 待办
Mac 侧加 Windows 为远程设备，并接受共享文件夹：
```sh
syncthing cli config devices add \
  --device-id UAAIR6J-VG5U7QW-7K2PMIJ-2O35VVZ-G32HAPX-UVQ7KUA-P25L26N-N2OWPAB \
  --name windows-collector
```
然后 **Windows GUI** 里「Add Remote Device」粘贴 Mac 的设备 ID（见上），
编辑 `timeplanner-aw` 文件夹 →「Sharing」勾上 Mac → Save；
Mac GUI 弹「新文件夹」→ 接受，路径设 `~/ActivityWatchSync`（**Folder ID 必须是 `timeplanner-aw`**）。

### 5. 定时导出（每 10 分钟）— ⬜ 待办
`~/Library/LaunchAgents/com.timeplanner.awexport.plist`，`StartInterval` = 600，
`ProgramArguments` = `/usr/bin/python3 <repo>/scripts/aw_export.py --sync-dir ~/ActivityWatchSync
--host JuanitadeMacBook-Air`（**`--host` 别漏**，否则快照名跟着 IP 变）。然后：
```sh
launchctl load ~/Library/LaunchAgents/com.timeplanner.awexport.plist
```
或用 cron：
```cron
*/10 * * * * /usr/bin/python3 <repo>/scripts/aw_export.py --sync-dir ~/ActivityWatchSync --host JuanitadeMacBook-Air >> /tmp/aw_export.log 2>&1
```

---

## 验证

Mac 导出后，Windows 上等 Syncthing 同步过来（几秒），然后：
```sh
timeplanner summary          # ③ AW 那条线应出现 Mac 的 focus block，app 占比合并两台
```
`timeplanner summary` 顶部若显示「（合并 2 台：DESKTOP-…, <mac>）」即成功。

## 关掉/回退
- 停自启：删 `…\Startup\syncthing-timeplanner.vbs`（Mac：`brew services stop syncthing`）。
- 停同步：Syncthing GUI 删 `timeplanner-aw` 文件夹。
- 停合并：`.env` 里把 `TIMEPLANNER_AW_SYNC_DIR` 置空，timeplanner 立刻回到单机实时 AW。
