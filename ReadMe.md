#### Changes from "Burrow_antenna_capture_v1_2.py"
- Reorganized the script into multiple modular scripts for easier debugging / future modifications
	- Organization of the files is in the docstring at the top of `main.py`
	- Biggest change is that all user parameters are consolidated and edited from one place (`config.py` / `settings.json`)
	- Changes there propagate through the rest of the helpers
- Enabled **discrete sample rates** to be selected, as arbitrary sample rates are not supported by the Red Pitaya firmware through the osmosdr packaging (an unsupported rate silently falls back to 100 kHz)
- Fixed the `rx1.bin` / `rx2.bin` (formerly `remote1.bin` / `remote2.bin`) startup-write bug
	- Their was automatic raw IQ recording to the remote1/remote2.bin sinks at startup. This wrote to the bins continuously wasting space if the program was running for too long. Then, every time the "start recording" button was pressed, the bins were cleared and the recording restarted and then saved as a csv when "stop" was pressed.
	- The automatic recording feature was removed.
	- The file sinks now start pointed at the OS null device (`os.devnull`), so **nothing is written until recording is explicitly triggered** 
	- Capture files were renamed `rx1.bin` / `rx2.bin` and are written into a `captures/` folder next to `main.py` (configurable)
- Removed the separate **"GR Controls" pop-up window**. These were redundant with the custom dashboard.
- Added the **Settings Ribbon** (see below) for live profile management and applying parameter changes.
- Added the standalone `lwd_plotter.py` script as a new **"LWD PLOTTER"** tab in the PyQt5 GUI.

---

#### Settings & Profiles (`settings.json`)

All tunable parameters live in `settings.json` at the project root:

- **`working`** — the live values the app actually runs on. The app reads this on startup.
- **`profiles`** — named presets (e.g. `default`, `indoor_demo_v1`) you can Load from / Save to.
- **`loaded_from`** — the profile name the working copy was last loaded from.

Editing fields in the ribbon changes only the *working* copy — never the preset you loaded from. Saving is the only action that writes a named preset. `config.py` supplies built-in defaults used as a fallback if a key or the file is missing, so profiles only need to list what differs.

---

#### Settings Ribbon (strip across the top of the window)

The ribbon is the control strip above the dashboard tabs. It has two rows: profile management, and the editable parameter fields plus the apply button.

##### Profile controls
- **Profile dropdown** — selects which named preset the profile buttons act on.
- **Load** — copies the selected preset's values into the working copy and into the fields. The preset itself is left untouched; any unsaved edits in the fields are discarded.
- **Save** — overwrites the *currently selected* preset with the current field values.
- **Save As** — prompts for a new name and creates a *new* preset from the current fields, leaving the original untouched.
- **Delete** — removes the selected preset (won't let you delete the last remaining one).
- **`● edited (unsaved)`** indicator — appears when the fields differ from the preset they were loaded from.

##### Parameter fields
- **`samp_rate`** *(dropdown — valid rates only)* — the master I/Q sample rate. This is a dropdown of only the firmware-supported rates (20k / 50k / 100k / 250k / 500k / 1250k), specifically so a user **cannot** type an unsupported value and silently get the 100 kHz fallback. Sets the FFT frequency span (±`samp_rate`/2) and, with `fft_size`, the resolution and time window.
- **`center_freq` (Hz)** — RX + TX tuning frequency. Sets the center of the FFT x-axis (the hardware tunes to this; both sources and the TX sink use it).
- **`fft_size`** *(dropdown)* — the number of samples batched per FFT / per ring-buffer read. See "What changing fft_size does" below.
- **`plot_fps`** — live plot refresh rate in frames per second. Internally converted to a millisecond timer interval (`refresh_ms = round(1000 / plot_fps)`). Higher = smoother but more CPU; 30 is a good default, 15–20 if the machine struggles, up to ~50 if you want it snappier. Does **not** depend on the sample rate.
- **`ema_alpha`** — exponential-moving-average smoothing factor for the displayed amplitude only (range 0–1). Lower = smoother / slower to react; higher = jumpier / more responsive. `1.0` means no smoothing. (Phase is not EMA-smoothed; it uses a magnitude-weighted circular mean.)
- **`band` (Hz)** — the ROI `[low, high]` window (two fields) used for the amplitude peak search in the Amplitude & Phase panel. 
	- Essentially says "look from [fc-low, fc+high] when doing the peak finding for the equalizer panel
	- By default it was set to a generous +/- 30 kHz which is wider than the filter band (can narrow if needed)
- **`note`** — a text label baked into the recording filenames (`capture_ampphase_<note>_<timestamp>.csv`).

##### Apply
- **Apply & Restart** — writes the current field values into the working copy of `settings.json`, then re-execs the process so the new values take effect. The window briefly closes and reopens; data buffers reset and plots restart from zero. This is used for *all* changes (there are no live-update fields) — which is fine because parameters are only meant to change between tests, not mid-capture.

---

#### What changing `fft_size` does internally

It will not break anything — every consumer reads `fft_size` from the same source and resizes together at startup. `cfg.fft_size`:
- sizes both ring buffers (`RingBuffer(size=fft_size)`),
- sets the reader-thread chunk size,
- and is passed to the dashboard, which sizes the FFT and phase panels.

Effects of a **larger** `fft_size`:
- **Finer frequency resolution** — bin width `Δf = samp_rate / fft_size` gets smaller.
- **Longer time window per buffer** — `fft_size / samp_rate` seconds, so the phase panel's time axis spans more (e.g. at 100 kHz: 1024 → ~10 ms, 4096 → ~41 ms, 16384 → ~164 ms).
- **More CPU per redraw** — the FFT cost scales roughly `N·log N`, so very large sizes at a high `plot_fps` cost the most.

A **smaller** `fft_size` is the reverse: coarser resolution, shorter window, cheaper to draw. Because it changes the ring buffers and panel construction, it only takes effect on restart — which is why it's an Apply & Restart field rather than a live one.

---

#### Running without hardware

Not supported in this snapshot. An earlier version of this document
described a `dummy_mode` flag that replaced the Red Pitaya blocks with
synthetic sources; no such flag exists in the code. The app requires the
RX and TX Pitayas to be reachable.

---

#### Block rename table (old GRC names → current names)

`main.py` was GRC-generated once and is now hand-maintained (there is no
`.grc` file). The GRC-generated block names were renamed to describe the
signal each block carries. `sdr_` marks a physical radio boundary
(osmosdr source/sink); everything else is software. Vector sinks, ring
buffers and reader threads are named after what feeds them.

Older recordings, notebooks and notes may still use the left-hand names.

| Old (GRC-generated)              | Current                             |
|----------------------------------|-------------------------------------|
| analog_sig_source_x_0_0          | siggen_tx_tone                      |
| osmosdr_sink_0_0                 | sdr_tx                              |
| fft_filter_xxx_0_0_0             | lpf_tx_ref                          |
| osmosdr_source_0                 | sdr_rx_meas1                        |
| fft_filter_xxx_0                 | lpf_rx_meas1                        |
| blocks_file_sink_0               | rec_rx_meas1                        |
| osmosdr_source_1                 | sdr_rx_meas2                        |
| fft_filter_xxx_0_0               | lpf_rx_meas2                        |
| blocks_file_sink_0_0             | rec_rx_meas2                        |
| blocks_multiply_conjugate_cc_0   | multiply_conjugate_rx_txconj        |
| sink1                            | sink_lpf_rx_meas1                   |
| sink_prod                        | sink_multiply_conjugate_rx_txconj   |
| rb1                              | rb_lpf_rx_meas1                     |
| reader1                          | reader_lpf_rx_meas1                 |
| rb_prod                          | rb_multiply_conjugate_rx_txconj     |
| reader_prod                      | reader_multiply_conjugate_rx_txconj |

The dashboard/panel constructor arguments that carry these streams were
renamed to match: `ringbuffer1` → `rb_lpf_rx_meas1`, `ringbuffer_prod` →
`rb_multiply_conjugate_rx_txconj`.

Output capture filenames (`rx1.bin`, `rx2.bin`, the amp/phase CSV) are
unchanged.

---

#### `num_receivers` — how many receiver chains to build

`num_receivers` in `settings.json` selects how many RX chains the flowgraph
constructs. Each chain is built by `trx_ssb._build_rx_chain(idx, ...)`:

    sdr_rx_measN (redpitaya <rx_addr>:100N)  ->  lpf_rx_measN  ->  rec_rx_measN

- **`1` (default, matches the current hardware)** — only the RX IN1 chain
  (port 1001) is built. `sdr_rx_meas2`, `lpf_rx_meas2` and `rec_rx_meas2`
  are not created at all. With nothing plugged into RX IN2 that chain was
  streaming, filtering (3301 taps) and recording (~800 kB/s) pure noise.
- **`2`** — the RX IN2 chain (port 1002) is built as well. It is
  record-only: it feeds `rec_rx_meas2` and nothing else, exactly as before.

Only chain 1 gets the measurement taps — the `sink_lpf_rx_meas1` vector
sink the panels read, and input 0 of `multiply_conjugate_rx_txconj`.
Anything that touches chain-2 blocks (the sample-rate/centre-freq setters,
the record start/stop) is guarded with
`if getattr(self, 'sdr_rx_meas2', None) is not None:`, so it is a no-op in
1-receiver mode.

All LPFs (both receivers plus `lpf_tx_ref`) share one `firdes.low_pass`
tap list — the three designs were always identical.

Any value other than 1 or 2 raises a `ValueError` from `AppConfig` at
startup.
