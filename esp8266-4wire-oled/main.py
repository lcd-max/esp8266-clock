# 开机入口（这个文件必须是源码：MicroPython 启动时用 pyexec_file 直接编译
# main.py，塞 .mpy 进去会报 SyntaxError）。
# 真正的程序在 app.mpy —— 编译过的字节码，比源码省三分之二内存。
import app

app.main()
