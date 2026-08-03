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
- **APPLY** — writes the current field values into the working copy of `settings.json` and applies them to the *running* app. The window never closes. What happens per field:

  | Field | Applied |
  |---|---|
  | `center_freq_hz` | live — retunes the RX and TX radios and the panel axes instantly |
  | `note` | live — the next recording uses the new name |
  | `band_hz` | live — ROI mask in the equalizer panel |
  | `ema_alpha` | live — amplitude smoothing constant |
  | `plot_fps` | live — panel refresh timers |
  | `samp_rate_hz` | flowgraph rebuilt in place (~1 s pause) |
  | `fft_size` | flowgraph rebuilt in place (~1 s pause) |

  A rebuild stops the flowgraph, drops and recreates the GR blocks at the new settings, and starts it again — the Qt window, the dashboard and the ring buffers all survive, so only the data restarts, not the app. The status line under the fields reports which path was taken.

- **RESTART APP** — the old behaviour: save and re-exec the whole process. Only needed if the radios get into a state an in-place rebuild cannot clear.

---

#### Absolute power calibration

The amplitude readout is now a real measurement in **dBFS**: 0 dB is a full-scale complex tone at the converter.

The old number was not. It computed `20*log10(|FFT(x*w)| / sqrt(sum(w^2)))`, which reads **+34.4 dB high** at `fft_size = 4096` — and the offset is `20*log10(sqrt(2N/3))`, so it moved with the FFT size:

| fft_size | old reading was high by |
|---|---|
| 1024 | +28.3 dB |
| 4096 | +34.4 dB |
| 16384 | +40.4 dB |

So the same signal read 12 dB apart at the two ends of the ribbon's `fft_size` list. It was fine as a relative indicator at a fixed FFT size and meaningless as anything else.

Two fixes make it absolute:

1. **Normalise by `sum(w)`, not `sqrt(sum(w^2))`.** A complex tone of amplitude A on a bin centre produces a peak of `|X| = A * sum(w)`, so this makes the scale read the tone's amplitude directly, at any `fft_size`.
2. **Sum the main lobe instead of reading the peak bin.** A tone almost never lands exactly on a bin, and Hanning scalloping costs up to 1.42 dB when it falls halfway between two. Summing `|X|^2` across the main lobe and normalising by Parseval recovers the true power wherever it falls — verified exact at 0, 0.1, 0.25 and 0.5 bins of offset, and identical at `fft_size` 1024 / 4096 / 16384.

##### Getting to dBm

dBFS is referenced to the converter, not the antenna. To read absolute power you need one measured offset, which absorbs the Red Pitaya input range (the LV/HV jumper), the input impedance and termination, and any LNA or cable loss ahead of the receiver.

You do not need a signal generator. **The TX Pitaya is a known source** — and a better reference than a generator would be, because it is the exact transmitter the measurement uses.

**Loopback procedure** (TX output known to be −6 dBm):

1. Disconnect the coil from RX IN1.
2. Cable TX OUT1 straight into RX IN1. Add a known attenuator if you have one; a short coax at these frequencies loses well under 0.1 dB, so without one the level at the input is −6 dBm.
3. Launch, let the reading settle, and note `SIG 1` (with `power_cal_offset_db = 0` it reads dBFS).
4. Set `power_cal_offset_db = −6 − shown_dBFS` (minus the attenuator's dB, if you used one) and `power_unit = "dBm"`. Both apply live from APPLY.
5. Reconnect the coil.

**Sanity check.** −6 dBm into 50 Ω is 112 mV rms, 158 mV peak — about −16 dB relative to a full-scale real sine on the ±1 V LV input. Depending on whether gr-osmosdr maps a real sine of peak Vp to `|z| = Vp` or `Vp/2`, expect `SIG 1` somewhere around **−16 to −22 dBFS**, giving an offset of roughly **+10 to +20 dB**. A wildly different answer (−70 dBFS, or a positive dBFS) means the path, the termination or the jumper is not what you think it is, and the offset would just be encoding that mistake. It is also a safe level: 158 mV peak is well inside the ±1 V input range, no clipping and no risk to the input.

**Two-point linearity check** (worth doing once, costs nothing extra). Repeat step 3 with a known attenuator in line. The dBFS reading should drop by exactly the attenuator's value. If a 20 dB pad moves the reading by 20 dB, the whole chain is linear and the single-offset model is valid; if it moves by 14 dB, something is compressing and one offset will not describe the receiver.

**What the offset assumes.** It is only valid while the physical path stays as it was during calibration:

- Calibrate through the *same* input, jumper position and termination you measure with. The STEMlab fast inputs are high-impedance, not 50 Ω, so an unterminated line reads roughly 6 dB high — harmless if calibration and measurement share it, a 6 dB error if you add a terminator afterwards.
- TX drive must be unchanged. Nothing in the ribbon touches TX amplitude or gain (the sink's gains are fixed at construction and the baseband tone is a constant), so this holds unless the flowgraph is edited.
- Re-run it after any front-end change: a different LNA, a different cable, a moved jumper.

#### The SPECTRUM tab

The frequency-domain views live in their own tab, next to SDR DASHBOARD and LWD PLOTTER. Three plots, all of receiver 1, tapped at three points:

| Plot | Tapped at | What it tells you |
|---|---|---|
| **PRE-LPF** | `sdr_rx_meas1` output, before any filtering | What the antenna actually delivers: the real noise floor, interferers, supply spurs. Use this when changing the physical setup. |
| **POST-LPF** | `lpf_rx_meas1` output | The same signal after the 1 kHz low-pass — what the measurement actually consumes. Comparing the two shows what the filter is doing. |
| **PEAK SEARCH** | `lpf_rx_meas1` output | The post-LPF spectrum with the search band shaded and a marker on the bin the amplitude readout is reporting, plus its offset from centre. |

The pre-LPF tap is a new vector sink straight off the radio; the measurement path is untouched by it.

**Span and refresh.** `spectrum_span_hz` (ribbon: `spec_span`) sets the half-span of the frequency axis, defaulting to the LPF passband — there is little point looking across ±50 kHz when everything past ±1 kHz is stopband. It is not only a zoom: bins outside the span are dropped *before* the curve reaches pyqtgraph, and the number of points drawn per frame is the real cost (81 points instead of 4096 at ±1 kHz of a ±50 kHz band). Set it to `0` for the full sampled bandwidth — which you want when hunting interferers on the PRE-LPF plot, since at ±1 kHz that plot can no longer show anything outside the passband.

`spectrum_fps` refreshes the spectra independently of `plot_fps`, defaulting to 10 rather than 30. The spectra also stop entirely while another tab is on screen.

**Filter control.** `lpf_cutoff_hz` (ribbon: `lpf_cutoff`) and `lpf_transition_hz` set the shared receiver low-pass. Both apply live — `fft_filter_ccc.set_taps()` is a runtime call, so no rebuild. One design is shared by both receivers and the TX reference and must stay that way: a mismatch between the RX and TX-reference filters shows up in the conjugate product as a phase error.

#### `search band` — what the peak search does, and what it does not

The ribbon field is labelled **search band** because it filters nothing. It is used in exactly one place — bounding an `argmax` over FFT bins:

```python
mask = (rf >= fc + band[0]) & (rf <= fc + band[1])
peak = np.argmax(mag[mask])
```

So it answers "where should I look for the strongest bin?", and the answer is **the strongest bin in the band, which is not necessarily the carrier at the centre frequency**. If anything inside the band is stronger — an interferer, a spur — the amplitude readout silently follows that instead, and before the PEAK SEARCH plot existed there was no way to tell: the peak's *position* was computed and then discarded, only its level was kept.

In practice the 1 kHz LPF protects you today, since everything beyond ±1 kHz is 60+ dB down before the search ever runs. The marker exists so that stays a fact you can check rather than an assumption. If the marker sits at 0 Hz offset, the readout is measuring the carrier; if it wanders, it is not.

Narrowing the search band to roughly the LPF passband (say ±1 kHz) makes it structurally impossible for a distant interferer to capture the readout. `roi_peak()` in `panels.py` is the single implementation, used by both the equalizer that measures and the panel that draws it, so the marker can never disagree with the number.

#### Input safety checks (`scripts/validate.py`)

Every profile goes through `validate_profile()` before it reaches the radios — from the ribbon's APPLY and again inside `apply_settings()`. It splits problems into two kinds:

- **Errors — refused, nothing is applied.** The radios keep their current settings and a dialog explains why. These are the values that would reach hardware or silently corrupt a measurement: a centre frequency outside the Red Pitaya's 0–62.5 MHz range (the 125 MS/s converters' Nyquist limit — the FPGA image would *alias* rather than refuse, so a mistyped frequency has to be caught here), a sample rate that isn't one of the driver's supported rates (it silently falls back to 100 kHz and every frequency axis becomes wrong), an `fft_size` that isn't a power of two, an `ema_alpha` outside (0, 1], an ROI band that is inverted or lies entirely outside the sampled bandwidth (the mask would select no FFT bins and the equalizer would quietly stop updating), or an empty `note`.
- **Warnings — applied, but reported** in the ribbon's status line. Display-only knobs are clamped to a sane range rather than refused (`plot_fps` to 1–120, `rolling_window_s` to 1–3600 s, `emit_interval_ms` to 10–10000 ms), and legal-but-odd values get a note (a very low centre frequency, an ROI wider than Nyquist, a `note` containing path separators).

The rule behind the split: a display value that is merely silly can be corrected silently, but a hardware value that is wrong must never be silently corrected — clamping a mistyped centre frequency to the band edge would transmit on the wrong frequency without telling anyone.

Transmit power is not exposed anywhere in the UI: the osmosdr sink's gains are fixed at construction, so there is no setting in the ribbon that can raise drive level, dissipation or voltage.

#### Rolling window width

The rolling strip-chart has a **WINDOW (s)** box in its header, left of the record button. Type a value and press Enter; it applies immediately, with no APPLY and no restart.

The chart's x-axis is wall-clock time (`time.monotonic()`), so the scroll *speed* is real time and nothing changes it — not `fft_size`, not the centre frequency, not the averaging. `emit_interval_ms` (default 100 ms) only sets how densely points are drawn. What the window width controls is how long a point stays on screen, and therefore how long it keeps affecting the autoscale: `_autoscale_y` fits the range to the points still inside the window, so a big transient holds the scale open until it rolls off the left edge. Shortening the window is the fastest way to recover the detail after, say, keying the transmitter off and on — at 60 s you wait a minute for the dip to clear, at 10 s you wait ten seconds.

Shortening trims the older points immediately (that is the point — the autoscale rescales at once rather than on the next sample). Those points are discarded, so widening the window again refills from live data rather than restoring history.

#### What changing `fft_size` does internally

It will not break anything — every consumer reads `fft_size` from the same source and resizes together at startup. `cfg.fft_size`:
- sizes both ring buffers (`RingBuffer(size=fft_size)`),
- sets the reader-thread chunk size,
- and is passed to the dashboard, which sizes the FFT and phase panels.

Effects of a **larger** `fft_size`:
- **Finer frequency resolution** — bin width `Δf = samp_rate / fft_size` gets smaller.
- **Longer time window per buffer** — `fft_size / samp_rate` seconds, so the phase panel's time axis spans more (e.g. at 100 kHz: 1024 → ~10 ms, 4096 → ~41 ms, 16384 → ~164 ms).
- **More CPU per redraw** — the FFT cost scales roughly `N·log N`, so very large sizes at a high `plot_fps` cost the most.

A **smaller** `fft_size` is the reverse: coarser resolution, shorter window, cheaper to draw. Because it resizes the ring buffers and the panels' window/axis arrays, it cannot be absorbed by the running blocks — APPLY rebuilds the flowgraph in place for it (the window stays open).

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
