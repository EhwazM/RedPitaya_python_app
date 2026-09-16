from redpitaya_lia import RedPitayaLIA

HOST = "rp-f0c5e4.local"

with RedPitayaLIA(HOST) as lia:
    lia.configure(
        frequency_hz=1000.0,
        phase_offset_rad=0.0,
        k=17,
        n_stages=2,
    )

    while True:
        try:
            measurement = lia.get_measurement()
            print(f"Measurement: {measurement}")
        except KeyboardInterrupt:
            break
