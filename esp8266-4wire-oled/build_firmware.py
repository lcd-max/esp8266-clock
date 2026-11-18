# 编译带 freeze 的 ESP8266 固件
#
# 用法：
#   python build_firmware.py            # 1MB flash 版（TYWE3L 用这个）
#   python build_firmware.py 4m         # 4MB 版
#
# 依赖：先跑 setup_build_env.py 摆好目录；本机已有 mpy-cross v1.29.0
import os, sys, shutil, subprocess, glob

ROOT = r"G:/esp-build"
MPY_CROSS = os.path.join(
    os.environ["APPDATA"],
    r"Python\Python313\site-packages\mpy_cross\mpy-cross.exe")


def find_mp():
    for d in os.listdir(ROOT):
        p = os.path.join(ROOT, d)
        if d.startswith("micropython-") and os.path.isdir(os.path.join(p, "ports")):
            return p
    raise SystemExit("没有找到 MicroPython 源码，先跑 setup_build_env.py")


def main():
    variant = (sys.argv[1] if len(sys.argv) > 1 else "1m").lower()
    mp = find_mp()
    tool_bin = os.path.join(ROOT, "toolchain", "xtensa-lx106-elf", "bin")
    sdk = os.path.join(ROOT, "sdk")
    manifest = os.path.join(ROOT, "frozen", "frozen_manifest.py")

    for p in (tool_bin, sdk, manifest):
        if not os.path.exists(p):
            raise SystemExit("缺 %s，先跑 setup_build_env.py" % p)

    # 把现成的 mpy-cross 塞进源码树：make 会认为已是最新，跳过编译（本机没有 gcc）
    mpyc_dir = os.path.join(mp, "mpy-cross", "build")
    os.makedirs(mpyc_dir, exist_ok=True)
    dst = os.path.join(mpyc_dir, "mpy-cross.exe")
    if not os.path.exists(dst):
        shutil.copy2(MPY_CROSS, dst)
        print("复用 mpy-cross:", dst)

    # 注意：Windows 的 CreateProcess 查找可执行文件用的是"当前进程"的 PATH，
    # 不认 subprocess 传进去的 env["PATH"]。所以必须改 os.environ 本身。
    os.environ["PATH"] = tool_bin + os.pathsep + os.environ.get("PATH", "")
    # make（MSYS 版）会把 PATH 转成 /g/... 这种 POSIX 格式传给子进程，
    # 原生 Windows 的 Python 认不出来，所以额外用这个变量把路径带下去。
    os.environ["MP_TOOLCHAIN_BIN"] = tool_bin
    # MSYS 的 TMPDIR 是 /tmp，原生 Windows 版 gcc 会写不了临时文件，
    # 报 "when writing output to : Invalid argument"。换成真正的 Windows 路径。
    tmp = os.environ.get("TEMP") or r"C:\Windows\Temp"
    for k in ("TMPDIR", "TMP", "TEMP"):
        os.environ[k] = tmp
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["QSTR_CPUS"] = "1"   # 多线程 Pool 在 Windows 上偶发句柄冲突
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        os.environ.pop(k, None)
    env = dict(os.environ)

    cmd = ["make", "-C", os.path.join(mp, "ports", "esp8266").replace("\\", "/"),
           "BOARD=ESP8266_GENERIC",
           "FROZEN_MANIFEST=" + manifest.replace("\\", "/"),
           "ESP_SDK=" + sdk.replace("\\", "/"),
           "PYTHON=python"]
    if variant in ("1m", "flashi"):
        cmd.append("BOARD_VARIANT=FLASH_1M")
        tag = "1m"
    elif variant in ("2m",):
        cmd.append("BOARD_VARIANT=FLASH_2M_ROMFS")
        tag = "2m"
    else:
        tag = "4m"
    cmd.extend(sys.argv[2:])          # 允许透传 V=1 之类的 make 参数

    print("编译 %s ..." % tag)
    print(" ".join(cmd))
    rc = subprocess.run(cmd, env=env).returncode
    print("make 退出码", rc)

    build = os.path.join(mp, "ports", "esp8266", "build-ESP8266_GENERIC" +
                         ("" if tag == "4m" else "-FLASH_" + tag.upper().replace("M", "M")))
    bins = glob.glob(os.path.join(mp, "ports", "esp8266", "build-*", "firmware.bin"))
    for b in bins:
        print("固件: %s  %.0f KB" % (b, os.path.getsize(b) / 1024))
    return rc


if __name__ == "__main__":
    sys.exit(main())
