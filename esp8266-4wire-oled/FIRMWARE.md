# 编译带 freeze 的 ESP8266 固件

> 状态（2026-11-18）：**改用 GitHub Actions 云端编译**，见文末「云端编译」。
> 本机 Windows 路线能编出 550KB 固件但上电不启动（原因已定位，见「还没解决的问题」），
> 保留作为踩坑记录。

## 为什么要自己编

`freeze` 把 Python 模块在编译固件时编成字节码写进 flash 只读段，开机 import 时
不再占用 RAM 堆。实测对比（4MB 板，v1.29.0）：

| 模块 | 形式 | 堆占用 |
|---|---|---|
| `ujson` | C 内置 | 16 字节 |
| `framebuf` | C 内置 | 64 字节 |
| `webrepl` | **frozen**（源码 3.4KB） | 400 字节 |
| `config` | .mpy 文件（758B） | 1,760 字节 |
| `ssd1306` | .mpy 文件（989B） | 1,840 字节 |

按这个比例，把 `app`/`web`/`ssd1306`（合计 15.8KB 常驻）全部冻进固件，
只剩约 1.5KB，**能还回 14KB 堆**。

## 需要的四个包

| 包 | 来源 | 用途 | 大小 |
|---|---|---|---|
| `xtensa-lx106-elf-gcc` | `earlephilhower/esp-quick-toolchain` release `3.2.0-gcc10.3`，取 `x86_64-w64-mingw32.*.zip` | 交叉编译器（gcc 10.3） | 77MB |
| ESP8266 NONOS SDK 2.2.0 | `espressif/ESP8266_NONOS_SDK` tag `v2.2.0` 的源码包 | `libmain.a`/`libjson.a`/`liblwip.a` 和头文件 | 6.4MB |
| MicroPython 源码 | `micropython/micropython` tag `v1.29.0` | 固件源码 | 9.5MB |
| 子模块 | `micropython/axtls`、`micropython/berkeley-db-1.xx`、`micropython/micropython-lib` | 源码树缺它们 | 2MB |

GitHub Releases 直连只有 28KB/s，前面套 `https://gh-proxy.com/` 能到 400~700KB/s。

## 目录布局

```
G:/esp-build/
  toolchain/xtensa-lx106-elf/bin/   # 解压工具链
  sdk/                              # 解压 NONOS SDK 2.2.0
  frozen/                           # 待冻结的 app.py / web.py / ssd1306.py + manifest
  micropython-1.29.0/               # 源码 + lib/ 下的三个子模块
```

脚本：`setup_build_env.py` 摆目录，`build_firmware.py` 编译。

```
python setup_build_env.py
python build_firmware.py 4m "CFLAGS_EXTRA=-Wno-error -fcommon" MICROPY_PY_SSL=0 MICROPY_PY_BTREE=0 "ESPTOOL=python -m esptool"
```

## 踩过的坑（按顺序）

1. **Windows 找不到 gcc**：`subprocess` 用传进去的 `env["PATH"]` 也不生效——`CreateProcess`
   查可执行文件用的是**当前进程**的 PATH。必须改 `os.environ`。
2. **make 传给子进程的 PATH 是 MSYS 格式**（`/g/esp-build/...`），原生 Python 认不了。
   在 `py/makeqstrdefs.py` 顶部加 `MP_TOOLCHAIN_BIN` 环境变量把 Windows 路径补回去。
3. **`TMPDIR=/tmp` 让 gcc 报 `when writing output to : Invalid argument`**。
   要把 `TMPDIR/TMP/TEMP` 改成真正的 Windows 临时目录。
4. **qstr 生成的多线程 Pool 在 Windows 上偶发失败**，设成单线程（`QSTR_CPUS=1`）。
5. **本机没有 gcc，编不出 mpy-cross**：把 pip 装好的 `mpy-cross.exe`（v1.29.0，正好匹配）
   拷到 `mpy-cross/build/`，make 就跳过编译。
6. **Arduino core 的 SDK 不能直接用**：`lwip/arch/cc.h` 里有 `#include "../../../cores/esp8266/..."`，
   指向 Arduino 源码树。必须换原厂 SDK。
7. **原厂 SDK 的 lwip 头在 `third_party/include/` 下**，要合并到 `include/`。
8. **`c_types.h` 里 `typedef unsigned char bool`** 与 gcc 10 的 C99 冲突：
   改成 C99 下 `#include <stdbool.h>`（注意 esp-quick-toolchain 的 newlib 里有这个头，
   在 `lib/gcc/xtensa-lx106-elf/10.3.0/include/`）。
9. **gcc 10 默认 `-fno-common`**，`modmachine.h` 里的 `pyb_pin_obj` 变成重复定义。
   加 `-fcommon`，且必须 `rm -rf build-*` 重编（改 CFLAGS 不会触发重编）。
10. **`-Werror` 太严**：老 SDK 一堆宏重定义，加 `-Wno-error`。
11. **`freeze("app.py")` 会被当目录打开**（WinError 267）。正确写法是
    `freeze(".", ("app.py", "web.py", "ssd1306.py"))`——第一个参数必须是目录。
12. **缺 `esp_sdk_ver.h`**：esp-open-sdk 编译时生成，手写一个即可
    （`#define ESP_SDK_VERSION 0x020200`）。
13. **缺 `aes_encrypt_init` / `aes_wrap`**：原厂 `libwpa.a`/`libcrypto.a` 只是引用，
    实现由 esp-open-sdk **重编的 libwpa.a** 提供，原厂 SDK 里没有。
    暂时用 `aes_stub.c` 空桩 + ld 的 `--unresolved-symbols=ignore-all` 绕过。
14. **`--allow-undefined` 是 lld 的选项**，GNU ld 要用 `--unresolved-symbols=ignore-all`。

## 云端编译（推荐路线）

本机路线的死结是**原厂 SDK 的静态库与 gcc 10 的 ABI 不合**。换个角度就绕开了：
MicroPython 的 esp8266 port 里写着 `ESP_SDK = $(shell $(CC) -print-sysroot)/usr`——
**SDK 的库和头文件是装在交叉编译工具链自己的 sysroot 里的**，官方
`xtensa-lx106-elf-standalone` 工具链里那套 `libhal.a` / `libpp.a` / `liblwip.a`
是用同一个 gcc 一起编出来的，天然匹配。本机之所以要外挂原厂 SDK，是因为
earlephilhower 的 Windows 包只给了 gcc、没给 SDK。

所以只要在 Linux 上用官方工具链编译，连 esp-open-sdk 都不用碰。

### 配置

`.github/workflows/build-firmware.yml`，流程和 MicroPython 官方
`.github/workflows/ports_esp8266.yml` 一致：

1. `actions/cache` 缓存官方工具链（首次约 80MB，之后命中缓存）
2. 下载 `https://micropython.org/resources/xtensa-lx106-elf-standalone.tar.gz`，
   删掉工具链里 2016 年的 `esptool.py`
3. `sudo pip3 install esptool==3.3.1` —— 必须钉这个版本，Makefile 用的是
   `esptool.py --chip esp8266 elf2image` 这种 3.x 的写法，4.x 默认输出名变了
4. `git clone --depth 1 -b v1.29.0` 拉源码，先编 `mpy-cross`，再
   `make -C ports/esp8266 submodules` 拉 axtls / berkeley-db
5. 正式编译：

```
make -C ports/esp8266 \
  BOARD=ESP8266_GENERIC BOARD_VARIANT=FLASH_1M \
  FROZEN_MANIFEST=$GITHUB_WORKSPACE/frozen_manifest.py \
  MICROPY_PY_SSL=0 MICROPY_SSL_AXTLS=0 MICROPY_PY_BTREE=0
```

`FROZEN_MANIFEST` 直接指向仓库里的 `frozen_manifest.py`。清单里的
`freeze(".", (...))` 是**相对清单文件所在目录**解析的（`manifestfile.py`
执行清单前会 `os.chdir(os.path.dirname(manifest_path))`），所以 `app.py`、
`web.py`、`ssd1306.py` 就放在旁边即可，不用往源码树里拷。

关掉 SSL 和 BTREE 是因为这台时钟只用明文 HTTP（TLS 一握手就 OOM），
BTREE 也没用上，两者合计省约 35KB flash。想编全功能版就在
Actions 页面手动触发时把 `slim` 取消勾选。

### 用法

```bash
git init && git add -A && git commit -m "esp8266 clock"
git remote add origin https://github.com/<你的账号>/esp8266-clock.git
git push -u origin main
```

推上去后进仓库 **Actions** 标签页，`Build ESP8266 firmware` 会自动跑，
约 4~6 分钟。产物在 run 页面底部的 **Artifacts** → `firmware-1m.zip`，
解压得到 `micropython-esp8266-1m-frozen.bin`。

手动重编 / 换容量：Actions → Build ESP8266 firmware → **Run workflow**，
可选 `1m` / `2m` / `4m`，以及是否精简。

打 tag 时会自动建 Release 附上固件：

```bash
git tag v1 && git push origin v1
```

### 烧录

1MB 板（TYWE3L，GD25Q80C）：

```bash
esptool.py --chip esp8266 --port COM4 erase_flash
esptool.py --chip esp8266 --port COM4 --baud 460800 \
  write_flash --flash_size=1MB --flash_mode=qio 0 micropython-esp8266-1m-frozen.bin
```

擦除会清掉文件系统，所以烧完还要用 `python build.py upload COM4` 把
`main.py` 和 `config.py` 传上去（`wifi.txt` / `geo.txt` 也没了，要重新配网：
连 `YD-Clock-SETUP`（开放，无密码）→ 打开 `192.168.4.1`）。

### 编译失败怎么查

| 现象 | 原因 |
|---|---|
| `xtensa-lx106-elf-gcc: not found` | 工具链缓存命中但解压不完整，改 cache 的 `key` 重跑 |
| `esptool.py: error: unrecognized arguments` | 装成了 4.x/5.x，检查有没有被别的包覆盖 |
| `region irom0_0_seg overflowed` | 固件超过 572KB，把 `slim` 打开或减少冻结模块 |
| `No module named manifestfile` | `make -C ports/esp8266` 必须在源码树根目录下跑 |
| `lib/micropython-lib/README.md` 不存在 | 没 init micropython-lib 子模块，见下方第 15 条 |

## 又踩的一个坑（云端）

15. **`py/manifest.mk` 生成 `frozen_content.c` 前会无条件 `test -e
    $(MPY_LIB_DIR)/README.md`**，不存在就直接 `false`。但 `make -C ports/esp8266
    submodules` 的 `GIT_SUBMODULES` 里只有 `lib/axtls` 和 `lib/berkeley-db-1.xx`
    ——只有 cmake 版（`py/mkrules.cmake`）才会自动补 `lib/micropython-lib`。
    即使清单里一个 `require()` 都没有，也必须手动
    `git submodule update --init lib/micropython-lib`。

（顺带一个手工调试的坑：想跳过 make 直接跑 `makemanifest.py` 验证清单时，
`-f` 的值不能拆成两个词，`-f "-march=xtensa"` 会被 argparse 当成新选项，
得写 `--mpy-cross-flags=-march=xtensa`。make 拼出来的是 `-f"-march=xtensa"`，
一个词，所以官方构建不受影响。）


## 还没解决的问题（本机 Windows 路线）


固件能链接出来（`text 558KB / bss 66.5KB`，`firmware.bin` 550KB），烧进去 bootloader
校验通过（`chksum` 正常），但**应用段没有任何串口输出、REPL 也进不去**。
同一块板烧官方 v1.29.0 固件能正常跑，所以板子和烧录流程没问题。

已经排除：未定义符号（加桩后仍不启动）、隐式函数声明（编译日志 0 条）、
链接脚本（`esp8266_2MiB.ld` 正确）、flash 参数（`--flash_size=detect`）。

最可能的原因是**原厂 SDK 的库与 gcc 10 编译的代码 ABI 不合**——esp-open-sdk 的
价值不只是 gcc，还有它用 `-mforce-l32` 重新编译过的整套 SDK 库。这条路要继续，
得拿到 esp-open-sdk 的真实产物（Linux 下 `make STANDALONE=y` 编一次，或者从
`larsks/esp-open-sdk` Docker 镜像里取 `/opt/esp-open-sdk`）。
