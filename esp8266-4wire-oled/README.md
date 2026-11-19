# ESP8266 联网时钟 · 极限精简版

0.96" 128x64 SSD1306（7 针 SPI）+ ESP8266 + MicroPython。
**目标：1MB flash 的模组（如涂鸦 TYWE3L、GD25Q80 flash）也能跑。**

上一版（功能完整）在 `.bak-v2/` 里，需要城市表/设置网页时再回去用。

## 内存账（实测，4MB 板 gc 后读数）

| 阶段 | 剩余堆 | 说明 |
| --- | --- | --- |
| 开机 | 35,712 | |
| import 全部模块 | 20,992 | **常驻 14,720** |
| + OLED 初始化 | 19,792 | 帧缓冲 1KB |
| + NTP/定位/天气跑完 | 19,440 | |
| + 配网页监听 | 19,296 | |

**整套运行时占 16,400 字节左右**，跑 1MB 固件（堆约 22KB）还剩约 5.6KB 给 HTTP 缓冲
（页面 1.3KB、天气响应几十字节），够用但不宽裕。

mpy 体积：`app 6452 + web 2178 + ssd1306 989 + config 758 = 10,377 字节`
（上一版是 16,821，砍掉 38%）。

## 砍掉了什么

- **城市表**：不存在的。定位靠出口 IP，直接把经纬度喂给天气源。
- **WiFi 扫描列表**：配网页只给输入框，手机上本来就看得见自己的 WiFi 名。
  省掉 `w.scan()`（阻塞 2 秒）+ 每个 SSID 的字符串。
- **设置网页**：亮度/24 小时制/夜间变暗/翻转都在 `config.py`，改完重新上传。
- **ujson**：凭据改存两行纯文本 `wifi.txt`，定位缓存存 `geo.txt`。
- **HTTP 重定向跟随**、多 NTP 服务器兜底、全部调试 `print`。
- 12 小时制、夜间变暗、抗烧屏平移**保留**（各占几百字节，值得留）。

## 接线（屏 → 模组）

裸模组（涂鸦 TYWE3L / ESP8266EX）上**没有 D1/D2 这类 NodeMCU 编号**，按 GPIO 对。

### 4 针 I2C 屏（默认，`IFACE = "i2c"`）

| 屏丝印 | 模组 | GPIO | 说明 |
| --- | --- | --- | --- |
| GND | GND | — | 地 |
| VCC | 3V3 | — | 1.3" 屏标 5V/3.3V，直接吃 3.3V |
| SCL | GPIO5 | 5 | 时钟 |
| SDA | GPIO4 | 4 | 数据 |

只要 4 根线，SPI 那 5 根全省了。模块背面一般自带 4.7k 上拉电阻；如果扫不到地址，
把 SCL/SDA 对调试试（不同厂家的丝印顺序不统一），或把 `I2C_FREQ` 降到 `100_000`。

### 7 针 SPI 屏（备用，`IFACE = "spi"`）

| 屏丝印 | 模组 | GPIO | NodeMCU | 作用 |
| --- | --- | --- | --- | --- |
| GND | GND | — | GND | 地，必须与 CH340/电源共地 |
| VCC | 外部 5V 或 3V3 | — | VIN / VU | 见下方供电说明 |
| D0 / SCL | GPIO14 | 14 | D5 | SPI 时钟 |
| D1 / SDA | GPIO13 | 13 | D7 | SPI 数据 |
| RES / RST | GPIO4 | 4 | D2 | 复位 |
| DC / A0 | GPIO5 | 5 | D1 | 命令/数据 |
| CS | GPIO15 | 15 | D8 | 片选 |

GPIO12（MISO）不接。SCK/MOSI/CS 绑在硬件 HSPI 上**改不了**，RES/DC 可以改
（`config.py` 的 `RES_PIN` / `DC_PIN`）。

### 供电：接错就是全黑

屏的 VCC 不是一个固定答案，看背面：

- 背面有 **662K**（XC6206，3.3V LDO）→ VCC 必须 **5V**。给 3.3V 时压差被吃掉，
  输出掉到 3V 以下，屏全黑不亮。
- 背面没有 LDO（裸 3.3V 屏，通常只有一颗电容）→ VCC 接 **3V3**。

裸模组只有 3.3V，要 5V 就从 CH340 的 **5V 脚**（USB 供的）取，地一定要和模组连一起。

### GPIO15 是启动采样脚

上电瞬间 GPIO15 必须为低，否则进不了正常启动。屏的 CS 是高阻输入，靠模组自己的
下拉就能保证低电平，正常情况直接接没问题。

如果出现上电不启动（74880 波特率下看到 `boot mode:(1,x)`），把屏的 **CS 直接接
GND** 让它常选中——驱动里 `cs(0)/cs(1)` 翻的是一个空脚，显示完全不受影响。

### 1.3 寸屏的芯片要选对（`PANEL`）

1.3" 128×64 模块的驱动芯片有 **SSD1306** 和 **SH1106** 两种，卖家经常混标成
「SSD1106/1306」。两者初始化命令完全不同：

- 选错芯片 → 全黑，或者画面整体偏 2 像素、左边多一条亮线
- SSD1306 靠 `0x8D` 开电荷泵，SH1106 靠 `0xAD`；SH1106 的显存是 132 列，比可见
  区域多 4 列，所以要用 `COL_OFFSET = 2` 把画面推回来

板子到手先跑一次地址扫描，扫到 `0x3C` 说明线和电都对，剩下只是芯片型号问题：

```python
from machine import I2C, Pin
I2C(scl=Pin(5), sda=Pin(4), freq=400000).scan()   # 期望 [60]（0x3C）
```

`scan()` 空列表 = 线或电的问题，不是芯片问题。

## 烧录

固件用 GitHub Actions 云端编译（`app`/`web`/`ssd1306` 已冻进固件，只剩约 1.5KB
常驻）。推代码上去 → Actions 跑完 → 下 Artifact。细节见 [FIRMWARE.md](FIRMWARE.md)。

```bash
# 1. 装固件：1MB 模组（TYWE3L/GD25Q80）用 1m，4MB 模组用 4m
esptool.py --chip esp8266 --port COM4 erase_flash
esptool.py --chip esp8266 --port COM4 --baud 460800 \
  write_flash --flash_size=1MB --flash_mode=qio 0 micropython-esp8266-1m-frozen.bin

# 2. 只传主程序和配置（frozen 模式，不会再传 app/web/ssd1306.mpy）
python build.py upload COM4 frozen
```

`build.py` 会顺手删掉板上的旧文件（`geo.mpy` / `webserver.mpy` / `ui.css` 等）。

> 涂鸦模组出厂固件锁 flash，第一次务必先 `erase_flash`，不然写进去不启动。

### 自动下载接线（省掉手动短接 GPIO0）

裸模组 + CH340 时，把适配器两根控制线接上，`esptool` 就能全自动复位进下载模式：

```
CH340 DTR  →  模组 GPIO0
CH340 RST  →  模组 RST        （有的模块标 RTS）
```

**注意：接好之后 `mpremote` 就不能用了。** pyserial 打开串口时默认把 DTR、RTS
都置为有效（两根线都拉低），等于开串口瞬间芯片被按在复位 + 下载模式上，永远
进不了 raw REPL，报 `could not enter raw repl`。`build.py` 因此改成了自带的
raw REPL 实现，开串口前后显式把 DTR/RTS 拉高，**和这套接线可以共存**：

- 烧固件 → `esptool`（自己驱动 DTR/RTS）
- 传程序 → `python build.py upload COM12`（自带上传器）

唯一要记住的：**别再用 `mpremote`**，需要交互式 REPL 就用 `build.py` 里的 `Dev` 类。

> **改 `app.py` / `web.py` / `ssd1306.py` 要重新编固件**，重传 `.mpy` 没用——
> 固件里的 frozen 模块优先于文件系统。只有 `config.py` 是改完上传就生效的。

不用自制固件也行：烧官方 `ESP8266_GENERIC-1m-v1.29.0.bin`，然后
`python build.py upload COM4`（不带 frozen），走全部 .mpy，堆少约 14KB 但能跑。

## 用法

- **配网**：没有 `wifi.txt`、连不上、或长按重置过，开机直接开热点
  **`YD-Clock-SETUP`（开放，无密码）**（不再先拿占位 SSID 白等 30 秒超时）。
  手机连上后**一般会自动弹出配网页**——固件里做了 DNS 劫持，把任意域名都答成
  `192.168.4.1`，手机探测"有没有网"时就被接住。没弹就手动打开 `192.168.4.1`。
  填 SSID + 密码 → 保存 → 自动重启。
  iPhone 若提示"无互联网连接"，选**保持连接**，别让它切回移动数据。
- **已经在网里时**，用浏览器打开 OLED 上显示的 IP（同一局域网内）也是这个页面，
  改 WiFi 不用重开热点。
- **长按 FLASH 键 3 秒**：清掉 WiFi 和定位缓存，重启后直接进配网热点。
- **短按 FLASH**：临时显示/隐藏天气区。

### 屏幕上的字

| 位置 | 含义 |
|---|---|
| 第一行滚动 `2026-11-19 THU   ONLINE` | 本地日期、星期（英文缩写）、**当前**联网状态（掉线立刻变 `OFFLINE`）；定位没拿到时末尾多一段 `NOLOC` |
| 中间大字 `11:10:12` | 时:分:秒，**同一个字号**（3 倍），整串 116px 居中 |
| 天气第一行滚动 `TEMP 18.5C   SUNNY` | 温度、天气（`SUNNY`/`RAIN`/`CLOUDY`…） |
| 天气第二行滚动 `HUM 72%   FEEL 16.2C` | 相对湿度、体感温度 |
| 第三行 `192.168.4.1` | 配网热点的网关地址；显示 `HTTP FAIL` 表示 80 端口没绑上 |

三行都是**水平跑马灯**：文本首尾相接、匀速左移，屏幕只留 16 个字，所以长信息
（完整日期、`FEEL` 体感）靠滚动看全。速度在 `config.py` 的 `MARQUEE_PX` /
`MARQUEE_MS` 里调，默认 2px / 110ms ≈ 18 像素/秒。
点阵字库是 8x8 的英文字库，中文放不进去，所以星期用 `MON`~`SUN` 缩写。

`ONLINE` 看的是**当前链路**，不是"曾经连上过"：`w.isconnected()` 每秒查一次，
路由断电、手机热点关掉，顶栏下一帧就翻成 `OFFLINE`，时钟继续按 RTC 走，
后台每 10 秒重连一次，连回来自动变回 `ONLINE` 并补一次对时。

`NOLOC` 只代表"定位没拿到"，位置是**出口 IP 的归属地**（运营商登记的城市/镇），
不是 GPS，误差几十公里很正常。想看具体坐标，串口读 `geo.txt`，里面是 `纬度 经度`。
缓存内容只要不是一个纬度 + 一个经度，开机就会自动删掉重新定位，不会带着坏数据跑。

### 配网连不上怎么查

| 现象 | 原因 / 处理 |
|---|---|
| 搜不到 `YD-Clock-SETUP` | 板子还在试连旧 WiFi（`wifi.txt` 存在）。长按 FLASH 3 秒重置 |
| 连上了但打不开页面 | 手机切回移动数据了。关掉"WLAN 助理/自动切换"，再开浏览器输 `192.168.4.1` |
| 热点在、连上没 IP | AP 的 `ifconfig` 没设成功。屏幕第三行显示 `192.168.4.1` 才正常，显示 `HTTP FAIL` 是 80 端口没绑上 |
| 屏幕全黑 | 4 针 I2C 屏查 SCL/SDA 有没有接反（`scan()` 空列表就是线或电的问题）；7 针 SPI 屏查 VCC 是不是接了 5V |
| 提示密码错误 / 死活连不上热点 | 热点默认**开放无密码**。手机对连过的同名 SSID 会存一份"加密方式+密码"档案，档案和实际不符就握手失败——所以热点改名叫 `YD-Clock-SETUP`，且在手机里**忘记旧网络 `YD-ESP-SETUP`** |
| 配置了 `AP_PASS` 后连不上 | ESP8266 的 softAP 密码读不回来（`config("password")` 报 `unknown config param`），没法自查。改用开放热点最省事；非要加密就填 8 位以上，并用 `AUTH_WPA2_PSK`（混合模式的兼容性更差） |
| 保存后又回到配网页 | 看屏幕上 `WIFI FAIL st<n>` 后面的数字：**st3 密码错**、**st4 找不到这个热点**（路由器是 5GHz，或 SSID 带隐藏网络）、**st5 连接被拒/超时**（多半是信号弱或 MAC 过滤）。ESP8266 只支持 2.4GHz |
| 保存后重启，`wifi.txt` 却是 0 字节 | 旧版 `save()` 的 bug：`open(...).write(...)` 没 close，littlefs 的长度没提交就 `reset()` 了。现在改成显式 close 并回读校验，存不进去页面会直接报"保存失败" |

## 改设置

全在 `config.py`：`HOUR24`、`SHOW_SEC`（0 = 只显示时:分，冒号闪烁）、`SHOW_WX`、`ROTATE`、`OLED_CONTRAST`、
`MARQUEE_PX/MARQUEE_MS`（跑马灯速度）、`UI_MS`（刷新率）、
`NIGHT_A/B`、`BURNIN_SHIFT_SEC`、`WIFI_SSID/WIFI_PASS`（出厂值）、`LAT/LON`（兜底坐标）。

改完 `python build.py upload`。

## 文件

```
main.py      入口（3 行源码，pyexec_file 只认源码，不能塞 .mpy）
app.py       主程序：绘制 / NTP / 定位 / 天气 / 主循环      → 冻进固件
web.py       配网服务（非阻塞，一次 poll 一条请求）          → 冻进固件
ssd1306.py   屏幕驱动，SSD1306/SH1106 双芯片 + I2C/SPI 双总线   → 冻进固件
config.py    全部配置                                        → config.mpy（可改可重传）
setup.html   配网页面（放 flash，用时才读，约 1.3KB）
build.py     编译 + 上传（加 frozen 参数走冻结固件模式）
frozen_manifest.py                    冻结清单
.github/workflows/build-firmware.yml  云端编译固件
FIRMWARE.md  固件编译说明 + 踩坑记录
.bak-v2/     上一版完整程序（城市表 + 设置网页）
```

## 已知限制

- 只用明文 HTTP：TLS 握手要 20KB+ 临时内存，ESP8266 上一握手就 OOM。
  所以天气走 `wttr.in`（几十字节纯文本），定位走 `ip-api.com/line/`（三行纯文本）。
- 1MB flash 的文件系统只有 500KB 左右，够放这些文件，别往里塞大文件。
