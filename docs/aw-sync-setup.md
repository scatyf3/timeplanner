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

### 4. Mac 侧 Syncthing 配对 — ✅ 已配
```sh
syncthing cli config devices add --device-id UAAIR6J-… --name windows-collector
syncthing cli config folders add --id timeplanner-aw --label timeplanner-aw --path ~/ActivityWatchSync
syncthing cli config folders timeplanner-aw devices add --device-id UAAIR6J-…
```

### 5. 定时导出（每 10 分钟）— ✅ 已装
`~/Library/LaunchAgents/com.timeplanner.awexport.plist`，`StartInterval`=600，`RunAtLoad`，
日志 `/tmp/timeplanner-aw-export.log`。`launchctl list | grep awexport` 第二列是上次退出码（0=好）。

> **launchd 跑的是 `~/.local/bin/aw_export.py` 这份拷贝，不是仓库里的脚本。**
> macOS TCC 不给 launchd agent 读 `~/Documents`，直接指仓库路径会
> `[Errno 1] Operation not permitted`。改了仓库脚本记得刷新拷贝：
> ```sh
> cp scripts/aw_export.py ~/.local/bin/aw_export.py
> ```
> 这也是 `aw_export.py` 写成零依赖单文件的原因——它本来就是拿去别的机器上部署的。

卸载：`launchctl unload ~/Library/LaunchAgents/com.timeplanner.awexport.plist && rm 该 plist`。

### 6. Windows 侧接受 — ⬜ 待办（唯一剩下的手工步骤）
Mac 已经在往 Windows 敲门了：日志里能看到 `Established secure connection … device=UAAIR6J`
紧接着 `Lost device connection … error="reading length: EOF"` —— 就是 Windows 还不认识 Mac，
握完手直接把连接关了。在 **Windows GUI**（http://127.0.0.1:8384）：

1. 「Add Remote Device」→ 粘贴 Mac 设备 ID `6ZIQFEW-U6L6P7L-AQHPICM-V25V7DL-5SQA3GP-LNSIZZ4-YGB5D63-5HJNUA3` → Save
   （Mac 已连过，多半会直接弹「New Device」提示，点接受即可）
2. 编辑 `timeplanner-aw` 文件夹 →「Sharing」勾上 Mac → Save

---

## 验证

配对连上后，Mac 导出 → Windows 等几秒同步过来，然后在 **Windows** 上：
```sh
timeplanner summary          # ③ AW 那条线应出现 Mac 的 focus block，app 占比合并两台
```
显示「（合并 2 台：DESKTOP-…, JuanitadeMacBook-Air）」即成功。

反过来在 **Mac** 上跑 `timeplanner summary`：它走本机实时 AW，会跳过自己那份快照，
所以在 Windows 的快照同步过来之前，note 应该是**空的**。若在只有一台机器时就看到
「合并 2 台」，说明共享文件夹里有同一台机器的重名快照——见上面「host 名必须稳定」。

```sh
launchctl list | grep awexport            # 第二列 0 = 上次导出成功
tail -3 /tmp/timeplanner-aw-export.log    # exported <host>-<date>.json (active …min, N blocks)
syncthing cli config devices list         # 应有 Windows + Mac 两个 ID
```

## 关掉/回退
- 停自启：删 `…\Startup\syncthing-timeplanner.vbs`（Mac：`brew services stop syncthing`）。
- 停定时导出（Mac）：`launchctl unload ~/Library/LaunchAgents/com.timeplanner.awexport.plist`，再删 plist。
- 停同步：Syncthing GUI 删 `timeplanner-aw` 文件夹。
- 停合并：`.env` 里把 `TIMEPLANNER_AW_SYNC_DIR` 置空，timeplanner 立刻回到单机实时 AW。
