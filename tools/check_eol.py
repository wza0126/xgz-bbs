# -*- coding: utf-8 -*-
"""检查 shell 脚本换行符（必须 LF，否则 NAS 上 bash 会报错）。"""
import pathlib
import sys

CRLF = bytes([13, 10])
LF = bytes([10])
bad = 0
targets = sorted(pathlib.Path("deploy").glob("*.sh"))
for p in targets:
    b = p.read_bytes()
    n_crlf = b.count(CRLF)
    n_lf = b.count(LF)
    flag = "OK(LF)" if n_crlf == 0 else "!! CRLF!!"
    if n_crlf:
        bad += 1
    print(f"{flag:>10}  {p}  bytes={len(b)}  lines={n_lf}")
print("全部 LF，可以直接丢到 NAS" if not bad else "有 CRLF，需要转换")
sys.exit(1 if bad else 0)
