# vrmmon — CPU / GPU 与主板供电温度监控

[![Windows offline checks](https://github.com/LightningZ-X/computer-monitor/actions/workflows/checks.yml/badge.svg)](https://github.com/LightningZ-X/computer-monitor/actions/workflows/checks.yml) · [反馈问题](https://github.com/LightningZ-X/computer-monitor/issues)

Windows 桌面监控工具：传感器表、SQLite/CSV 长时间记录、阈值告警、托盘常驻、开机自启。

**两个数据源都可独立工作**：LibreHardwareMonitor 直读硬件（自带内核驱动，不需要任何
第三方软件常驻），HWiNFO 共享内存作为可选补充。

---

## 快速开始

```bat
pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File tools\setup_lhm.ps1  :: 部署 LibreHardwareMonitorLib 及其依赖（一次性）
python tools\install_shortcut.py      :: 装启动器，之后任意终端敲 vrmmon
```

装好启动器后即可在**任意终端**使用（通常**不需要新开终端**，见下方说明）：

```bat
vrmmon            :: 启动界面（提权，完整传感器）
vrmmon noadmin    :: 普通权限启动（只有显卡/硬盘温度）
vrmmon minimized  :: 提权并直接最小化到托盘
vrmmon doctor     :: 终端里看体检报告（参数透传，如 vrmmon doctor --groups）
vrmmon where      :: 看安装路径、数据库、运行状态、权限、最近的退出原因
vrmmon stop       :: 请正在运行的实例优雅退出（不需要管理员权限）
vrmmon help       :: 帮助
```

### 四种启动方式（都会启动界面）

| 方式 | 权限 | 说明 |
|---|---|---|
| **双击 `以管理员身份运行.bat`** | 提权 | **最直接**，不依赖任何安装。弹一次 UAC |
| **双击桌面/开始菜单图标** | 提权 | 由 `tools\install_menu_shortcut.py` 生成 |
| **终端敲 `vrmmon`** | 提权 | 需先 `python tools\install_shortcut.py` |
| 双击 `启动监控.bat` | 普通 | 只有显卡/硬盘温度，**没有 CPU 温度** |

四者走同一条启动逻辑（`vrmmon/launcher.py`）：**若已有实例在运行，就把它的窗口
唤到前台**，而不是静默退出或重复启动。

> **`.bat` 必须保持纯 ASCII。** cmd.exe 按 OEM 代码页（中文系统是 GBK）读 `.bat`，
> 把 UTF-8 中文注释写进去会被误解码并**直接破坏批处理语法**。这个坑真踩过：
> 文件看着正常，双击却报一堆 `'xxx' is not recognized`，程序根本不启动。
> `tools/install_shortcut.py` 写启动器时也用 ASCII，同理。

命令行变体：`vrmmon noadmin`（普通权限）、`vrmmon minimized`（提权 + 直接进托盘）、
`python main.py`。

> **下面这些命令不启动界面**，是工具：
> `vrmmon doctor`（体检报告）、`vrmmon where`（安装/运行状态）、
> `vrmmon stop`（结束实例）、`vrmmon help`。

> **CPU 与主板温度需要管理员权限。** 这不是本程序的选择，而是 Windows 的硬约束，
> 详见下一节。程序内也有「以管理员重启」按钮，不必手动找 bat。

### 启动器装在哪里

`install_shortcut.py` **优先装到本来就在 PATH 里的目录**——默认是 Python 的
`Scripts`（pip 装命令行工具就用它），此时**完全不修改 PATH**，装完立刻可用。

这一点很关键：**新追加进 PATH 的目录对已经在运行的 Explorer 不可见**，而用户新开的
终端是从 Explorer 继承环境的。所以"改注册表 + 通知刷新"这套做法经常表现为
"装了但终端里找不到命令"，得注销一次才行。装到原有 PATH 目录就绕开了整个问题。

只有当 `Scripts` 不在 PATH 里时，才退回"专用目录 + 追加 PATH"的方案，此时才会写
`HKCU\Environment\Path`（当前用户，不碰系统 PATH），并且**严格保持注册表类型**
——用户 PATH 常是 `REG_EXPAND_SZ` 且含 `%USERPROFILE%` 这类变量引用，改成
`REG_SZ` 会让它们失效。原值备份在 `path-backup.txt`。

查看/卸载：`python tools\install_shortcut.py --show` / `--uninstall`。

---

## 为什么必须管理员权限

CPU 温度来自 CPU 内部 DTS，要读 **MSR 寄存器**；主板温度来自 **Super I/O / EC**，
要读**端口 I/O**。两者都是 ring0 操作，Windows 不允许普通进程执行。

我把所有可行路线都实测过：

| 路线 | 结果 | 证据 |
|---|---|---|
| NVAPI 热目标 | ❌ 驱动拒绝 | `sensorIndex=3/4` 返回 `-5 (INVALID_ARGUMENT)`，`POWER_SUPPLY(4)` 拿不到 |
| WMI ACPI 热区 | ⚠️ 只有机箱区 | `Win32_PerfFormattedData_Counters_ThermalZoneInformation` 给 `\_TZ.TZ00 = 27.9 °C` |
| LHM 非提权 | ⚠️ 只有显卡/硬盘 | CPU 温度传感器存在 **51 个但 Value 全是 None** |
| PawnIO 非提权 | ❌ ACCESS_DENIED | `\\.\PawnIO` 返回 Win32 错误 **5** |

**PawnIO** 是当前唯一已正确安装进 DriverStore、且不在微软易受攻击驱动黑名单上的
合规 ring0 框架，LHM 0.9.7 支持它（模块集含 `IntelMsr`、`LpcIo`、`LpcAcpiEc`、
`Nvidia`）。它在本机**正在运行**，但设备访问被限定给管理员。

---

## 本机实测能力矩阵（ASUS G835LW / Core Ultra 9 275HX / RTX 5080 Laptop）

| 传感器 | HWiNFO 共享内存 | LHM 提权 | LHM 不提权 | NVML |
|---|---|---|---|---|
| CPU 各核 / 封装 | ✅ 50 项绝对温度 | ✅ **24 个每核绝对温度** + Core Max/Average + 封装功率 | ❌ | ❌ |
| GPU 核心 | ✅ | ✅ | ✅ | ✅ |
| GPU 热点 | ✅ 1 项 | ✅ **7 项** | ❌ | ❌ |
| GPU 显存 | ✅ 9 项 | ✅ 9 项 | ⚠️ 仅结温 | ❌ |
| 内存 DIMM | ✅ 2 项 | ✅ 2 项 | ❌ | ❌ |
| 硬盘 | ✅ 6 项 | ✅ 6 项 | ⚠️ 仅阈值 | ❌ |
| **主板 PCH** | ✅ **4 项** | ❌ **0 个读数** | ❌ | ❌ |
| **供电 VRM** | ❌ | ❌ | ❌ | ❌ |
| **VR 过热报警位** | ✅ **3 项** | ❌ | ❌ | ❌ |
| **VRM 输出电流** | ✅ | ❌ | ❌ | ❌ |

**两个数据源是互补的，不是替代关系**：LHM 独有 GPU 热点 7 通道；HWiNFO 独有
主板 PCH 4 通道、VR 过热报警位、VRM 输出电流。想看得最全就两个一起开。

关键实测：LHM 即使提权 + PawnIO 生效，`Motherboard: ASUS G835LW` 这个硬件节点
**贡献 0 个读数**——没有 Super I/O、没有 EC、没有 PCH。

---

## 关于「供电温度」

**主板 CPU 供电（VRM / MOSFET）温度没有标准接口。** 多数消费级多相供电方案不提供
可直接读取的温度遥测；主板上的"VRM 温度"通常要额外焊一颗 NTC 热敏电阻接到
Super I/O 或 EC 的某个 AD 引脚，而**挂在哪个脚、标度多少只有主板厂知道**——
没有 ACPI 标准方法，没有 WMI 类。所以 HWiNFO / AIDA64 / LHM 都得靠逐型号逆向。

- 连 [给华硕 Z590-E 加 VRM 温度支持](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/issues/1546)
  都要单独开工单；
- [旗舰桌面板 ASUS Maximus XI Formula 也可能读不到](https://www.hwinfo.com/forum/threads/no-vrm-temps-asus-maximus-xi-formula.5382/)；
- 命名还逐板不同（[MSI B450 叫 `VRM MOS`](https://www.hwinfo.com/forum/threads/vrm-mos-on-msi-b450-tomahawk-max.6512/)）。

**本机（笔记本）实测：两个数据源都没有 VRM 通道。** 换到有 VRM 传感器的主板时，
本程序会自动识别显示（分类器同时支持中英文命名）。

读不到时的替代观测（程序已内置）：

| 替代观测 | 含义 |
|---|---|
| **供电过热报警** | `IA / GT / RING: VR 过热警报`——CPU 自己上报的供电过热事件位，唯一直接来自供电模块的信号 |
| **VRM 输出电流** | `VR VCC 电流 (SVID IOUT)`，实测 0~41 A，是 VRM 发热的成因本身 |
| **CPU 封装 / 核心温度** | 供电模块物理上就贴着 CPU 封装 |
| **GPU 热点温度** | 显卡侧最接近"供电/发热状况"的可读指标 |

---

## 用法

- 表格按硬件分组展开；每个分组下是该硬件的全部读数与角色判定。
- 默认只显示**真实温度**与供电/报警相关项；`显示全部读数` 可看全量 600+ 项。
- 工具栏：暂停、导出 CSV、重置极值、采样间隔、开机自启、告警声音、**以管理员重启**。
- 托盘图标直接显示当前最高温度并随告警变色；关闭窗口默认最小化到托盘继续记录。
- 数据持续写入 `data/vrmmon.sqlite3`。

## 采样间隔：最低 5ms

下限 `MIN_INTERVAL_S = 0.005`（5ms）。**它不是改个数字就成立的**——从 50ms 压到 5ms，
下面四处都得动，少一处就设了也跑不到：

| 障碍 | 现象 | 处理 |
|---|---|---|
| 单轮采集本身要 92.6ms | GPU 的 `Update()` 独占 46ms（NVAPI 调用），全部硬件每轮刷新 | 按硬件类型分级刷新：CPU 每轮；GPU 1s；主板/内存 2s；存储 5s。单轮降到 **4.1ms** |
| 每轮重复读传感器静态属性 | 104 个读数 × 6 次跨 CLR 边界属性访问 | 静态信息（名称/类型/角色**与 SensorId**）只读一次并缓存，每轮只读 `Value/Min/Max` |
| `_run` 是「跑完再睡 interval」 | 实际周期 = 单轮耗时 + interval | 改成补偿调度：**间隔是周期**，`wait(interval - 已耗时)` |
| **Windows 计时器粒度 15.6ms** | `Event.wait(5ms)` 实际睡满 15.6ms。实测：不睡能跑 211 轮/秒，设 10ms 却只有 **55** 轮/秒——瓶颈根本不在采集而在睡眠 | 间隔 < 50ms 时调 `timeBeginPeriod(1)`，退出时 `timeEndPeriod(1)`，与线程生命周期配对 |

实测（93 个传感器，HWiNFO 未运行）：

| 设定 | 实测 | 达成率 | 采集线程占用 |
|---|---|---|---|
| 5ms | 153 轮/秒 | 77% | ~82% 单核 |
| **10ms（当前默认）** | **83 轮/秒** | **83%** | ~47% 单核 |
| 20ms | 44 轮/秒 | 89% | ~24% 单核 |
| 50ms | 19.4 轮/秒 | 97% | ~9% 单核 |

**5ms 已经贴着这个技术栈的上限了。** 实测单轮耗时分布：中位 4.1ms、p90 5.4ms、
p99 43ms。那 2.7% 的慢轮吃掉了 17% 的墙钟时间，A/B 测下来：

- GPU 的 46ms 尖峰（每秒一次）占其中约 1/3；把它关掉，轮数只从 1523 涨到 1645
- **剩下 2/3 不是本程序的代码**——是 .NET GC 停顿与系统调度，pythonnet 栈上消不掉

> 换取的是 CPU：5ms 时采集线程占 ~82% 单核，10ms 约 47%。间隔越小越费电，
> 而**数据新鲜度另有一道不属于本程序的上限**：HWiNFO 的共享内存按它自己的
> `SensorInterval` 刷新（本机 100ms），所以低于 100ms 时 HWiNFO 那一路会读到
> 重复快照，只有 LHM 那一路是真的高频。

状态栏会显示 `采样 83/秒（设定 100）`，累计平均值低于设定 90% 时转警示色——
这比"某几轮被系统拖慢"更能说明到底跟不跟得上。

### 采样快 ≠ 入库也要快

这是最容易踩的坑：50ms 采样 × 745 个读数 = **14,754 行/秒 ≈ 145 GB/天**，
几天就撑满磁盘。所以两者解耦：

- `interval_s`（采样）：界面与**告警**都按它走，要抓瞬时尖峰就调小。
- `log_interval_s`（入库，默认 1.0）：落盘按它节流。设 0 表示每轮都写。

配套的 `retention_days`（默认 2.0）每 5 分钟清理一次过期记录，设 0 表示不清理。
没有它，即使 1 秒采样也是 3~10 GB/天。

| 配置 | 入库 | 磁盘 |
|---|---|---|
| 50ms 采样 + 1s 入库（默认） | 785 行/秒 | **10.4 GB/天** |
| 50ms 采样 + 0（每轮都写） | 14,754 行/秒 | 145 GB/天 |

状态栏会实时显示 `采样 83/秒（设定 100）` 与 `行/秒 ≈ GB/天`，超过 1 GB/天、
或累计平均采样率低于设定 90% 时转警示色。

## 数值精度

精度**对齐 HWiNFO 的惯例**：按传感器类型给固定位数。

| 单位 | 精度 | 示例 |
|---|---|---|
| `°C` 温度 | 1 位 | `61.76` → `61.8 °C` |
| `V` 电压 | 3 位 | `0.688` → `0.688 V` |
| `A` 电流 | 3 位 | `41.254` → `41.254 A` |
| `W` 功耗 | 2 位 | `41.254` → `41.25 W` |
| `RPM` 风扇 | 整数 | `1234.6` → `1235 RPM` |
| `MHz` 频率 | 整数 | `2285.24` → `2285 MHz` |
| `%` 占用率 | 1 位 | `37.25` → `37.2 %` |

规则**只定义在一处**（`vrmmon/sensors.py` 的 `DECIMALS_BY_UNIT`），所有显示位置
自动一致：传感器表三列、托盘悬停提示、体检报告都走 `format_value(value, unit)`，
调用方只传单位、不必各自记精度。改任何一档只改那张表。

### 为什么 CPU 温度总是 `65.0` 而不是 `65.3`

**因为传感器本身就只报整数，不是显示把小数抹掉了。** 查全库 154 个温度传感器：

| | 数量 | 说明 |
|---|---|---|
| 有小数值 | 17 个 | `GPU Core`（97%）、`GPU Hot Spot`×7、`Core Average`（91%）、`DIMM #1/#3`（0.25 分辨率）、HWiNFO `GPU 温度/热点` |
| **全是整数** | **137 个** | **全部 CPU 温度**：`CPU IA 核心`、`CPU 封装`、`CPU Package`、`Core Max`、`E-Core #*` |

原因是硬件层面：**Intel 的 CPU DTS 在 MSR 里存的就是整数**（`IA32_THERM_STATUS`
是"距 TjMax 多少度"的整数值），LHM 算 `TjMax - 偏移` 出来仍是整数；
**NVML 报 GPU 温度同样是整数**（实测 100% 为整数）。

所以温度要看到小数，得看 GPU 核心/热点、`Core Average` 或内存 SPD 那几路。

CSV 导出写的是**原始浮点数**、不做四舍五入，精度规则只作用于界面显示。

## 目录结构

```
main.py                     入口
vrmmon/
  cli.py                    命令行入口（vrmmon 启动器调用的就是它）
  sensors.py                统一传感器模型（Sensor / Role / Registry）
  classify.py               中英文命名 → 物理角色（含"伪温度"排除规则）
  elevation.py              管理员权限检测与提权重启
  single_instance.py        单实例互斥体（防止两个进程同时写库/占托盘）
  shortcut.py               启动器位置约定（优先用已在 PATH 的目录）
  shutdown.py               协作式关闭通道（vrmmon stop 靠它，不需要提权）
  logging_setup.py          异常 → vrmmon-error.log；启动/退出原因 → vrmmon-lifecycle.log
  providers/
    lhm.py                  LibreHardwareMonitor 直读（pythonnet，主数据源）
    hwinfo.py               HWiNFO 共享内存（可选补充）
    nvml_gpu.py             NVIDIA NVML（GPU 兜底）
  collector.py              采集线程：轮询 / 历史 / 入库 / 告警
  store.py                  SQLite 记录 + CSV 导出
  alerts.py                 阈值告警（滞回 + 冷却）
  config.py / autostart.py / doctor.py
  ui/main_window.py         主窗口（tkinter）  ui/tray.py 托盘（pystray）
tools/
  setup_lhm.ps1             部署 LHM 及其 NuGet 依赖
  install_shortcut.py       装启动器（优先用已在 PATH 的目录）/ 查看 / 卸载
  run_elevated.py           以管理员身份运行命令
  restart_elevated.py       提权结束所有实例并重启
  elevated_report.py        提权后生成体检报告
  autostart_launch.py       开机自启入口（静默发起提权）
  probe_lhm.py              直接看 LHM 能读到什么
  probe_pawnio.py           检查 PawnIO 可访问性与 LHM 的 PawnIO API
  probe_vrm.py              用历史数据判断某通道"是不是供电温度"
tests/                      见下
```

## 设计要点

- **界面只用标准库 tkinter**，不引入 PySide6（实测该包在本机网络下 20 分钟没能完成
  依赖解析；必要的小包 10 秒装完）。
- **LHM 经 pythonnet 进程内加载**，不启子进程、不走 IPC。代价是首次 `Computer.Open()`
  要枚举全部硬件，需数秒——所以采集线程启动时先热身一次，避免首帧采样被拖掉。
- **`SensorId` 必须包含 kind**：同一分组下会出现同名但不同量的传感器。
  LHM 的 `CPU Package` 同时是**温度**和**功耗**，`P-Core #1` 同时是**温度/电压/频率**。
  标识不含 kind 时它们会合并成一个对象，后写入的覆盖先写入的——实测会**静默丢掉
  24 个每核绝对温度和 CPU 封装温度**。这个 bug 是靠比对数据库里 `role × kind` 矩阵
  才发现的，不是靠读代码。
- **区分"伪温度"**：`与 TjMax 的差值`是相对量、`Thermal Sensor High Limit` 是阈值、
  `Temperature Sensor Resolution` 是分辨率——它们单独归为 `Role.META` 并从默认视图
  与告警中排除；而 SSD / 内存这类**真实温度**的角色是 `Role.OTHER`，**仍然会告警**。
- 界面刷新 800ms 一次，与采样间隔无关——采样再快也不会拖慢界面。
- SQLite 开 `check_same_thread=False` + 可重入锁（连接在主线程创建、在采集线程写入）；
  CSV 导出走独立只读连接，长时间导出不阻塞采集。

## 测试

```bat
python tests\test_hwinfo_parse.py   :: 21 项：共享内存布局 / 越界防护 / 分类 / 标识唯一性 / 告警 / 日志 / 关闭通道
python tests\smoke_collector.py     :: 端到端：真实取数 → 入库 → CSV 导出
python tests\test_ui_smoke.py       :: 界面：构建真实窗口，断言控件文字、表格行与状态栏
```

`test_hwinfo_parse.py` 用合成缓冲区验证 264/316 字节结构体偏移、GBK 中文解码、
坏 magic 与越界拒绝，不需要 HWiNFO 在运行。
`smoke_collector.py` 会等待 LHM 冷启动完成（而不是固定 sleep），并在导出前先暂停
采集——否则采集线程会在导出与 stop() 之间再落一轮，造成偶发失败。

## 诊断

```bat
python -m vrmmon.doctor --groups     :: 按硬件分组汇总读数/温度个数
python -m vrmmon.doctor --grep VR    :: 只看名称匹配的传感器
python -m vrmmon.doctor --out 报告.txt :: 写 UTF-8 文件，避免控制台 GBK 乱码
python tools\probe_vrm.py            :: 用历史数据判断某通道"是不是供电温度"
```

`probe_vrm.py` 原理：供电区探头贴着 CPU 供电模块，温度必然跟着 CPU 功耗走。
在 16.8 分钟实测数据上（CPU 封装 66~90 °C、标准差 4.02）：CPU DTS 各核
+0.79~+1.00，而 **PCH 4 通道只有 +0.29~+0.33**，与 GPU 核心(+0.32)、显存
(+0.20~+0.28) 同级——所以 PCH 不是被误标的供电探头。

## 排查

| 现象 | 处理 |
|---|---|
| 状态栏显示「仅显卡/硬盘可用」 | 未提权。点「以管理员重启」 |
| 显示「CPU 温度传感器存在 51 个但全部无值」 | 同上，内核驱动未加载 |
| 显示「缺少 packages\flat472」 | 运行 `python tools\setup_lhm.ps1` |
| 显示「未安装 pythonnet」 | `pip install pythonnet` |
| HWiNFO 那行 FAIL | 正常——它是可选数据源，不影响独立运行 |
| 供电温度「本机未暴露」 | 硬件不提供，见上文；看替代观测项 |
| 体检报告乱码 | 用 `--out 文件`，别用 `>` 重定向 |
| 程序无声消失 | 看 `data\vrmmon-error.log`（异常）与 `data\vrmmon-lifecycle.log`（**含退出原因**） |
| 终端里敲 `vrmmon` 提示找不到命令 | `python tools\install_shortcut.py --show` 看启动器位置；若"不在 PATH 中"，注销或重启资源管理器一次 |
| `vrmmon` 找不到但 `python main.py` 正常 | 同上——启动器位置问题，不是程序问题 |
| `vrmmon stop` 说没在运行但明明在跑 | 升级前的旧实例没有关闭通道，用任务管理器结束 pythonw.exe |
| 双击窗口的 × 后程序不见了 | 托盘图标不可用时关闭窗口即退出；`vrmmon-lifecycle.log` 会写明原因 |

## 已知限制

- **开机自启会弹一次 UAC**：Windows 不允许自启项静默提权。想免掉需要「最高权限
  的计划任务」或常驻服务（本次未做）。
- 普通权限下读不到 CPU 与主板传感器，这是系统约束。
- GPU 供电(VRM)温度在本机不存在用户态接口；NVAPI 的 `POWER_SUPPLY` 被驱动拒绝。
- **数据库增长很快**：约 700 个读数/轮，1 秒入库即 3~10 GB/天。已有两点应对：
  采样与入库解耦（`log_interval_s`）+ 定期清理过期记录（`retention_days`，默认保留 2 天）。
  详见上面「采样间隔」一节。

## 视觉风格

界面按 `D:\deepseekharness\psu-calculator`（整机功耗计算器）的设计系统重做，
那份 `docs/DESIGN.md` 的硬规则都在 `vrmmon/ui/theme.py` 里落地。改界面之前先读那几条，
否则很容易退回到"一眼 AI"的配色：

| 规则 | 本项目的落地 |
|---|---|
| 背景**三级亮度**分区，靠亮度差与细分隔线分组 | `--nav #0a0a0a` 顶栏 / `#121212` 主区 / `#181818` 凹陷（传感器表） |
| **全局只有一个强调色**，只用于 1px 描边、指示条、小控件 | ROG 红 `#ff0033`：`///` 分区标记、4px 关键指示条、状态徽标描边、按钮按下态 |
| **禁止给大容器铺红底** | 传感器区不套深色卡片、不铺红；靠 `///` 标题 + 4px 红条 + 细分隔线 |
| 圆角 2px，面板保持干净矩形 | ttk 控件本就是矩形，直接符合 |
| 禁止渐变、外发光、emoji；阴影只给浮层 | 界面与图标都是纯色块，无渐变无发光 |
| 字号/字重收敛 | Segoe UI 8/9/10/11/15；字重只用 400/600/700 |
| 数字必须对齐 | 状态栏与所有数值一律 Consolas 等宽 |

### logo 是一套完整的 lockup

与功耗计算器**完全一致**的图标系统。那边 `.logo` 是 `mark` + `wordmark` 两个素材并排
（`width 22px / aspect 88/166` 与 `width 140px / aspect 406/55`、`gap 11px`），
本项目按同一比例放大到标志高 34px，得到 **18×34 标记 + 115×16 字标、间距 9px**
（字标宽/标志高 = 3.382，那边是 3.373）。

两份素材原图都是"纯白 + 透明遮罩"（`assets/lightning-*-source.png`，来自用户提供的
截图，已获授权从中提取，轮廓未重绘），按用途上色——和那边 CSS 用
`background: var(--rog)` + `mask-image` 的做法一致。

| 位置 | 用什么 | 来源 |
|---|---|---|
| 窗口 / 任务栏 | `assets/vrmmon-<哈希>.ico` | `main_window._apply_window_icon()` |
| 桌面 / 开始菜单快捷方式 | 同上 | `tools\install_menu_shortcut.py` |
| 顶栏 lockup | `assets/vrmmon-mark.png` + `vrmmon-wordmark.png` | 同上（tkinter 不能给图片上色，故预先生成） |
| 托盘 | 运行时按状态色给遮罩上色 | `ui/tray.py: make_icon()`，橙=警示、红=危险、绿=正常、灰=无数据 |

### 图标：闪电必须够大

`.ico` 的几何直接对齐那边的 **`favicon.svg`**：满幅 `--bg #121212` 底、**闪电占高 94%**、
无描边无圆角。

> **这条踩过坑**：一开始我把闪电做成占高 58% 并加了描边和圆角。结果缩到任务栏的
> 16px 时，闪电只剩 **7 个红色像素**，整个图标看上去就是"一个黑方块"——
> 反馈是"图标没有改过来"。改成 94% 后 16px 下有 **26 个红像素（10.2%）**，能认出来了。
> 小尺寸图标的可辨识度得按**像素数**验证，不能只看大图好不好看。

> 他们两处图标的比例其实不同：`favicon.svg` 闪电占高 **90.6%**、
> `apple-touch-icon.png` 只占 **62.2%**。本项目取前者——任务栏是 16~48px 的小尺寸，
> 大闪电才认得出。

### 图标文件名为什么要带哈希

`assets/vrmmon-<8位哈希>.ico`——哈希纯粹是为了**绕开 Explorer 的图标缓存**。

Explorer 的图标缓存**以路径为键**：路径不变、文件内容变了，它照样用旧图标。
实测踩到过：

```
iconcache_16.db / _32.db / _48.db …   最后写入  09:59:39
assets/vrmmon.ico                     最后写入  16:25:08
```

缓存比新图标早了 6 个多小时，桌面一直显示旧图标。跑 `ie4uinit -show`
**返回成功但并没有重写那些缓存文件**；而彻底清缓存要停掉 explorer.exe，
会关掉用户所有资源管理器窗口。

把内容哈希写进文件名后，路径一变就不会命中旧缓存，**也不用重启 Explorer**
（再对桌面发一次 `SHChangeNotify(SHCNE_ASSOCCHANGED)` 让它重绘即可）。
`install_menu_shortcut.py` 每次生成会自动删掉旧哈希的图标，不会越积越多。

**产品名与品牌名是分开的**，这也是照搬那边的约定：他们的 logo 是 `LIGHTNING`，
页面标题却是「台式机功耗计算器」。所以这里顶栏是 LIGHTNING lockup，
窗口标题/托盘提示/快捷方式名仍是「vrmmon 供电温度监控」。

素材路径集中在 `vrmmon/ui/assets.py`（此前三处各拼一遍路径，容易漏改）；
设计令牌在 `vrmmon/ui/theme.py`，改配色只动那一个文件。

## 动效

只剩一处，且是**有信息量的反馈**：

| 动效 | 位置 | 对应设计 |
|---|---|---|
| 告警时状态栏脉冲（三角波 3 次后衰减，结束精确回到常态色） | 状态栏 | `psu-ui-feedback`（控件改动反馈） |

缓动曲线直接用那边 CSS 的同名三条（`--ease` / `--motion-enter` / `--motion-fast`），
按同样四个分量实现 cubic-bezier（`ui/motion.py`），不另调。
调度按**真实流逝时间**算进度——掉帧只跳帧，不会让动画变慢。

> **启动动画已按要求删除**（曾有黑幕 → 光晕 → 光束 → 闪电定格的 1.8s 开场）。
> 相关代码 `ui/boot.py`、菜单项、入场补间全部移除，界面冒烟测试有断言守着
> 「`ui/boot.py` 已删除」「窗口上不存在启动动画对象」「窗口内画布数始终为 0」。

### reduced-motion 仍然生效

`config.json` 的 `"animations": false`，或 Windows 关闭「在 Windows 中显示动画」
（读 `SPI_GETCLIENTAREAANIMATION`）时，告警脉冲整段归零——不是加速播放。

> tkinter 两个坑，改这里之前先看：
> ① `Canvas` 把 `lift`/`tkraise` **都**重载成 `tag_raise`（提升画布元素），
> 要提升画布**控件**本身必须显式调 `tk.Misc.tkraise`；
> ② Label 自身的 `padx` 只接受单个距离，传 `(a, b)` 会报 `bad screen distance`——
> 间距要用 `pack_configure(padx=...)`。
> 这两条都会让补间回调抛的 `TclError` 被静默吞掉、动画"停在半路"，
> 所以 `motion.Animator` 会把这类异常记一次日志——空手查这种问题代价很大。


