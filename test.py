import osmosdr
src = osmosdr.source(args="numchan=1 redpitaya=169.254.9.106:1001")
src.set_sample_rate(500000)
print(src.get_sample_rates())   # prints the meta-range of valid rates
print("current:", src.get_sample_rate())