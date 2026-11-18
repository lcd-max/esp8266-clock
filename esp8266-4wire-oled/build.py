# -*- coding: utf-8 -*-
"""
编译 / 上传脚本（在电脑上跑，不是板子上）
=========================================
    python build.py                     # 把 .py 编译成 mpy/*.mpy
    python build.py upload              # 编译 + 上传到板子（默认 COM4）
    python build.py upload COM12        # 指定串口
    python build.py upload COM12 frozen # 烧了自制固件后：只传 main/config/setup.html

需要 mpy-cross（`pip install mpy-cross`）和 pyserial（`pip install pyserial`）。
main.py 保持源码上传（MicroPython 启动时直接编译它），其余都走 .mpy。

为什么不用 mpremote
-------------------
CH340 的 DTR/RTS 一旦接到模组的 GPIO0 / RST（自动下载电路），mpremote 就
用不了了：pyserial 打开串口时默认把 DTR、RTS 都置为有效（两根线都拉低），
等于开串口的一瞬间芯片被按在复位状态 + 下载模式上，永远进不了 raw REPL，
报错 `could not enter raw repl`。

所以这里直接用 pyserial，打开前后都显式把 DTR/RTS 拉高（无效），芯片才
会正常跑程序。好处是这套接线可以永久留着，烧固件（esptool）和传程序
（本脚本）都不需要再拔线。

frozen 模式配合 .github/workflows/build-firmware.yml 编出来的固件用：
app / web / ssd1306 已经在固件的只读段里，再传一份 .mpy 只是白占文件系统
（1MB 板只有约 400KB 可用），而且容易让人以为改源码就生效——改了这三个
文件必须重新编固件，.mpy 覆盖不了 frozen 模块。

内存参考：esp8266-4m 固件堆约 36KB，esp8266-1m 固件只有约 22KB。
这套精简版常驻约 8KB（frozen 版约 1.5KB），两种都跑得动。
"""
import os
import subprocess
import sys
import time

MODULES = ("config", "ssd1306", "web", "app")
# 这三个在自制固件里是 frozen 的，frozen 模式下不编译也不上传
FROZEN = ("app", "web", "ssd1306")
ASSETS = ("main.py", "setup.html")
OLD = ("geo.mpy", "webserver.mpy", "ui.css", "mainmod.mpy", "cities.mpy",
       "settings.mpy", "ui1.js", "ui2.js", "page.html", "wifi.json", "geo.json")
OUT = "mpy"
CHUNK = 512          # 每次写入板子的字节数（hex 后 1024 字符，一行放得下）


# ------------------------------------------------------------------ 编译
def build(mods=MODULES):
    os.makedirs(OUT, exist_ok=True)
    for m in mods:
        src = m + ".py"
        dst = os.path.join(OUT, m + ".mpy")
        subprocess.run([sys.executable, "-m", "mpy_cross",
                        "-march=xtensawin", "-o", dst, src], check=True)
        print("  %-16s %6d bytes" % (dst, os.path.getsize(dst)))
    total = sum(os.path.getsize(os.path.join(OUT, m + ".mpy")) for m in mods)
    print("mpy 合计 %d 字节（1MB 固件堆约 22000，4MB 约 36000）" % total)


# ------------------------------------------------------- 串口 / raw REPL
class Dev:
    """最小 raw REPL 客户端。

    关键点：DTR/RTS 全程保持 False（= 引脚高电平）。接自动下载电路后，
    这两根线分别连着 GPIO0 和 RST，只要有一个为有效就会把芯片按住。
    """

    def __init__(self, port, baud=115200):
        import serial
        s = serial.Serial()
        s.port = port
        s.baudrate = baud
        s.timeout = 0.2
        s.dtr = False            # 打开前先设好，避免 win32 在 open 时置为 True
        s.rts = False
        s.open()
        s.dtr = False            # 再设一次，确保引脚是高电平
        s.rts = False
        self.s = s

    def close(self):
        try:
            self.s.dtr = False
            self.s.rts = False
            self.s.close()
        except Exception:
            pass

    # ---- 底层收发
    def _drain(self, sec=0.3):
        t = time.time()
        buf = b""
        while time.time() - t < sec:
            buf += self.s.read(self.s.in_waiting or 1)
        return buf

    def raw(self, code, timeout=10):
        """在 raw REPL 里执行一段代码，返回 (stdout, stderr)。"""
        self.s.write(code.encode("utf-8") + b"\x04")
        buf = b""
        t = time.time()
        while time.time() - t < timeout:
            buf += self.s.read(self.s.in_waiting or 1)
            if buf.endswith(b"\x04>"):
                break
        out, err = b"", b""
        if buf.startswith(b"OK"):
            rest = buf[2:]
        else:
            rest = buf
        if b"\x04" in rest:
            out, _, err = rest.partition(b"\x04")
            err = err[:-2] if err.endswith(b"\x04>") else err
        else:
            err = rest
        if err.strip():
            raise RuntimeError("板上报错：%s" % err.decode("utf-8", "replace"))
        return out, err

    def enter(self):
        self.s.write(b"\r\x03\x03")          # Ctrl-C：打断任何在跑的程序
        self._drain(0.4)
        self.s.write(b"\x01")                # Ctrl-A：进 raw REPL
        t = time.time()
        buf = b""
        while time.time() - t < 5:
            buf += self.s.read(self.s.in_waiting or 1)
            if buf.endswith(b"raw REPL; CTRL-B to exit\r\n>"):
                break
        if b"raw REPL" not in buf:
            raise RuntimeError("进不了 raw REPL（芯片是不是被 DTR/RTS 按住？）")
        self.raw("import binascii")

    def exit(self):
        self._drain(0.2)
        self.s.write(b"\x02")                # Ctrl-B：退回普通 REPL
        self._drain(0.2)

    def reset(self):
        """用 RTS 拉一下 RST，让芯片干净启动（DTR 保持无效 = GPIO0 高）。"""
        self.exit()
        self.s.dtr = False
        self.s.rts = True                    # RST 低
        time.sleep(0.1)
        self.s.rts = False                   # RST 放开
        time.sleep(0.5)

    # ---- 文件操作
    def rm(self, name):
        try:
            self.raw("import os\ntry:\n os.remove(%r)\nexcept OSError:\n pass" % name)
        except Exception:
            pass

    def put(self, local, remote):
        data = open(local, "rb").read()
        self.raw("f=open(%r,'wb')" % remote)
        for i in range(0, len(data), CHUNK):
            self.raw('f.write(binascii.unhexlify("%s"))'
                     % data[i:i + CHUNK].hex())
        self.raw("f.close()")
        return len(data)


# ------------------------------------------------------------------ 上传
def upload(port, frozen=False):
    mods = [m for m in MODULES if m not in FROZEN] if frozen else list(MODULES)
    d = Dev(port)
    try:
        d.enter()
        print("已连上 %s" % port)

        for m in OLD:
            d.rm(m)
        if frozen:
            # 清掉以前传上去的 .mpy，免得占空间（反正也覆盖不了固件里的）
            for m in FROZEN:
                d.rm(m + ".mpy")

        # 先传模块和静态文件，main.py 放最后：万一中途断线，不会留下一个
        # import 不到的入口让板子一起来就崩
        for m in mods:
            n = d.put(os.path.join(OUT, m + ".mpy"), m + ".mpy")
            print("  -> %-14s %6d bytes" % (m + ".mpy", n))
        for a in ASSETS:
            if a == "main.py":
                continue
            n = d.put(a, a)
            print("  -> %-14s %6d bytes" % (a, n))
        for a in ASSETS:
            if a != "main.py":
                continue
            n = d.put(a, a)
            print("  -> %-14s %6d bytes" % (a, n))

        out, _ = d.raw("import os\nprint(sorted(os.listdir()))")
        print("板上文件：" + out.decode("utf-8", "replace").strip())
        d.reset()
    finally:
        d.close()

    if frozen:
        print("上传完成（frozen 模式：app/web/ssd1306 走固件自带），已复位")
        print("注意：改 app.py / web.py / ssd1306.py 需要重新编固件，不是重传")
    else:
        print("上传完成，已复位板子")


if __name__ == "__main__":
    args = sys.argv[1:]
    frozen = "frozen" in args
    args = [a for a in args if a != "frozen"]
    build([m for m in MODULES if m not in FROZEN] if frozen else MODULES)
    if args and args[0] == "upload":
        upload(args[1] if len(args) > 1 else "COM4", frozen=frozen)
