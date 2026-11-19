# RK87 Light

RK-H87 三模机械键盘的 RGB 灯光控制台。单文件 Windows 程序，自绘界面 + 系统托盘。

## 它能做什么

- **21 种内置灯效**：常亮、呼吸、霓虹流光、跑马灯、彩虹轮盘等，点击即选。
- **灯光参数**：亮度 0–5、动画速度 1–5、休眠定时 1–5。
- **颜色**：自定义 RGB 取色，或开启随机色。
- **托盘常驻**：最小化到托盘、右键菜单快捷切换灯效、开机自启。
- **安全写入**：所有硬件写入只走 `0x0A` 灯光通道。

## 安全设计（重要）

- 程序**默认以演示模式启动**，改动只存本地，不碰硬件。
- 必须显式点击「连接真机」才会打开 HID 设备。
- 底层 `rk87core.py` **只写 0x0A 灯光 Feature report**，绝不触碰其它 report。
  SINO WEALTH 主控的某些 report 会触发 ISP bootloader，盲写有变砖风险。

## 关于电量

RK-H87 的电量没有标准 HID 上报通道。官方做法是键盘端 `Fn+Enter` 用数字 1–0
在键盘上显示 10%–100%，属固件内部行为，主机侧读取不到数值。因此本工具不提供电量显示。

## 协议来源

协议逆向自开源项目 [Rangoli](https://github.com/rnayabed/rangoli)（GPLv3），
其作者用 Wireshark 抓取官方 RK 软件的 HID 流量逆向得到；本项目 `ef.json` 配置
对应的正是 RK-H87RGB（VID 258A / PID 00EF）。已用真机只读探测交叉验证。

## 运行 / 构建

```bash
# 源码运行（演示模式）
.venv/Scripts/python.exe rk87light.py

# 重新构建 exe
.venv/Scripts/python.exe -m PyInstaller RK87Light.spec --noconfirm
# -> dist/RK87Light.exe

# 重新生成图标/背景
.venv/Scripts/python.exe make_assets.py
```

依赖：`hidapi`、`pystray`、`Pillow`、`pyinstaller`（仅构建）。Python 3.10+ 带 tkinter。
