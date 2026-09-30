import os
import sys
import time

workers = int(sys.argv[1])
for _ in range(workers):
    if os.fork() == 0:
        while True:
            pass
while True:
    time.sleep(60)
