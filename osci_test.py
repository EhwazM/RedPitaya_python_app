import time
import numpy as np
import app.redpitaya_scpi.redpitaya_scpi as scpi

IP, F, DEC = "rp-f0c5e4.local", 10_000, 8
rp = scpi.scpi(IP)
for ch in (1, 2):
    rp.tx_txt(f"SOUR{ch}:FUNC SINE")
    rp.tx_txt(f"SOUR{ch}:FREQ:FIX {F}")
    rp.tx_txt(f"SOUR{ch}:VOLT 0.5")
    rp.tx_txt(f"OUTPUT{ch}:STATE ON")
    rp.tx_txt(f"SOUR{ch}:TRIG:INT")
time.sleep(0.2)

def arm_and_wait(dly):
    rp.tx_txt("ACQ:RST")
    rp.tx_txt("ACQ:DATA:FORMAT BIN")
    rp.tx_txt("ACQ:DATA:UNITS VOLTS")
    rp.tx_txt(f"ACQ:DEC {DEC}")
    rp.tx_txt(f"ACQ:TRIG:DLY {dly}")
    rp.tx_txt("ACQ:TRIG:LEV 0")
    rp.tx_txt("ACQ:START")
    time.sleep(16384 * DEC / 125e6 * 1.5 + 0.01)
    rp.tx_txt("ACQ:TRIG NOW")
    while rp.txrx_txt("ACQ:TRIG:STAT?") != "TD":
        time.sleep(0.001)
    while rp.txrx_txt("ACQ:TRIG:FILL?") != "1":
        time.sleep(0.001)

def read_block(cmd):
    rp.tx_txt(cmd)
    return np.frombuffer(rp.rx_arb(), dtype=">f4").astype(float)

def ptr(cmd):
    return int(rp.txrx_txt(cmd))

def jumps(y, thr=0.05):
    idx = np.where(np.abs(np.diff(y, 2)) > thr)[0] + 1
    out = []
    for i in idx:
        if not out or i - out[-1] > 16:
            out.append(int(i))
    return out

for dly in (0, 8192):
    bad_server = {1: 0, 2: 0}
    bad_own = {1: 0, 2: 0}
    for k in range(20):
        arm_and_wait(dly)
        w, t = ptr("ACQ:WPOS?"), ptr("ACQ:TPOS?")
        for ch in (1, 2):
            ring = read_block(f"ACQ:SOUR{ch}:DATA:START:END? 0,16383")
            server = read_block(f"ACQ:SOUR{ch}:DATA?")
            own = np.roll(ring, -(w + 1))          # oldest sample first
            bad_server[ch] += bool(jumps(server))
            bad_own[ch] += bool(jumps(own))
            if jumps(own):
                print(f"  dly={dly} f{k} CH{ch} WPOS={w} own_jumps={jumps(own)} ring_jumps={jumps(ring)}")
    print(f"DLY {dly}: server buffer bad {bad_server}/20, own unroll bad {bad_own}/20, "
          f"trigger index in own buffer = {(t - (w + 1)) % 16384}")
rp.close()