# -*- coding: utf-8 -*-
"""
SSD1306 / SH1106 128x64 OLED 驱动（精简版）
==========================================
两条总线都支持：
  SSD1306_SPI  —— 7 针 SPI 屏（SCK/MOSI/RES/DC/CS）
  SSD1306_I2C  —— 4 针 I2C 屏（SCL/SDA，最省脚、接线最简单）

I2C 版有个必须处理的坑：1.3 寸模块的芯片有 SSD1306 和 SH1106 两种，
卖家用「SSD1106/1306」混着标。两者初始化命令不同，SH1106 的显存还是
132 列（左右各多 2 列），用 SSD1306 的参数会整体偏 2 像素或直接不亮。
所以这里两种都支持，由 config.py 的 PANEL 选。

显示部分统一用**页寻址**命令（0xB0+n / 0x00 低列 / 0x10 高列），这套命令
两个芯片都认，一套代码通吃，也顺便让 SH1106 的 2 列偏移能用一个参数补。

平移（set_shift）不再另开一份 1KB 的"平移后显存"：
  * 横向 dx：靠列地址起始偏移 + 行尾补黑；
  * 纵向 dy：靠页地址起始偏移（只支持 0 或 8，即整页）。
峰值内存少 1KB —— 1MB 模组堆只有 17KB 左右，这一 KB 很值。
"""
from micropython import const
import framebuf
import time

_SET_CONTRAST = const(0x81)
_SET_ENTIRE_ON = const(0xA4)
_SET_NORM_INV = const(0xA6)
_SET_DISP = const(0xAE)
_SET_MEM_ADDR = const(0x20)
_SET_COL_ADDR = const(0x21)
_SET_PAGE_ADDR = const(0x22)
_SET_DISP_START_LINE = const(0x40)
_SET_SEG_REMAP = const(0xA0)
_SET_MUX_RATIO = const(0xA8)
_SET_COM_OUT_DIR = const(0xC0)
_SET_DISP_OFFSET = const(0xD3)
_SET_COM_PIN_CFG = const(0xDA)
_SET_DISP_CLK_DIV = const(0xD5)
_SET_PRECHARGE = const(0xD9)
_SET_VCOM_DESEL = const(0xDB)
_SET_CHARGE_PUMP = const(0x8D)

# 页寻址用的老命令，SSD1306 和 SH1106 都认
_PAGE = const(0xB0)

# SSD1306：水平寻址关掉，改页寻址，开内部电荷泵（0x8D,0x14）
_INIT_1306 = (0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40,
              _SET_MEM_ADDR, 0x02,
              0xA1, 0xC8, _SET_COM_PIN_CFG, 0x12, _SET_CONTRAST, 0xCF,
              0xD9, 0xF1, _SET_VCOM_DESEL, 0x40, _SET_ENTIRE_ON,
              _SET_NORM_INV, _SET_CHARGE_PUMP, 0x14, _SET_DISP | 0x01)
# SH1106：没有 0x20（寻址模式）命令，9V 泵用 0xAD，预充电/ VCOM 参数也不同
_INIT_1106 = (0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40,
              0xAD, 0x8B,
              0xA1, 0xC8, _SET_COM_PIN_CFG, 0x12, _SET_CONTRAST, 0xCF,
              0xD9, 0x22, _SET_VCOM_DESEL, 0x35, _SET_ENTIRE_ON,
              _SET_NORM_INV, _SET_DISP | 0x01)


class SSD1306_SPI(framebuf.FrameBuffer):
    def __init__(self, width, height, spi, dc, res, cs):
        self.width = width
        self.height = height
        self.pages = height // 8
        self.spi = spi
        self.dc = dc
        self.cs = cs
        self.shift = 0
        self.buffer = bytearray(self.pages * width)
        super().__init__(self.buffer, width, height, framebuf.MONO_VLSB)

        res(1)
        time.sleep_ms(10)
        res(0)
        time.sleep_ms(20)
        res(1)
        for cmd in (_SET_DISP, _SET_MEM_ADDR, 0x00, _SET_DISP_START_LINE,
                    _SET_SEG_REMAP | 0x01, _SET_MUX_RATIO, height - 1,
                    _SET_COM_OUT_DIR | 0x08, _SET_DISP_OFFSET, 0x00,
                    _SET_COM_PIN_CFG, 0x02 if width > 2 * height else 0x12,
                    _SET_DISP_CLK_DIV, 0x80, _SET_PRECHARGE, 0xF1,
                    _SET_VCOM_DESEL, 0x30, _SET_CONTRAST, 0xFF,
                    _SET_ENTIRE_ON, _SET_NORM_INV,
                    _SET_CHARGE_PUMP, 0x14, _SET_DISP | 0x01):
            self._cmd(cmd)
        self.fill(0)
        self.show()

    def _cmd(self, cmd):
        self.dc(0)
        self.cs(0)
        self.spi.write(bytearray([cmd]))
        self.cs(1)

    def _data(self, buf):
        self.dc(1)
        self.cs(0)
        self.spi.write(buf)
        self.cs(1)

    def contrast(self, c):
        self._cmd(_SET_CONTRAST)
        self._cmd(c)

    def rotate(self, r):
        self._cmd(_SET_COM_OUT_DIR | ((r & 1) << 3))
        self._cmd(_SET_SEG_REMAP | (r & 1))

    def set_shift(self, dx):
        """抗烧屏的横向平移。纵向不在这里做 —— 早先按整页（8 像素）下移，
        屏幕最下面一行会被推出可视区，看起来就是"字符偏下、末行显示不全"。
        纵向改由 app.draw() 在绘制时偏移 1~2 像素，不影响任何一行。"""
        self.shift = dx & 3

    def show(self):
        dx = self.shift
        w = self.width
        for p in range(self.pages):
            src = p * w
            self._cmd(_SET_PAGE_ADDR)
            self._cmd(p)
            self._cmd(p)
            self._cmd(_SET_COL_ADDR)
            self._cmd(dx)
            self._cmd(w - 1)
            self._data(self.buffer[src:src + w - dx])
            if dx:
                self._data(b"\x00" * dx)


class SSD1306_I2C(framebuf.FrameBuffer):
    """4 针 I2C 屏。addr 一般 0x3C，少数模块是 0x3D，这里自动试一遍。"""

    def __init__(self, width, height, i2c, addr=0x3C,
                 panel="ssd1306", coloff=0):
        self.width = width
        self.height = height
        self.pages = height // 8
        self.i2c = i2c
        self.coloff = coloff
        self.shift = 0
        self.buffer = bytearray(self.pages * width)
        super().__init__(self.buffer, width, height, framebuf.MONO_VLSB)

        # 地址不对时 writeto 会抛 ENODEV，试一下另一个常见地址，
        # 省得用户为了 0x3C/0x3D 这个差别去改代码
        self.addr = addr
        for a in (addr, 0x3D if addr == 0x3C else 0x3C):
            try:
                i2c.writeto(a, b"\x00")
                self.addr = a
                break
            except OSError:
                pass

        for c in (_INIT_1106 if panel == "sh1106" else _INIT_1306):
            self._cmd(c)
        self.fill(0)
        self.show()

    def _cmd(self, c):
        self.i2c.writeto(self.addr, b"\x00" + bytes((c,)))

    def _data(self, b):
        self.i2c.writeto(self.addr, b"\x40" + b)

    def contrast(self, c):
        self._cmd(_SET_CONTRAST)
        self._cmd(c)

    def rotate(self, r):
        self._cmd(_SET_COM_OUT_DIR | ((r & 1) << 3))
        self._cmd(_SET_SEG_REMAP | (r & 1))

    def set_shift(self, dx):
        """同 SPI 版：只做横向平移，纵向交给绘制时的 y 偏移。"""
        self.shift = dx & 3

    def show(self):
        dx = self.shift
        w = self.width
        col = self.coloff + dx
        lo = col & 0x0F
        hi = 0x10 | (col >> 4)
        for p in range(self.pages):
            self._cmd(_PAGE | p)
            self._cmd(lo)
            self._cmd(hi)
            src = p * w
            self._data(self.buffer[src:src + w - dx])
            if dx:
                self._data(b"\x00" * dx)
