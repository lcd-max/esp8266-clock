# -*- coding: utf-8 -*-
"""配网服务（极限精简版）

只做一件事：GET / 给一张 WiFi 表单，POST /save 存凭据并重启。
不扫描、不列表、不返回状态 —— 扫描要 2 秒且每个 SSID 都要占内存，
而手机上本来就看得见自己的 WiFi 名。

非阻塞：accept 超时 0，读也非阻塞，一次 poll 最多服务一条请求，
所以网页开着时 OLED 的秒照走。
"""
import socket
import time
import os

CT = "text/html; charset=utf-8"

# 调试开关跟着 config.py 走（config.mpy 是单独传的，改完重传就生效）。
DBG = 0
try:
    import config as _cfg
    DBG = getattr(_cfg, "DEBUG", 0)
except Exception:
    pass


def log(s):
    """写一行调试日志到 dbg.txt。手机上白屏时，只能靠这个看请求到底走到哪一步。

    必须显式 close：MicroPython 不做引用计数，`open(p,"a").write(s)` 留下的
    文件对象要等 GC 才析构，这段时间里别的地方读它可能还是旧内容。
    """
    if not DBG:
        return
    try:
        if os.stat("dbg.txt")[6] > 3000:
            os.remove("dbg.txt")
    except Exception:
        pass
    f = None
    try:
        f = open("dbg.txt", "a")
        f.write(s + "\n")
    except Exception:
        pass
    finally:
        if f:
            try:
                f.close()
            except Exception:
                pass


def dec(b):
    """%XX / + -> 原始字符。"""
    o = bytearray()
    i = 0
    n = len(b)
    while i < n:
        c = b[i:i + 1]
        if c == b"+":
            o += b" "
            i += 1
        elif c == b"%" and i + 2 < n:
            try:
                o.append(int(b[i + 1:i + 3].decode(), 16))
                i += 3
            except Exception:
                o += c
                i += 1
        else:
            o += c
            i += 1
    try:
        return o.decode()
    except Exception:
        return ""


def form(b):
    d = {}
    for p in b.split(b"&"):
        i = p.find(b"=")
        if i > 0:
            d[dec(p[:i])] = dec(p[i + 1:])
    return d


def wsend(c, d, ms=1200):
    """把 d 一个字节不剩地写出去，返回 1/0。

    accept() 出来的 socket 是 settimeout(0) 的非阻塞态，write() 只是尽力塞进
    发送缓冲就返回，1310 字节的页面经常只出去一部分。紧接着 close()，没发出去
    的部分被 lwIP 直接丢掉 —— 手机端看到的就是永远转圈的白屏。必须循环重试到
    全部写完。
    """
    i = 0
    n = len(d)
    t0 = time.ticks_ms()
    while i < n:
        try:
            k = c.write(d[i:])
        except OSError:                    # EAGAIN：缓冲区满，5ms 后再试
            k = 0
        except Exception:
            return 0
        if k:
            i += k
            t0 = time.ticks_ms()
            continue
        if time.ticks_diff(time.ticks_ms(), t0) > ms:
            return 0                       # 对端一直不收，放弃
        time.sleep_ms(5)
    return 1


def send(c, b):
    d = ("HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
         "Cache-Control: no-store\r\nConnection: close\r\n\r\n"
         % (CT, len(b))).encode() + b
    ok = wsend(c, d)
    # 发完别立刻关：lwIP 里 close() 会 abort 连接，最后一段没等到 ACK 就没了，
    # 浏览器拿到的仍是半截响应。先收一下客户端的 FIN，再关。
    try:
        c.settimeout(0.3)
        c.read(64)
    except Exception:
        pass
    time.sleep_ms(60)
    try:
        c.close()
    except Exception:
        pass
    log("SND %d %d" % (len(d), ok))


def page(c):
    """页面放 flash，读一次发出去（约 900 字节，发完就释放）。"""
    try:
        b = open("setup.html", "rb").read()
    except Exception:
        b = b"<h3>setup.html missing</h3>"
    send(c, b)


def note(c, txt):
    send(c, ("<meta charset=utf-8><meta name=viewport content='width=device-width'>"
             "<body style='font:16px/1.6 -apple-system,Arial;padding:30px 22px;"
             "background:#eef1f7;color:#19202e'><h3>%s</h3>"
             "<p style='color:#5b6472;font-size:14px'>设备正在重启，稍等几秒，"
             "OLED 会显示时间和天气。</p>" % txt).encode())


class Portal:
    def __init__(self, on_save, ip=None):
        self.cb = on_save
        self.s = None
        self.q = []
        self.reboot = False
        self.ip = ip            # 非 None = AP 模式，顺便起 DNS 劫持
        self.d = None

    def start(self, port=80):
        if self.ip:
            self.dns()
        try:
            s = socket.socket()
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port))
            s.listen(4)
            s.settimeout(0)
            self.s = s
            return 1
        except Exception:
            self.s = None
            return 0

    # ---------------------------------------------------------- DNS 劫持
    # AP 模式下把任意域名的 A 记录都答成 192.168.4.1，手机连上热点后
    # 探测「有没有网」会被这里接住，自动弹出配网页。没有它就得靠用户
    # 自己记住并手输 192.168.4.1，而大多数手机此时还会因为「无互联网」
    # 自动切回移动数据，页面就更打不开了。
    def dns(self):
        try:
            d = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            d.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            d.bind(("0.0.0.0", 53))
            d.settimeout(0)
            self.d = d
        except Exception:
            self.d = None

    def dp(self):
        try:
            p, a = self.d.recvfrom(512)
        except Exception:
            return
        n = len(p)
        i = 12
        while i < n and p[i]:
            i += p[i] + 1
        if i + 5 > n:
            return
        q = p[12:i + 5]                    # QNAME + QTYPE + QCLASS，原样回写
        ip = bytes([int(x) for x in self.ip.split(".")])
        r = (p[:2] + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00" + q +
             b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04" + ip)
        try:
            self.d.sendto(r, a)
        except Exception:
            pass

    def poll(self):
        if self.d:
            self.dp()
        if not self.s:
            return
        self.pump()
        try:
            c, _ = self.s.accept()
        except Exception:
            c = None
        if c:
            try:
                c.settimeout(0)
            except Exception:
                pass
            if len(self.q) >= 6:
                self.drop(0)
            self.q.append([c, time.ticks_ms(), b""])
            log("ACC %d" % len(self.q))
        self.pump()

    def drop(self, i):
        try:
            self.q[i][0].close()
        except Exception:
            pass
        try:
            self.q.pop(i)
        except Exception:
            pass

    def pump(self):
        i = 0
        while i < len(self.q):
            c, t0, d = self.q[i]
            try:
                x = c.read(512)
            except OSError:
                x = None
            except Exception:
                x = b""
            if x is None:                      # EAGAIN：还没数据，下一轮再看
                # 浏览器会先开几个空闲预连接占坑，只发 TCP 不发数据。
                # 这种连接留 800ms 就踹掉，否则真正的 GET 排不上队。
                # 注意 pump 每秒只跑 40 次左右，别把窗口设得太贴 —— 手机
                # 从完成 TCP 握手到发出 GET 有时要 300~500ms。
                if time.ticks_diff(time.ticks_ms(), t0) > 1500:
                    self.drop(i)
                    continue
                i += 1
                continue
            d += x
            self.q[i][2] = d
            if b"\r\n\r\n" in d and (x == b"" or self.ok(d)):
                self.q.pop(i)
                self.serve(c, d)
                return
            if time.ticks_diff(time.ticks_ms(), t0) > 5000:
                self.drop(i)
                continue
            i += 1

    def ok(self, d):
        """请求头 + body 都到齐了吗（GET 只需要头）。"""
        i = d.find(b"\r\n\r\n")
        n = 0
        for l in d[:i].split(b"\r\n"):
            if l[:15].lower() == b"content-length:":
                try:
                    n = int(l[15:])
                except Exception:
                    n = 0
        return len(d) - i - 4 >= n

    def serve(self, c, d):
        i = d.find(b"\r\n\r\n")
        head = d[:i]
        body = d[i + 4:]
        f = head.split(b" ", 2)
        if len(f) > 1:
            log("REQ " + f[0].decode() + " " + f[1].decode()[:24])
        else:
            log("REQ BAD " + str(len(d)))
        if len(f) > 1 and f[0] == b"POST" and f[1][:5] == b"/save":
            v = form(body)
            s = v.get("ssid", "")
            if s:
                r = 1
                if self.cb:
                    r = self.cb(s, v.get("pass", ""))
                if r == 0:                 # 存不进去就别重启，否则又空转一圈
                    note(c, "保存失败，请重试")
                    return
                note(c, "已保存：" + s)
                self.reboot = True
                return
        page(c)
