# SC830 鼠标控制中心

为 AULA 狼蛛 SC830 实现的原生 Linux 配置软件。基于用户提供的 `SC830驱动V1.1.8.exe` 分析协议，以 Python 标准库和 Tk 构建，通过 Linux `hidraw` 接口与鼠标通信。

## 使用

克隆仓库后启动：

```bash
git clone https://github.com/JuanZoran/SC830-Linux.git
cd SC830-Linux
./run.sh
```

按下方安装步骤设置桌面入口和设备权限后，可在应用菜单搜索 **SC830 鼠标控制中心**，或运行 `sc830`。打开程序会查询设备状态。界面中展示的是本地配置，点击“应用当前页”或“应用全部配置”后才会写入鼠标。

## 外观

界面配色跟随 Omarchy 当前主题（`~/.local/state/omarchy/current/theme/colors.toml`），深浅模式、强调色和各页图标颜色都取自主题。执行 `omarchy theme set` 后，运行中的窗口会在约 1.5 秒内自动换色。界面按 Hyprland 显示器缩放放大；可用 `SC830_SCALE=1.5` 指定缩放，用 `SC830_THEME=/path/to/colors.toml` 预览其他主题。

窗口的 WM class 为 `Sc830`，Hyprland 可以用 `o.window({ class = "^(sc830|Sc830)$" }, { float = true, center = true })` 让它默认浮动。

## 功能

| 功能 | 提供的操作 |
|---|---|
| DPI | 1–5 档、启用/禁用档位、选择当前档位、指示灯颜色 |
| 回报率 | 125 / 250 / 500 / 1000 Hz |
| 灯效 | 关闭、混彩流光、呼吸、单色常亮、霓虹、跑马、混彩常亮、七彩波浪 |
| 按键 | 6 个可编程键、鼠标按键、DPI 循环/增减、快捷键、多媒体、火力键、禁用、宏 |
| 宏 | 6 个鼠标端槽位、录制、事件编辑、键盘/鼠标/滚轮/位移、延时、3 种循环方式 |
| 配置 | 5 份本地配置、JSON 导入导出、常规 Windows PSF 导入 |
| 设备 | 自动识别配置接口、状态和电量查询、有线接口优先 |

DPI 按 PAW3212 的原程序映射表选择：400–4800（步进 200）及 6000–9600（步进 200）。这是原软件使用的编码表。宏每个最多 125 个动作，单步延时上限 4095 毫秒；循环次数为 1–255。火力键间隔为 5–255，次数为 0 时按住连发。

宏录制在录制窗口内进行；键盘组合键可分别录制修饰键和普通按键。鼠标位移通过事件编辑器添加。宏保存到当前本地配置后，在“按键”页选择“宏”并填槽位号，再应用按键页即可下载并绑定。

配置文件位于 `${XDG_CONFIG_HOME:-~/.config}/sc830/`。`profile1.json` 至 `profile5.json` 为本地配置；`last-applied.json` 记录最近一次发送的设置及分区。5 份本地配置与鼠标端的 6 个宏槽位分别管理。

## 命令行

```bash
cd SC830-Linux

./run.sh list
./run.sh status
./run.sh dpi-values

# 预览完整报文
./run.sh --dry-run apply examples/default-profile.json

# 下列命令写入鼠标
./run.sh dpi 800 1200 1600 2400 4000 --active 2
./run.sh polling 500
./run.sh lighting steady --colors '#33AAFF' --brightness 3
./run.sh button 4 key:ctrl+c
./run.sh button 5 media:mute
./run.sh button 6 fire:50:3
./run.sh macro 1 examples/copy-paste-macro.json --bind 6 --loop count --repeat 1

# 只应用配置中的灯效
./run.sh apply examples/default-profile.json --sections lighting

# 有多个鼠标时，指定配置接口
./run.sh --device /dev/hidraw3 status
```

按键编号为：1 左键、2 右键、3 中键、4 前进、5 后退、6 DPI 键。完整配置会检查至少保留一个左键。单键命令固定保留物理左键；需要调整左键时使用完整配置。

导入原程序针对 PAW3212 导出的 `.psf`：

```bash
./run.sh convert /path/to/profile.psf /path/to/profile.json
```

PSF 的宏库另存于原程序的 `.mlf`。带宏绑定的 PSF 会提示先在本程序重建宏；常规 PSF 支持 DPI、灯效、回报率和按键编码。安装包内的通用工厂 PSF 使用其他传感器的默认范围，应使用原程序为 SC830 导出的配置。

## 安装与运行环境

运行需要 Linux、Python 3.11+、Tk，启动脚本使用 `/usr/bin/python3`。运行时无第三方 Python 依赖；其他 Linux 桌面使用内置配色，Omarchy 可自动跟随系统主题。

```bash
cd SC830-Linux
./install.sh
./install-permissions.sh
```

第二条安装命令通过系统认证安装 `/etc/udev/rules.d/70-sc830.rules`，为当前活动桌面用户授予 `248a:5b2e` 和 `248a:5b2f` 的 HID 访问权限。规则在重新插入设备后继续生效。通信使用 2.4G 接收器或 USB 有线连接；蓝牙配置协议尚待分析。

## 验证与开发

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s tests -v
```

已建立 **81 组原程序生成的报文向量**，覆盖 DPI 表、档位掩码、颜色、四种回报率、全部界面灯效、按键顺序和宏分包边界。共 14 项回归测试检查逐字节编码、状态解析、重试、输入校验及宏先下载后绑定。

实机已经读到 `248a:5b2f / M601 / PAW3212` 和电量。按用户选择，本轮保留鼠标现有设置；设置写入和实际灯效、宏触发的验收待手动点击应用后进行。详见 [验证记录](docs/verification.md)。

协议层 `sc830/protocol.py` 为纯数据编码，CLI 与 GUI 共用；`device.py` 负责设备通信，`profiles.py` 负责配置编译和持久化。协议依据和原程序地址见 [协议说明](docs/protocol.md)。

重新生成原程序对照向量时，将安装包解出的 `GamingMS.exe` 作为显式输入：

```bash
uv run --python 3.12 --with pefile --with unicorn \
  tools/original_oracle.py /path/to/GamingMS.exe tests/original-vectors.json
```

该工具只在 Unicorn 中执行编码函数，拦截 Windows API 并记录报文，过程不访问鼠标。项目保留自己的源代码、可重复运行的测试和分析说明；原安装包仍位于用户的下载目录。
