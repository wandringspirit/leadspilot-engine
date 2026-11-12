"""Hold some memory so Oracle's Always Free idle check doesn't count this server as idle.

Oracle reclaims an Always Free A1 server when, over 7 days, CPU, network AND memory all stay under 20 %.
This keeps memory above that line. It uses no CPU and no network. The real fix is a Pay As You Go
account (exempt); then remove it:  sudo systemctl disable --now memory-ballast
"""
import ctypes
import os
import signal

MB = int(os.environ.get("BALLAST_MB", "1300"))
buf = bytearray(MB * 1024 * 1024)
page = os.sysconf("SC_PAGE_SIZE")
for i in range(0, len(buf), page):          # touch every page, so it is really in memory
    buf[i] = 1

libc = ctypes.CDLL("libc.so.6", use_errno=True)
if libc.mlockall(1) != 0:                   # MCL_CURRENT: never swapped out
    print("mlockall failed (errno %d): the memory may be swapped out" % ctypes.get_errno(), flush=True)
print("holding %d MB" % MB, flush=True)

signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
while True:
    signal.pause()
