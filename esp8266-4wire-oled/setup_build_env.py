# 本地搭 ESP8266 编译环境：解压工具链 / SDK / MicroPython 源码，摆好目录
import os, sys, zipfile, tarfile, shutil

ROOT = r"G:/esp-build"
TOOLS = r"G:/YD-ESP32-S3资料/tools"
SRC = r"G:/YD-ESP32-S3资料/esp8266-clock"


def main():
    os.makedirs(ROOT, exist_ok=True)

    # ---- 1) 交叉编译器 xtensa-lx106-elf ----
    tc_dir = os.path.join(ROOT, "toolchain")
    gcc = os.path.join(tc_dir, "xtensa-lx106-elf", "bin", "xtensa-lx106-elf-gcc.exe")
    if not os.path.exists(gcc):
        print("[1/4] 解压工具链 ...", flush=True)
        if not os.path.exists(os.path.join(TOOLS, "toolchain.zip")):
            print("!! 缺 toolchain.zip"); return 1
        with zipfile.ZipFile(os.path.join(TOOLS, "toolchain.zip")) as z:
            z.extractall(tc_dir)
        print("      ->", gcc, os.path.exists(gcc))
    else:
        print("[1/4] 工具链已就绪")

    # ---- 2) ESP8266 NONOS SDK（取 Arduino core 里那份，libmain.a 带 -mforce-l32）----
    sdk = os.path.join(ROOT, "sdk")
    if not os.path.exists(os.path.join(sdk, "lib", "libmain.a")):
        print("[2/4] 从 Arduino core 提取 SDK ...", flush=True)
        zp = os.path.join(TOOLS, "arduino-2.7.4.zip")
        if not os.path.exists(zp):
            print("!! 缺 arduino-2.7.4.zip"); return 1
        with zipfile.ZipFile(zp) as z:
            names = z.namelist()
            probe = [n for n in names if n.endswith("tools/sdk/lib/libmain.a")]
            print("      libmain 命中:", probe[:1])
            if not probe:
                return 1
            prefix = probe[0][: probe[0].index("tools/sdk/")]
            os.makedirs(sdk, exist_ok=True)
            for n in names:
                if not n.startswith(prefix + "tools/sdk/") or n.endswith("/"):
                    continue
                rel = n[len(prefix + "tools/sdk/"):]
                dst = os.path.join(sdk, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with z.open(n) as f, open(dst, "wb") as o:
                    shutil.copyfileobj(f, o)
        print("      ->", os.path.exists(os.path.join(sdk, "lib", "libmain.a")))
    else:
        print("[2/4] SDK 已就绪")

    # ---- 3) MicroPython 源码 ----
    mp = None
    for d in os.listdir(ROOT):
        if d.startswith("micropython-") and os.path.isdir(os.path.join(ROOT, d)):
            mp = os.path.join(ROOT, d)
    if not mp:
        print("[3/4] 解压 MicroPython 源码 ...", flush=True)
        tp = os.path.join(TOOLS, "micropython-1.29.0.tar.gz")
        if not os.path.exists(tp):
            print("!! 缺 micropython-1.29.0.tar.gz"); return 1
        with tarfile.open(tp, "r:gz") as t:
            t.extractall(ROOT)
        for d in os.listdir(ROOT):
            if d.startswith("micropython-") and os.path.isdir(os.path.join(ROOT, d)):
                mp = os.path.join(ROOT, d)
    print("[3/4] 源码:", mp)

    # ---- 4) 把要冻的模块拷到独立目录 ----
    fz = os.path.join(ROOT, "frozen")
    os.makedirs(fz, exist_ok=True)
    for f in ("app.py", "web.py", "ssd1306.py", "config.py", "frozen_manifest.py"):
        shutil.copy2(os.path.join(SRC, f), os.path.join(fz, f))
    print("[4/4] 已拷:", sorted(os.listdir(fz)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
