# esp8266-clock

ESP8266 网络时钟，每个硬件方案一个目录。

## [esp8266-4wire-oled](esp8266-4wire-oled/) · 4 针 I2C OLED（当前方案）

ESP8266EX（1MB / 4MB flash 都能跑） + 0.96"/1.3" I2C OLED：

- **网页配网**：没有 `wifi.txt` 时开热点 `YD-Clock-SETUP`（开放、无密码），
  内置 DNS 劫持，手机连上自动弹配网页；长按 FLASH 3 秒清除配置重进配网
- **自动定位**：按出口 IP 取经纬度（不内置城市表），天气走 `wttr.in`，NTP 对时
- **屏幕**：SSD1306 / SH1106 双芯片、I2C 地址自动探测（0x3C / 0x3D）
- **上传**：`build.py` 自带 raw REPL 客户端，兼容 DTR/RTS 自动下载接线
- **抗烧屏**：定时像素偏移 + 夜间自动调低对比度

烧写、接线、配网排错见 [esp8266-4wire-oled/README.md](esp8266-4wire-oled/README.md)。

### 关于云端编译

`esp8266-4wire-oled/.github/workflows/build-firmware.yml` 是给 GitHub Actions
编「带 frozen 模块的 1MB 固件」用的。GitHub 只读仓库**根目录**的
`.github/workflows/`，要用的话把这个文件移到根目录，并把里面的工程路径
加上 `esp8266-4wire-oled/` 前缀。
