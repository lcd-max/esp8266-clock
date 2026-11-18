# -*- coding: utf-8 -*-
"""ESP8266 联网时钟 · 极限精简版

目标：1MB flash 的模组（esp8266-1m 固件，堆约 22KB）也能跑。
做到的取舍：没有城市表、没有扫描列表、没有设置页、没有多余的调试输出；
定位用纯文本接口（ip-api.com 三行），凭据用纯文本两行，连 ujson 都不 import。

屏幕：128x64 OLED。4 针 I2C（默认）或 7 针 SPI，由 config.py 的 IFACE 选。
"""
import time
import socket
import os
import machine
import network
from machine import Pin, SPI, I2C, RTC
import ssd1306
import config as cfg
import web

W, H = 128, 64
WK = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
EB = 946684800 if time.localtime(0)[0] == 2000 else 0

# 5x7 点阵，每个字形 7 字节，低 5 位是一行像素（最高位在左）。
# 存成 bytes 而不是字符串元组：后者要 15 个独立小对象，白吃 3KB 堆。
CH = "0123456789: -.C"
GL = (
    b"\x0e\x11\x13\x15\x19\x11\x0e",
    b"\x04\x0c\x04\x04\x04\x04\x0e",
    b"\x0e\x11\x01\x02\x04\x08\x1f",
    b"\x1f\x02\x04\x02\x01\x11\x0e",
    b"\x02\x06\x0a\x12\x1f\x02\x02",
    b"\x1f\x10\x1e\x01\x01\x11\x0e",
    b"\x06\x08\x10\x1e\x11\x11\x0e",
    b"\x1f\x01\x02\x04\x08\x08\x08",
    b"\x0e\x11\x11\x0e\x11\x11\x0e",
    b"\x0e\x11\x11\x0f\x01\x02\x0c",
    b"\x00\x03\x03\x00\x03\x03\x00",
    b"\x00\x00\x00\x00\x00\x00\x00",
    b"\x00\x00\x00\x1f\x00\x00\x00",
    b"\x00\x00\x00\x00\x00\x00\x03",
    b"\x0e\x11\x10\x10\x10\x11\x0e",
)

st = {"wx": None, "g": 0, "nw": 0, "nn": 0, "sw": cfg.SHOW_WX, "c": -1, "f": 0,
      "ok": 0, "wc": 0}
P = None
_o = None
_lu = 0


def _nop(*a, **k):
    pass


class _NoDisp:
    """屏没接、地址不对、初始化失败时的替身。

    所有方法都变成空操作，时钟和配网照常跑 —— 否则屏一没接，
    main() 在 init_oled() 就抛异常，连热点都开不出来，人就被卡死了。
    """

    def __getattr__(self, n):
        return _nop


# ------------------------------------------------------------------ 绘制
def big(s, x, y, sc=3, gp=2):
    """画点阵大字，返回结束的 x。"""
    cx = x
    for ch in s:
        i = CH.find(ch)
        if i < 0:
            i = 11
        g = GL[i]
        w = 2 if ch in ":. " else 5
        for r in range(7):
            b = g[r]
            for c in range(w):
                if b & (1 << (w - 1 - c)):
                    _o.fill_rect(cx + c * sc, y + r * sc, sc, sc, 1)
        cx += w * sc + gp
    return cx - gp


def bw(s, sc=3, gp=2):
    n = -gp
    for ch in s:
        n += (2 if ch in ":. " else 5) * sc + gp
    return n


def draw():
    tm = time.localtime(int(time.time()) + cfg.TZ_HOURS * 3600)
    hh = tm[3]
    sec = tm[5]
    o = _o
    o.fill(0)
    o.text("%02d-%02d %s" % (tm[1], tm[2], WK[tm[6]]), 2, 1, 1)
    if st["g"]:
        o.text("LOC", 114, 1, 1)
    o.hline(0, 10, W, 1)
    s = "%02d%s%02d" % (hh if cfg.HOUR24 else (hh % 12 or 12),
                        ":" if sec % 2 == 0 else " ", tm[4])
    if st["sw"]:
        big(s, 27, 13, 3)
        if not cfg.HOUR24:
            o.text("AM" if hh < 12 else "PM", 108, 20, 1)
        elif cfg.SHOW_SEC:
            o.text("%02d" % sec, 112, 20, 1)
        o.hline(0, 36, W, 1)
        w = st["wx"] or {}
        x = big(w.get("t", "--"), 2, 40, 2, 1)
        o.text("C", min(x + 2, 114), 40, 1)
        d = w.get("d", "--")
        o.text(d, W - len(d) * 8 - 2, 40, 1)
        o.text("RH%s F%s" % (w.get("h", "--"), w.get("f", "--")), 2, 54, 1)
    else:
        big(s, (W - bw(s, 3)) // 2, 22, 3)
        if not cfg.HOUR24:
            o.text("AM" if hh < 12 else "PM", 108, 30, 1)
        elif cfg.SHOW_SEC:
            o.text("%02d" % sec, 112, 30, 1)
    o.show()


def ui():
    """阻塞操作的空隙里补刷一次屏（最多 200ms 一次），秒不会卡住。

    没联网时不刷：这时 time.time() 还是 1970/2000 年，画出来是个假时钟，
    还会把「热点是哪个、IP 是多少」的提示盖掉 —— 屏幕上留着提示才有用。
    """
    global _lu
    if _o is None or not st["ok"]:
        return
    n = time.ticks_ms()
    if _lu and time.ticks_diff(n, _lu) < 200:
        return
    _lu = n
    try:
        draw()
    except Exception:
        pass


def msg(*ls):
    o = _o
    o.fill(0)
    y = 4
    for s in ls:
        o.text(str(s)[:15], 2, y, 1)
        y += 12
    o.show()


def theme():
    """夜间自动变暗，只在目标值变化时写一次对比度。"""
    c = cfg.OLED_CONTRAST
    if cfg.NIGHT_ON:
        h = time.localtime(int(time.time()) + cfg.TZ_HOURS * 3600)[3]
        if h >= cfg.NIGHT_A or h < cfg.NIGHT_B:
            c = cfg.NIGHT_CONTRAST
    if c != st["c"]:
        st["c"] = c
        try:
            _o.contrast(c)
        except Exception:
            pass


def init_oled():
    if cfg.IFACE == "i2c":
        # ESP8266 只有软件 I2C，引脚可以随便挑；这里用 GPIO4/5。
        # 好处是 SPI 那 5 根线全省了，屏只要 4 根线。
        bus = I2C(scl=Pin(cfg.SCL_PIN), sda=Pin(cfg.SDA_PIN), freq=cfg.I2C_FREQ)
        return ssd1306.SSD1306_I2C(W, H, bus, cfg.I2C_ADDR,
                                   cfg.PANEL, cfg.COL_OFFSET)
    res = Pin(cfg.RES_PIN, Pin.OUT, value=1)
    # ESP8266 只有一个硬件 SPI，脚位固定（14/13/12/15），不能传 sck/mosi
    spi = SPI(cfg.SPI_ID, baudrate=cfg.SPI_BAUDRATE, polarity=0, phase=0)
    return ssd1306.SSD1306_SPI(W, H, spi, Pin(cfg.DC_PIN, Pin.OUT), res,
                               Pin(cfg.CS_PIN, Pin.OUT))


# ------------------------------------------------------------------ 配置存取
def creds():
    """返回 (ssid, pw)。没存过就返回 (None, None) —— 直接进配网热点，
    不要拿 config.py 里的占位 SSID 去死等超时。"""
    try:
        a = open(cfg.WIFI_FILE).read().split("\n")
        if a[0].strip():
            return a[0].strip(), (a[1].strip() if len(a) > 1 else "")
    except Exception:
        pass
    if cfg.WIFI_SSID:                  # 想在固件里预置 WiFi，就填 config.py
        return cfg.WIFI_SSID, cfg.WIFI_PASS
    return None, None


def wfile(p, s):
    """写文本文件，**显式 close**，返回 1/0。

    千万别写 `open(p, "w").write(s)`：MicroPython 不做引用计数，那个文件对象
    要等 GC 才析构，而 littlefs 是 close() 时才把文件长度提交进元数据的。
    后面紧跟 machine.reset() 的话，长度还没落地，文件就是 0 字节 ——
    配网页回「已保存」、重启后又回到热点，就是被这个坑住的。
    """
    f = None
    try:
        f = open(p, "w")
        f.write(s)
        return 1
    except Exception:
        return 0
    finally:
        if f:
            try:
                f.close()
            except Exception:
                pass


def save(ssid, pw):
    """存凭据，返回 1/0。存不进去要能告诉用户，别闷着重启。

    写完必须回读确认：空文件比写失败更坏，因为它会让下一次启动误判成
    「没配过网」，用户看到的就是重启后重新回到配网热点，像什么都没发生。
    """
    if not wfile(cfg.WIFI_FILE, ssid + "\n" + pw):
        return 0
    try:
        if not open(cfg.WIFI_FILE).read().strip():
            return 0
    except Exception:
        return 0
    return 1


def reset():
    """长按 FLASH：清 WiFi + 定位缓存，重启后进配网热点。"""
    msg("RESET ...", "clear wifi")
    for f in (cfg.WIFI_FILE, cfg.GEO_FILE, cfg.SETUP_FLAG):
        try:
            os.remove(f)
        except Exception:
            pass
    time.sleep_ms(600)
    machine.reset()


# ------------------------------------------------------------------ 网络
def wcon(ssid, pw):
    w = network.WLAN(network.STA_IF)
    w.active(False)                    # 不关再开会扫不到网络
    time.sleep_ms(300)
    w.active(True)
    time.sleep_ms(700)
    msg("WIFI ...", str(ssid)[:14])
    for _ in range(cfg.WIFI_RETRY + 1):
        try:
            w.connect(ssid, pw)
        except Exception:
            pass
        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < cfg.WIFI_TIMEOUT * 1000:
            if w.isconnected():
                break
            time.sleep_ms(250)
        if w.isconnected():
            break
    try:
        w.config(pm=0)                 # 关省电：手机开网页不转圈
    except Exception:
        pass
    return w


def ap():
    """开配网热点。三个顺序坑，都是在这块板子上实测出来的：

    1. STA 必须先关。AP+STA 共存时 ESP8266 会把 AP 的 DHCP DNS 跟着 STA 走，
       手机连上热点后解析任何域名都发往一个它根本到不了的路由器——网页
       永远转圈。这是最难查的一种故障，因为「热点有了」会让人以为网络是好的。
    2. config() 只能在 AP 已激活时调用。SDK 的 wifi_softap_set_config 在
       AP 关闭状态下返回 false，这里会抛 OSError: can't set AP config，
       所以不能「先关再配」。
    3. 写完配置必须把 AP 关掉再打开一次。启动后才改的配置只落在 SDK 的
       配置结构里，射频侧还是旧的：beacon 广播的加密方式和内部握手状态
       不一致，手机表现就是「密码明明对，却一直连不上」。
    """
    try:
        network.WLAN(network.STA_IF).active(False)
    except Exception:
        pass
    time.sleep_ms(200)

    a = network.WLAN(network.AP_IF)
    try:
        a.active(True)
    except Exception:
        pass
    time.sleep_ms(200)

    pw = getattr(cfg, "AP_PASS", "") or ""
    try:
        if len(pw) >= 8:                # WPA2 最短 8 位，短了只能开开放热点
            a.config(essid=cfg.AP_SSID, password=pw,
                     authmode=network.AUTH_WPA2_PSK)
        else:
            a.config(essid=cfg.AP_SSID, authmode=network.AUTH_OPEN)
    except Exception:
        try:                           # 加密配不上就退成开放热点，至少能进去配
            a.config(essid=cfg.AP_SSID, authmode=network.AUTH_OPEN)
        except Exception:
            pass

    # 重启一次 AP，让 SSID / 加密方式真正生效（见上面第 3 条）
    try:
        a.active(False)
        time.sleep_ms(300)
        a.active(True)
        time.sleep_ms(300)
    except Exception:
        pass

    try:
        # DHCP 服务器跟着这个地址走，设完手机才会拿到 192.168.4.x
        a.ifconfig((cfg.AP_IP, "255.255.255.0", cfg.AP_IP, cfg.AP_IP))
    except Exception:
        pass
    return a


def hget(url, t=None):
    """极简 HTTP（只支持 http://，TLS 握手会 OOM），返回 (head, body)。"""
    t = t or cfg.HTTP_TIMEOUT
    r = url[7:]
    i = r.find("/")
    host = r[:i] if i >= 0 else r
    path = r[i:] if i >= 0 else "/"
    ui()
    a = socket.getaddrinfo(host, 80)[0][-1]
    s = socket.socket()
    s.settimeout(t)
    s.connect(a)
    s.write(("GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: clock\r\n"
             "Accept: */*\r\nConnection: close\r\n\r\n" % (path, host)).encode())
    s.settimeout(2)
    b = b""
    while len(b) < 6000:
        ui()
        try:
            d = s.read(768)
        except Exception:
            break
        if not d:
            break
        b += d
    try:
        s.close()
    except Exception:
        pass
    i = b.find(b"\r\n\r\n")
    if i < 0:
        raise OSError("bad http")
    hd = b[:i]
    bd = b[i + 4:]
    if b"chunked" in hd:
        bd = unch(bd)
    return hd, bd


def unch(d):
    o = b""
    i = 0
    while i < len(d):
        j = d.find(b"\r\n", i)
        if j < 0:
            break
        try:
            n = int(d[i:j].split(b";")[0].decode(), 16)
        except Exception:
            break
        if not n:
            break
        o += d[j + 2:j + 2 + n]
        i = j + 4 + n
    return o


def ntp():
    for h in cfg.NTP_HOSTS:
        try:
            a = socket.getaddrinfo(h, 123)[0][-1]
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(cfg.NTP_TIMEOUT)
            p = bytearray(48)
            p[0] = 0x1B                # LI=0 VN=3 Mode=3(client)
            s.sendto(p, a)
            r = s.recv(48)
            s.close()
            v = ((r[40] << 24) | (r[41] << 16) | (r[42] << 8) | r[43]) - 2208988800
            if v < 1600000000:
                continue
            tm = time.localtime(v - EB)
            RTC().datetime((tm[0], tm[1], tm[2], tm[6], tm[3], tm[4], tm[5], 0))
            return 1
        except Exception:
            pass
    return 0


def sync():
    ui()
    if P:
        P.poll()
    if ntp():
        st["nn"] = int(time.time()) + cfg.NTP_REFRESH
        st["f"] = 0
    else:
        st["f"] += 1                   # 失败退避，否则每分钟卡好几秒
        st["nn"] = int(time.time()) + (120 if st["f"] < 3 else 900)


# ------------------------------------------------------------------ 定位 / 天气
def isnum(s):
    """'30.4043' / '-3.5' → 1；'Yanguan' / '' → 0。
    不能直接 float()：1MB 固件里省一个函数是一次 PUSH/POP，而这里只是字符判断。"""
    if not s:
        return 0
    for c in s:
        if not ("0" <= c <= "9" or c == "-" or c == "."):
            return 0
    return 1


def geo():
    """出口 IP → 经纬度（ip-api 的 line 接口是一行一个字段的纯文本）。

    只认数字行，不认位置：接口按 fields 顺序返回，但历史版本存下来的缓存
    里出现过「城市名 纬度」这种顺序，之后开机就把它当纬度用了 —— 天气接口
    拼成 wttr.in/Yanguan,30.4043，永远取不到数据。按内容取最稳。
    """
    ui()
    try:
        hd, bd = hget("http://ip-api.com/line/?fields=lat,lon,city", 5)
        if b"200" not in hd[:20]:
            return
        v = []
        for l in bd.decode().split("\n"):
            l = l.strip()
            if isnum(l):
                v.append(l)
                if len(v) == 2:
                    break
        if len(v) < 2:
            return
        cfg.LAT = v[0]
        cfg.LON = v[1]
        st["g"] = 1
        wfile(cfg.GEO_FILE, cfg.LAT + " " + cfg.LON)
        st["nw"] = 0
    except Exception:
        pass


def geo_load():
    """读缓存。内容不是一个纬度 + 一个经度就直接删掉，让 geo() 重新定位 ——
    留着坏缓存比没有缓存更糟：屏幕会亮 LOC，天气却永远是 --。"""
    try:
        a = open(cfg.GEO_FILE).read().split()
        if len(a) > 1 and isnum(a[0]) and isnum(a[1]):
            cfg.LAT = a[0]
            cfg.LON = a[1]
            st["g"] = 1
            return
        os.remove(cfg.GEO_FILE)
    except Exception:
        pass


def nm(s):
    """'+18.5°C' / '72%' / '-3' → '18.5' / '72' / '-3'"""
    o = ""
    for c in s:
        if "0" <= c <= "9" or c in "-.":
            o += c
        elif o:
            break
    return o[:5] or "--"


def dsc(s):
    s = s.lower()
    if "rain" in s or "drizzle" in s or "shower" in s:
        return "RAIN"
    if "snow" in s or "ice" in s:
        return "SNOW"
    if "storm" in s or "thunder" in s:
        return "STORM"
    if "haze" in s or "fog" in s or "mist" in s or "smoke" in s:
        return "HAZE"
    if "sun" in s or "clear" in s:
        return "SUNNY"
    return "CLOUDY"


def wxf():
    ui()
    t = int(time.time())
    try:
        hd, bd = hget("http://wttr.in/" + cfg.LAT + "," + cfg.LON
                      + "?format=%t|%h|%C|%f")
        if b"200" not in hd[:20]:
            raise OSError("status")
        p = bd.decode().strip().split("|")
        if len(p) < 3:
            raise OSError("format")
        st["wx"] = {"t": nm(p[0]), "h": nm(p[1]), "d": dsc(p[2]),
                    "f": nm(p[3]) if len(p) > 3 else "--"}
        st["nw"] = t + cfg.WEATHER_REFRESH
        st["f"] = 0
        return
    except Exception:
        pass
    st["f"] += 1
    st["nw"] = t + (120 if st["f"] < 3 else 600)


# ------------------------------------------------------------------ 主流程
def main():
    global P, _o
    try:
        machine.freq(cfg.CPU_FREQ)
    except Exception:
        pass
    try:
        o = init_oled()
    except Exception:
        o = _NoDisp()                  # 屏没接也能开机、也能配网
    _o = o
    o.fill(1)                          # 全白自检：屏亮 = 线都通
    o.show()
    time.sleep_ms(1200)
    o.fill(0)
    o.show()
    if cfg.ROTATE:
        try:
            o.rotate(1)
        except Exception:
            pass
    theme()
    k = Pin(cfg.BOOT_PIN, Pin.IN, Pin.PULL_UP)
    msg("CLOCK v3.0 lite", "128x64 SSD1306")
    P = web.Portal(save)

    ssid, pw = creds()
    w = network.WLAN(network.STA_IF)

    if ssid:
        w = wcon(ssid, pw)
        if w.isconnected():
            st["ok"] = 1
            geo_load()
        else:
            # 连不上时把原因码留下来，直接显示在屏上：
            # 3 = 密码错  4 = 找不到这个热点  5 = 连接被拒/超时
            # 没有这个数字的话，「WIFI FAIL」三种原因长得一模一样，只能靠猜
            st["wc"] = w.status()

    # 一旦落到配网热点就完全不碰 STA：AP+STA 共存时 ESP8266 会把 AP 的 DHCP
    # DNS 跟着 STA 走（实测变成路由器的 10.15.94.14），手机连上解析不了
    # 任何域名，网页就一直转圈。宁可这次开机不重连，也别毁掉配网页。
    ap_only = 0 if st["ok"] else 1

    if st["ok"]:
        ok = P.start()
        msg("WIFI OK", w.ifconfig()[0], "" if ok else "HTTP FAIL")
        sync()
        geo()
        wxf()
    else:
        # 没凭据 / 连不上 / 长按重置，都落到配网热点
        ap()
        # 顺序要紧：先把热点起来、拿到 192.168.4.1，再 bind 80。
        # 反过来的话设备此刻一个活动接口都没有，bind(0.0.0.0) 绑不住，
        # self.s 变 None，poll 直接 return —— 热点正常、网页永远没反应。
        P.ip = cfg.AP_IP              # 打开 DNS 劫持，手机连上自动弹配网页
        ok = P.start()
        msg("WIFI SETUP" if not ssid else "WIFI FAIL st%d" % st["wc"],
            "AP " + cfg.AP_SSID, cfg.AP_IP if ok else "HTTP FAIL")

    lt = lth = lsh = time.ticks_ms()
    si = 0
    pr = 0
    wf = 0
    SH = ((0, 0), (1, 0), (2, 0), (0, 8))

    while 1:
        n = time.ticks_ms()

        # 长按 3 秒 = 重置；短按 = 切天气显示
        if k.value() == 0:
            if pr == 0:
                pr = n
            elif time.ticks_diff(n, pr) > cfg.LONG_PRESS_MS:
                reset()
        elif pr:
            if time.ticks_diff(n, pr) > 80:
                st["sw"] = 0 if st["sw"] else 1
            pr = 0

        # 每秒：网络维护（断网不阻塞，只是发起重连）
        if time.ticks_diff(n, lt) >= 1000 and not ap_only:
            lt = n
            if w.isconnected():
                if not st["ok"]:
                    # 配网后自动重连成功：从热点提示切回时钟
                    st["ok"] = 1
                    geo_load()
                    st["nn"] = 0
                    st["nw"] = 0
                    draw()
                e = int(time.time())
                if e >= st["nn"]:
                    sync()
                if e >= st["nw"]:
                    if not st["g"]:
                        geo()
                    wxf()
            else:
                wf += 1
                if wf % 10 == 1:
                    cs = creds()
                    if cs[0]:
                        try:
                            w.connect(cs[0], cs[1])
                        except Exception:
                            pass

        P.poll()
        P.poll()
        if P.reboot:
            time.sleep_ms(500)
            machine.reset()

        if time.ticks_diff(n, lth) >= 5000:
            lth = n
            theme()
        if not ap_only:
            ui()

        # 抗烧屏：定时整屏平移 1~2 像素
        if cfg.BURNIN_SHIFT_SEC and time.ticks_diff(n, lsh) >= cfg.BURNIN_SHIFT_SEC * 1000:
            lsh = n
            si = (si + 1) % 4
            try:
                o.set_shift(SH[si][0], SH[si][1])
            except Exception:
                pass

        time.sleep_ms(50)


if __name__ == "__main__":
    main()
