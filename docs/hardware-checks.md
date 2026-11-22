# What waits on hardware

Every check, measurement, threshold or open question in this repository that cannot be settled until real hardware
or real recorded data exists. One list, so that none is missed when the rig, the breakout board, the cameras or the
compute machine arrive. The requester asked for it on 2026-11-21.

**How to use it.**
- **When a piece leaves something for the hardware,** add a row here in the same commit, with its source.
- **When an item is settled,** strike it through, and say how and in which commit. Don't delete it.
- **Sources** are `path:line` at the time the row was written.

**How it was made.** A read-only sweep of the specs, plans, handoffs, the checkpoint, `wl.yaml` and the handover
and amendment files, at `291bbea` (2026-11-21), plus the items of the analog-copy design (amendments 77-83). Each
item was judged open from its text. The code was not run. Items a later document settled were dropped. The last
section lists what the sweep could not judge. Rows 9a, 15a, 16b, 17a, 18a and 18b, and the built names in rows
15-18, were added when the analog copy was built (amendments 85 and 86, 2026-11-21), from its ledger's notes.

## The synced rig: sync box live, NI/SpikeGLX, Intan

| # | Waits on | The check | What it settles | Source |
|---|---|---|---|---|
| 1 | A few minutes recorded with the sync box live, with NI and Intan | Run barcode decode, rate fit, offset alignment and the whole timing chain on real data. They have only ever run on synthetic fixtures; the lab's one recording has no wl-sync barcodes. | Whether the alignment path works at all: the cheapest large risk reduction before January. | `wl.yaml` `status.next`; `docs/CHECKPOINT.md:471` |
| 2 | The same recording | The rig's frame-gap rate. | Whether gap-aware barcode extraction was worth building. The reference file has no gaps. | `docs/handoffs/2026-09-19-gap-aware-barcode-extraction-built.md:241` |
| 3 | A rig recording with NI, Intan and strobes | One limit on stored fit residuals for every device's fit, barcodes included. It is unset by choice; a real crystal's 12 h rate wander is unmeasured. | A residual gate on publishing. | `docs/handoffs/2026-11-20-part-3b-2.md:58`, `:108` |
| 4 | Rig strobe and code-word streams | The rig's real code-word burst sizes: fixed bursts of 11 or more words make the strobe matcher refuse. Also the cost of refusing a 12 h recording that does not match (422 s on synthetic data). | The strobe matcher's margin rule and its `Unanchored` refusals. | `docs/handoffs/2026-11-20-part-3b-2.md:176`, `:189` |
| 5 | wl-sync's bench day on the Pi 5 box | The latency from a `B` tick to the barcode's lead edge on the wire, which wl-sync then writes as `barcode_delay_us`. | The barcode delay correction. Until then it is recorded as unmeasured and not corrected. | `HANDOVER-wl-sync.md:61`, `:137` |
| 6 | wl-sync's bench acceptance | The `W` tick's offset at capture start, and the PIO timestamp gate: within 100 µs over 10 min, with no dropped words. | Whether the code-word clock model holds. If not, a Pi 4 with `pigpio` is the fallback. The box runs `--fake` until then. | `HANDOVER-wl-sync.md:179`; `docs/superpowers/specs/2026-11-04-syncbox-header-design.md:50` |
| 7 | A real box run lasting hours | wl-sync's fix for unwrapping its 71.6-minute tick wrap (`d309bf1`) holds. | Whether codes after a long pause are timed early by multiples of 71.6 min. | `HANDOVER-wl-sync.md:151` |
| 8 | A real box | `rp1-pio`'s unscaled ticks against SI seconds: wl-sync's figure is up to 50 ppm, its rate unmeasured. | How far the one-count timebase's seconds are from true seconds; the daily report's split-clock section. | `docs/superpowers/specs/2026-11-04-syncbox-header-design.md:191`; `docs/handoffs/2026-11-20-wl-sync-notes-7-8.md` |
| 9 | The rig's SpikeGLX output | The rig's real SpikeGLX layout, which has not been seen (both layouts are read). Also the NHP `~imroTbl` geometry against `ElectrodeConfig`. | The probe census, electrode-table geometry and `SegmentConfig`. | `docs/handoffs/2026-09-30-nwb-probes.md` §3 |
| 9a | The rig's NI `.meta` | Whether the rig saves a subset of the NI's channels (`snsSaveChanSubset` other than `all`). `read_nidq_meta` takes the binary's row width from `snsMnMaXaDw` and ignores `snsSaveChanSubset` and `nSavedChans`, so a channel-subset save would mis-index the XA channels, and the digital word with them: the digital-word extractors share the reader. Pre-existing, found in the analog copy's Task 3 review. | Whether the NI reader must take its width and columns from the saved subset. | `wl_preproc/timebase/_nidq_meta.py:110`, `:124`; the analog copy's ledger, Task 3 |
| 10 | A real Intan RHS session | `dcamplifier.dat`'s dtype: the vendor's text and its code disagree. Also how well stim words and `dcamplifier.dat` compress. | The RHS reader's `dcamplifier` scaling; archival. | `docs/superpowers/specs/2026-08-12-wl-preproc-design.md:672` |
| 11 | A real NP1032 (NHP) session | The compression ratio on NHP data (3.59 is from NP1), and a codec comparison on real noise. The synthetic fixture inverts the ranking. | The codec choice (`blosc-zstd` by default; WavPack about 6% better). | `docs/superpowers/specs/2026-08-27-archival-and-compression-design.md:127`, `:168` |
| 12 | Real transfers from the rig | `STALL_AFTER_S` (2 h, a first guess) against real transfer times. Also whether the rig or the transfer tool writes `DONE` per system. | A reporting threshold. | `docs/superpowers/specs/2026-08-15-phase-1c2-ingest-design.md:519` |
| 13 | Real rig data (January) | The reference mode and the size of the double high-pass. Also whether interpolating dead channels helps or harms sorting. | The reader and chain defaults (2b-2). | `docs/superpowers/specs/2026-08-26-phase-2b2-reader-and-chain-design.md:722` |
| 14 | Real stimulation sessions | An artifact-removal method, which depends on stimulation amplitude and geometry. | The artifact-removal paramset's default. | `docs/superpowers/specs/2026-08-12-wl-preproc-design.md:653` |

## The eye tracker's analog copy on the NI (amendments 77-86)

| # | Waits on | The check | What it settles | Source |
|---|---|---|---|---|
| 15 | A real session with both the sync box and the eye tracker's analog copy reaching the NI (needs the breakout board) | The copy's lag behind each frame, which `SystemTimebase.Copy` stores as `lag_s`, with `lag_measured` and `lag_source_datetime`. It must lie in `[0, LAG_CEILING_S]` (0.03 s), and the corrected copy must agree with the eye file's own barcodes within one frame (`agreement_us`), or the session is D. The measured lag is the whole delay, processing, the DAC's hold and any smoothing included: about 7.1 ms on the synthetic copy for a 5 ms set lag. | The lag correction. Until then, eye data placed by its copy is published labelled unmeasured, lag 0. | spec 2026-10-18, amendments 79, 86; `wl_preproc/schema/timebase.py:387-390`; `wl_preproc/timebase/analog_copy.py:152` |
| 15a | The same session, with a calibration inside the sync box's covered span | A held-gaze calibration inside the span gives no copy pairs: held gaze lines up with a wide peak, which fails `MIN_PEAK_MARGIN`. So a session whose only stretch inside the span is a calibration measures no lag and carries the rig's, or 0 unmeasured. No test has calibration windows inside the span of a copy-placed file; the synthetic `copy_partial` has its windows after it. | Whether real partial-box sessions measure the lag, or calibrations must leave free gaze inside the span. | the analog copy's ledger, Task 5; `wl_preproc/timebase/analog_copy.py:134` |
| 16 | The board, wired | Which NI analog inputs carry the eye signals. Nothing declares it; wl-sync pins only AI15, the audio tap. The matcher tries every XA channel (`NidqMeta.n_xa_channels`) and records the one it matched as `Copy.ni_channel`, `XA<k>`. | Confirms the matcher's search over every input. | amendments 78, 82; `wl_preproc/timebase/analog_copy.py:912`; `wl_preproc/schema/timebase.py:382` |
| 16a | OpenIrisDPI's analog-output settings on the lab's eye-tracker computer | What the ACCESIO DAC actually sends for eye X, Y and pupil: the P1-P4 difference, P1 alone, or OpenIris's own calibrated gaze. The matcher fits each of the six families in `analog_copy.FAMILIES` jointly (`Left P1-P4 (X, Y)`, `Left P1 (X, Y)`, `Left PupilWidth`, and the same for `Right`) and records the one it matched as `Copy.signal`. | Narrows the matcher's search over candidate sources. | amendments 78a, 86; `wl_preproc/timebase/analog_copy.py:175`; `wl_preproc/schema/timebase.py:383` |
| 16b | The same, with a blink and a stretch of lost tracking recorded | What the DAC writes while the pupil is lost, in a blink or lost tracking: held, zero, a rail, or something else. The eye file's lost-tracking zeros are passed to the matcher raw, not masked, so where the DAC writes something else a window holding the loss compares unlike values. | Whether lost frames are masked before matching (NaN gives no pair where it fills more than a tenth of a window). | the analog copy's ledger, Task 3; `wl_preproc/timebase/analog_copy.py:181` |
| 17 | Real eye traces with their copy | The matcher's constants, set from synthetic data: `DECIMATED_RATE_HZ` 1000, `WINDOW_S` 10, `STEP_S` 10, `SEARCH_PREFIX_S` 600, `MIN_CORRELATION` 0.9, `MIN_PEAK_MARGIN` 0.1, `RUNNER_UP_EXCLUSION_S` 0.05, `MIN_PAIRS` 20, `RESIDUAL_BOUND_US` 500, `MAX_DROPPED_FRACTION` 0.1, `LAG_CEILING_S` 0.03 and `CLOCK_SLACK_S` 120. What each session got is in `Copy`: `outcome`, `refusal`, `n_pairs`, `n_dropped`, `dropped_stretches` and `residual_us`. | The match's refusals and precision. | amendments 78, 82, 85; `wl_preproc/timebase/analog_copy.py:115-158`; `wl_preproc/schema/timebase.py:380-392` |
| 17a | The same | How nonlinear the DAC's output is against the columns it carries, measured directly, not only through `R`. The joint fit (amendment 86) also lifts rival peaks' `R`: on the synthetic copy, rivals reach 0.71-0.76, leaving margins of 0.17-0.25 against `MIN_PEAK_MARGIN` 0.1. So a trial-structured task whose gaze keeps returning to the centre may give no distinct anchor, and `NoCopy`, on a 12 h search. | `MIN_CORRELATION`, `MIN_PEAK_MARGIN`, and whether a family's linear model is enough. | the analog copy's ledger, Task 2; `wl_preproc/timebase/analog_copy.py:131-134` |
| 18 | The board | Buffering and any smoothing on the eye channels. Each channel is decimated to 1 kHz block means, each stamped at its block's centre, and each pair is refined below a sample by a parabola. A constant smoothing delay is part of the measured lag (amendment 86); one that varies shows as disagreement in 79's cross-check. | The matcher's sub-frame refinement. | amendment 82; `wl_preproc/timebase/analog_copy.py:889` |
| 18a | A real session run under wl-xcon, landed in its session folder | Whether the eye file keeps OpenIris's default name, `OpenIris-YYYYMonDD-HHMMSS`, which aims the channel search. The pattern was set from one name, the reference recording's (`OpenIris-2024Jul31-114628`). A renamed file, or another OpenIris version's name, falls back to `d = 0` with an INFO line. | Whether the search is aimed on real sessions, or the eye's start must come from elsewhere. | amendment 85; `wl_preproc/timebase/analog_copy.py:823` |
| 18b | The eye-tracker computer and the NI's acquisition computer, side by side | How far apart the two computers' wall clocks are, and how late each file's stamp is after its recording starts, against `CLOCK_SLACK_S` (120 s). An error beyond the slack gives absent, never a wrong match; the copy is in practice still found up to about 520 s. | `CLOCK_SLACK_S`. | amendment 85; `wl_preproc/timebase/analog_copy.py:158`, `:845` |

## The eye tracker: OpenIrisDPI, calibrated sessions, labelled data

| # | Waits on | The check | What it settles | Source |
|---|---|---|---|---|
| 19 | A calibrated session from the synced rig | Whether Otero-Millan and BMD discriminate on a calibrated recording, the BMD model's fit to monkey data, and hit and false-alarm rates. | Lifts PROVISIONAL from the Otero-Millan and BMD rows: the eye subsystem's highest-value unblock. | `docs/handoffs/2026-09-01-consensus-and-otero-millan-built.md:76`; `docs/handoffs/2026-09-27-bmd-built.md:316` |
| 20 | A calibrated session | The main-sequence defaults in degrees. The reference recording's degrees are a guessed scale. | The main-sequence and vigor defaults. | `docs/handoffs/2026-10-07-main-sequence.md:85` |
| 21 | Real calibrated sessions | The size of the second-order calibration at this lab's eccentricities, and the error-map diagnostic against real residuals per block. | Whether second-order calibration helps. | `docs/superpowers/specs/2026-08-31-second-order-calibration-design.md:288` |
| 22 | Hand-labelled macaque DPI data | Fine-tune U'n'Eye, whose networks were trained on video trackers. | U'n'Eye rows stay PROVISIONAL until then, after January. | `docs/handoffs/2026-10-06-uneye.md:74` |
| 23 | Hand-labelled macaque data | A supervised emission refit for NSLR, which detects nothing below 1°. | A second NSLR paramset. | `docs/handoffs/2026-09-27-nslr-built.md:240` |
| 24 | Hand-labelled blinks or raw eye video, and the rig's first recordings | Validate an adaptive blink pad. Re-measure the 50 ms and 80 ms flank pads, which were measured on the tutorial recording. | The blink pads in the validity mask. The adaptive pad is not built. | `docs/handoffs/2026-10-11-blink-padding.md:41`, `:51` |
| 25 | The lab's OpenIrisDPI computer recording raw eye video | That it records, and where the files land in the session folder. | Blink detection from the image. | `docs/pending-wl-xcon-amendments.md:139` |
| 26 | Rig geometry and real data | The plausible region (the recorded field plus 2°) and `max_speed_deg_s` (1000), both placeholders. | The eye-lost-data limits. | `docs/superpowers/specs/2026-11-06-eye-lost-data-and-region-design.md`; `docs/superpowers/specs/2026-08-31-saccade-detection-design.md:1343` |
| 27 | Real macaque DPI data | Whether fixational drift stays under 2 °/s after a 4 Hz low-pass, and `max_initial_saccade_freq`, which comes from humans watching a movie. | REMoDNaV's defaults. | `docs/superpowers/specs/2026-09-26-remodnav-design.md:795`, `:879` |
| 28 | Real rig data with a drifting eye | Why Otero-Millan found about 40 microsaccades on synthetic drift, against its band on the real recording. | Otero-Millan's behaviour on drift. | `docs/handoffs/2026-09-27-bmd-built.md:305` |
| 29 | wl-xcon's V3 bench | What OpenIris sends on a tracking failure. Its datagram has no blink or validity field. | wl-xcon's online detector, on its side. | `docs/superpowers/specs/2026-11-06-eye-lost-data-and-region-design.md:262` |
| 30 | A session that calibrates in MonkeyLogic | Which transport carries gaze into MonkeyLogic: OpenIrisDPI's UDP pixels, the ACCESIO voltage, or both. | Whether MonkeyLogic's saved calibration is in a space our pixel feature can use. The validation step is correct either way. | `docs/superpowers/specs/2026-08-30-eye-ohdpi-calibration-and-gaze-design.md:564` |

## The behaviour cameras

| # | Waits on | The check | What it settles | Source |
|---|---|---|---|---|
| 31 | The camera package (wl-camacq, registered, no repository yet) | Each video file's start and end on the camera computer's wall clock, with its synchronisation state, in the sidecar. | Telling a camera file that ran after a partial box stopped from a dead barcode cable (amendment 65's camera half). Until then a camera file with no barcode under the NI fails the session. | amendment 83 |
| 32 | The same package | Its real sidecar. Our contract still requires `trigger_source` `syncbox`, but wl-sync voided camera triggers on 2026-08-16: the cameras free-run and record the barcode line. Also `bcam`'s per-frame digital field names, and the optional `digital_line` and `frame_rate_hz` fields, which no one has told the camera project about. | `contracts/sidecar.py`, `extract_bcam` and bcam alignment, which is "specified and unavailable" until both fields are emitted. | amendment 83; `docs/superpowers/specs/2026-08-16-phase-1c4-timebase-design.md:425` |

## wl-xcon live sessions

| # | Waits on | The check | What it settles | Source |
|---|---|---|---|---|
| 33 | A live wl-xcon recording on build C7, with its words and clock files | Lab time (tier L) and the rig record's clock fit on a real session. wl-xcon has not measured the rig's clock. | Part 4. | spec 2026-10-18, `:685`, `:489` |
| 34 | A real wl-xcon session, or its simulated session folder | Measured runs, blocks and trials from the codes. | Runs and trials in the NWB on real data. | `docs/handoffs/2026-10-01-runs-and-trials.md:56` |

## The compute machine and the servers

| # | Waits on | The check | What it settles | Source |
|---|---|---|---|---|
| 35 | The compute machine (P6000, x86_64 Fedora, SELinux, GPU) | Phase 2b: the 2b-0 benchmark, then 2b-1's container seam, then 2b-2 to 2b-8. | Units, LFP and MUA in the canonical NWB. | `wl.yaml` `status.next`; `docs/superpowers/specs/2026-08-23-phase-2b-decomposition-design.md:194` |
| 36 | The compute machine | U'n'Eye's CPU inference time over a 1.18M-sample recording. | Whether U'n'Eye needs the `slow` test marker. | `docs/superpowers/specs/2026-08-31-saccade-detection-design.md:1347` |
| 37 | `serv` at 12 h | The SpikeGLX and Intan stages end to end at 12 h. The per-trial coverage writes, which took 26 min while another session held six cores, against a 10-minute line. | Batching the coverage writes; the stage memory target. | `docs/handoffs/2026-11-14-scaling-long-sessions.md:41`, `:55` |
| 38 | The real scratch device | The archival high-water mark, and whether the server gets the `wavpack-devel` library. | That value; the codec. | `docs/superpowers/specs/2026-08-27-archival-and-compression-design.md:512` |
| 39 | The lab host | Linux's errno 98 on a privileged port, and the 10 s `GET /health` timeout. The health walk holds the process-wide lock, so a slow NAS could make a healthy host look flaky. | The responder's timeout and the poll rate. | `docs/CHECKPOINT.md:2117`; `docs/handoffs/2026-08-16-phase-1c3-rulings.md:168` |

## Procurement and the board

| # | Waits on | The check | What it settles | Source |
|---|---|---|---|---|
| 40 | Ordering | The 12-13 week lead time for the NI PCIe-6343 and PXIe-6353. It was measured for the 6363. The cards were reported ordered on 2026-08-23. | Delivery against January 2027. | `docs/superpowers/specs/2026-08-12-wl-preproc-design.md:1369` |
| 41 | The breakout board, in layout at wl-sync on 2026-11-21 | The board's lead time, and the event-protocol bench test end to end. | Whether the board's schedule is real. Items 15-18, 15a, 16b and 17a wait on it. | `docs/handoffs/2026-08-13-syncbox-pcb-brief.md:105`, `:117`; wl-sync `wl.yaml` |

## Not judged by the sweep

- **Whether the sync box should trigger the eye tracker's frames** (`docs/CHECKPOINT.md:2102`; the 1c-4 spec `:339`).
  This is probably settled: wl-sync emits no camera trigger since 2026-08-16. Check with wl-sync before striking
  it.
- **The tolerance of the cross-check between the task PC's clock and the sync box's clock at ingest** (parent spec
  §13 item 5, "Blocking Phase 1"). Lab time and tier N may have replaced it.
- **`clock_trusted` and `clock_reason`**: whether anything reads them (`HANDOVER-wl-sync.md`).
- **Whether OpenIrisDPI surfaces Spinnaker's per-frame chunk data** (parent spec §13 item 2, "enhancement only").
