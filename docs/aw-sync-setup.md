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

## macOS（记录端）— 待办

### 1. ActivityWatch
确保已装并在跑（菜单栏有图标）。没装：`brew install --cask activitywatch` 或 https://activitywatch.net 。

### 2. Syncthing
```sh
brew install syncthing
brew services start syncthing          # 开机自启
open http://127.0.0.1:8384             # GUI
```
拿到本机设备 ID：GUI 右上「Actions → Show ID」，或 `syncthing --device-id`。

### 3. 配对（把 Windows 和 Mac 连起来）
两种方式二选一：

**A. 让我从 Windows 侧配（省事）**：把 Mac 的设备 ID 发我，我用 Windows 的 Syncthing API 把 Mac 加为远程设备并共享 `timeplanner-aw` 文件夹。然后你在 Mac GUI 上点「接受」，把该文件夹的本地路径设成 `~/ActivityWatchSync`。

**B. 全在 GUI 手点**：
1. Windows GUI「Add Remote Device」→ 粘贴 Mac 的设备 ID → Save。
2. Mac GUI 会弹出「新设备」→ 接受，粘贴 Windows 设备 ID。
3. Windows GUI 里编辑 `timeplanner-aw` 文件夹 →「Sharing」勾上 Mac → Save。
4. Mac GUI 弹「新文件夹 timeplanner-aw」→ 接受，路径设 `~/ActivityWatchSync`（**Folder ID 必须是 `timeplanner-aw`**）。

### 4. 部署导出脚本
把 `scripts/aw_export.py` 拷到 Mac（零依赖，系统 python3 即可）。先手动验证：
```sh
python3 aw_export.py --sync-dir ~/ActivityWatchSync
# 应输出：exported <mac-host>-<date>.json (active …min, N blocks)
```

### 5. 定时导出（每 10 分钟）
**cron**（最简单）：`crontab -e` 加一行——
```cron
*/10 * * * * /usr/bin/python3 /Users/<you>/aw_export.py --sync-dir /Users/<you>/ActivityWatchSync >> /tmp/aw_export.log 2>&1
```
**launchd**（更 mac 原生）：`~/Library/LaunchAgents/com.timeplanner.awexport.plist`，`ProgramArguments` 指向
`python3 aw_export.py --sync-dir ~/ActivityWatchSync`，`StartInterval` = 600，然后 `launchctl load`。

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
