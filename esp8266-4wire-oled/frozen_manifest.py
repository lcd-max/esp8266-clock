# 冻进固件的模块清单（MicroPython 的 freeze 机制）
#
# 用法（在 MicroPython 源码根目录执行）：
#   make -C ports/esp8266 BOARD=ESP8266_GENERIC BOARD_VARIANT=FLASH_1M \
#        FROZEN_MANIFEST=<本文件路径>
#
# 原理：这些 .py 在编译固件时被 mpy-cross 编成字节码，直接写进 flash 的
# 只读段；开机 import 时从 flash 读，不再占用 RAM 堆。实测同样 3.4KB 的
# 模块：放 .mpy 文件里占 1.7KB 堆，冻进固件只占 0.4KB（就一个模块字典）。
#
# 官方 boards/manifest.py 会 require() dht / ds18x20 / neopixel / onewire /
# bundle-networking，这个时钟一个都用不上，1MB flash 里就不塞它们了。
# 但 $(PORT_DIR)/modules 必须留：_boot.py（启动流程）、inisetup.py、
# webrepl.py、ntptime.py 都在那儿。

freeze("$(PORT_DIR)/modules")

# freeze(目录, 脚本列表)：第一个参数必须是目录（模块名的基准），
# 文件名放在第二个参数里。写成 freeze("app.py") 会被当成目录打开并报错。
freeze(".", ("app.py", "web.py", "ssd1306.py"))

# config.py 故意不冻：亮度、翻转、经纬度、引脚这些以后可能要改，
# 留在文件系统里改完直接复位就行，不用重编固件。代价约 1.7KB。
