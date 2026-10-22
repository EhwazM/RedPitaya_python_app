import time
import numpy as np
import app.redpitaya_scpi.redpitaya_scpi as scpi   # copy of your redpitaya_scpi.py next to this script

IP, DEC, F, N = "rp-f0c5e4.local", 8, 10_000, 30
FS = 125e6 / DEC
rp = scpi.scpi(IP)

