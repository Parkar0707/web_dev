# Squishy – Smart Video Compressor

**Documentation: user guide, how it works, and the mathematics behind it**

Squishy is a single-file desktop tool (`squishy.py`) written with the Python standard library and tkinter. It reads a video, works out the best encoding settings with a small amount of mathematics, and compresses it either to an **exact file size** or to a **chosen quality**. The encoding itself is done by the `ffmpeg` program; Squishy decides *what* to ask ffmpeg to do and shows it in an animated, outlined "cartoon" interface.

---

## Contents

1. [Requirements and installation](#1-requirements-and-installation)
2. [Quick start](#2-quick-start)
3. [User guide](#3-user-guide)
4. [Choosing settings (recipes)](#4-choosing-settings-recipes)
5. [How it works (pipeline)](#5-how-it-works-pipeline)
6. [The mathematics](#6-the-mathematics)
7. [The ffmpeg commands Squishy builds](#7-the-ffmpeg-commands-squishy-builds)
8. [Interface and animation maths](#8-interface-and-animation-maths)
9. [Limits and troubleshooting](#9-limits-and-troubleshooting)
10. [Code map and tuning](#10-code-map-and-tuning)
11. [Measured accuracy](#11-measured-accuracy)

---

## 1. Requirements and installation

| Need | Details |
|---|---|
| Python | 3.8 or newer, with **tkinter** (included with the python.org installers for Windows and macOS; on Debian/Ubuntu install `python3-tk`) |
| ffmpeg + ffprobe | Must be on your `PATH`, **or** placed next to `squishy.py` (also checked in a `bin/` folder beside it) |
| Python packages | **None.** Only the standard library is used |

Installing ffmpeg:

```text
Windows :  winget install ffmpeg
macOS   :  brew install ffmpeg
Linux   :  sudo apt install ffmpeg
```

If ffmpeg is missing, Squishy opens with an explanation card and a **Re-check** button, so you can install it and carry on without restarting.

---

## 2. Quick start

```bash
python squishy.py
```

1. Click **Choose video**.
2. Pick **Target size** (e.g. 16 MB) or **Quality**.
3. Click **Compress**.
4. When it finishes, click **Show in folder**.

The result is saved next to the original as `<name>_compressed.mp4`. The original is never modified.

---

## 3. User guide

### 3.1 The screen, top to bottom

| Area | What it shows / does |
|---|---|
| **Header** | Animated logo, app name, and a **status chip** with a coloured dot (grey = idle, teal = ready/finished, amber = working or a warning, red = error). |
| **File card** | File name, info pills (resolution, frame rate, duration, size, overall bitrate), the output file name, and the **Change** and **Save as…** buttons. |
| **Settings card** | Mode, presets, slider, codec, audio, speed, and (in Quality mode) resolution cap. |
| **Plan card** | What Squishy is about to do: output resolution, frame rate, codec, speed, the predicted size and percentage saved, and the video and audio bitrates. |
| **Action area** | **Compress** button; during work a progress bar with **Stop**; after work a before/after size comparison. |

### 3.2 Modes

**Target size** – "Make this file about X MB."
Use the slider (1 MB – 2000 MB, logarithmic) or a preset chip (8, 16, 25, 50, 100 MB). Squishy calculates the bitrate that fits the size, chooses a resolution and frame rate that the bitrate can support, and runs a **two-pass** encode so the result lands on the target. The resolution is chosen automatically.

**Quality** – "Keep it this good; the size follows."
Use the slider (0–100 %) or presets *Low / Medium / Good / High / Max*. Optionally cap the resolution (**Same**, **1080p**, **720p**, **480p**). The file size is *estimated* from a model; use **Estimate size** for a measured prediction.

### 3.3 Other options

| Option | Choices | Effect |
|---|---|---|
| **Codec** | H.264 · everywhere / H.265 · smaller | H.264 plays on practically everything. H.265 (HEVC) is roughly 30 % more efficient at the same quality but a few old devices cannot play it. Output is tagged `hvc1` so Apple devices accept it. |
| **Audio** | Sound on / Mute | Mute removes the audio track entirely (and gives its bitrate to the video in Target mode). |
| **Speed** | Fast / Balanced / Thorough | Maps to the encoder presets `veryfast` / `medium` / `slow`. Slower presets take longer but produce smaller files at the same quality (about 8 % and 13 % smaller than Fast). |
| **Resolution** (Quality mode only) | Same / 1080p / 720p / 480p | Caps the shorter side of the picture. Never enlarges a video. |

### 3.4 The plan card and status chip

The plan card is recalculated every frame as you change settings. The status chip explains anything unusual:

| Message | Meaning |
|---|---|
| *The video already fits that size – choose a smaller one!* | Your target is ≥ 97 % of the original size. **Compress** is disabled. |
| *That is too tiny for this video – pick a bigger size.* | The video bitrate left after audio would be below 40 kbps. **Compress** is disabled. |
| *Might not get smaller – try lower quality or a smaller resolution.* | In Quality mode the estimate is ≥ 97 % of the original (typical for videos that are already heavily compressed). |

### 3.5 Estimate size (Quality mode)

The formula-based estimate can be ±20 % off depending on how complex your footage is. **Estimate size** encodes three short clips (taken from 1/6, 1/2 and 5/6 of the way through the video) with your exact settings and measures them. The plan card then shows **measured ≈ …**. The estimate is discarded automatically if you change any setting.

### 3.6 During compression

* The progress bar shows a smoothed percentage; below it are the elapsed-time-based ETA, the encoder frame rate, the encoder speed multiple (e.g. `2.1x` = 2.1 × real time) and the pass number (`pass 1/2`, `pass 2/2`).
* All settings are locked while working.
* **Stop** cancels cleanly; the unfinished `.part` file is deleted and nothing is saved.
* The result is written to `<output>.part` first and renamed only when ffmpeg finishes successfully, so you never end up with a half-written file.

### 3.7 Keyboard shortcuts

| Key | Action |
|---|---|
| **Enter** | Compress |
| **Esc** | Stop |
| **Ctrl + O** | Choose video |
| **← / →** | Move the slider by 2 % |

### 3.8 Window size and displays

The interface is designed on a 760 × 790 grid and **scales** to the window (minimum window size 440 × 480). It enables Windows high-DPI awareness, uses large buttons suitable for touch screens, and centres itself on wide displays.

---

## 4. Choosing settings (recipes)

| Goal | Suggested settings |
|---|---|
| Send over a chat app with a size limit | Target size → the limit minus a small margin (e.g. 16 MB), H.264, Fast |
| Email attachment | Target size 25 MB (or your provider's limit), H.264 |
| Smallest file, newer devices only | Target size, H.265, Thorough |
| Keep it looking great, shrink moderately | Quality → *High* (≈ 75 %), H.264, Balanced |
| Phone-screen viewing only | Quality → *Medium*, resolution cap 720p |
| Archive a long recording | Quality → *Good*, H.265, Thorough, **Estimate size** first |

Tips:

* Target mode is **picky about tiny targets**. If the plan card shows a very low resolution (e.g. 426×240), the target is too small for the length of the video – raise it or shorten the clip.
* The Target default is set to **35 % of the original size** when a file is loaded.
* Videos already compressed by phones or screen recorders at low bitrates may not shrink much in Quality mode.

---

## 5. How it works (pipeline)

```text
 choose file
     │
     ▼
 ffprobe ──► width, height (rotation-aware), fps, duration, size,
     │       video bitrate, audio bitrate, audio channels
     ▼
 PLAN (pure maths, recomputed live)
     │   target mode : size → bitrate → audio/video split → fit resolution & fps
     │   quality mode: slider → CRF → bits-per-pixel model → estimated size
     ▼
 ffmpeg
     │   target mode : pass 1 (analysis, no output) → pass 2 (final encode)
     │   quality mode: single CRF pass
     ▼
 write  <name>_compressed.mp4.part  →  rename to  <name>_compressed.mp4
```

All heavy work runs in background threads. The interface thread only draws (about 30 frames per second) and reads messages from a queue (`probed`, `progress`, `done`, `fail`, `cancelled`, `pred`), so the window stays responsive.

---

## 6. The mathematics

### 6.0 Notation

| Symbol | Meaning |
|---|---|
| `T` | duration in seconds |
| `W, H` | picture width and height in pixels (after applying rotation metadata) |
| `s = min(W, H)` | the "short side" |
| `fps` | frames per second |
| `B_total, B_v, B_a` | total, video and audio bitrates in bits per second |
| `bpp` | **bits per pixel per frame**: `bpp = B_v / (W · H · fps)` |

`bpp` is the central quantity. It measures how many bits the encoder may spend on each pixel of each frame. Too few and the picture turns blocky; plenty and it looks clean.

### 6.1 Reading the video

* Frame rate comes as a fraction such as `30000/1001`, parsed as `30000 ÷ 1001 = 29.97`, and clamped to 1–120 fps.
* If the file carries a rotation of 90° or 270°, `W` and `H` are swapped so that all later maths uses the picture as viewers see it.
* If a stream bitrate is not reported, it is derived: `B_v = (size · 8 / T) − B_a`.

### 6.2 Target-size mode: the bitrate budget

A file is just bitrate multiplied by time, so the size target gives the bitrate directly.

```text
target_bytes = size_MB · 1024 · 1024

B_total = target_bytes · 8 · (1 − 0.04) / T
```

The factor `(1 − 0.04)` keeps **4 % in reserve** for container overhead and for the encoder not hitting its target exactly, so results land slightly *under* the target rather than over it.

**Audio share** (when audio is on):

```text
B_a = clamp(0.10 · B_total, 40 000, 160 000)   rounded to a multiple of 8 000
B_v = B_total − B_a
```

Audio gets 10 % of the budget, but never less than 40 kbps (speech would become muddy) or more than 160 kbps (beyond that listeners rarely notice). Muted → `B_a = 0` and all bits go to the picture.

**Feasibility checks:**

* If `B_v < 40 000` bps → plan is not allowed ("too tiny").
* If `target_bytes ≥ 0.97 · original_size` → plan is not allowed ("already fits").

### 6.3 Fitting the picture to the bitrate

Having `B_v`, Squishy asks: *can the current resolution and frame rate be encoded well at this bitrate?* "Well" is defined by a quality threshold `bpp_goal`:

| Codec | `bpp_goal` | Reason |
|---|---|---|
| H.264 | 0.060 | typical comfortable value for general footage |
| H.265 | 0.040 | roughly one-third more efficient |

Compute the current value:

```text
bpp_now = B_v / (W · H · fps)
```

If `bpp_now ≥ bpp_goal`, **nothing changes**.

Otherwise the picture is reduced in three steps:

**Step 1 – cap high frame rates.** If `fps > 30.5`, set `fps = 30` and recompute `bpp_now`. If that is enough, stop.

**Step 2 – shrink the picture.** The number of pixels per frame is proportional to the *square* of the linear scale `f`. To raise `bpp` from `bpp_now` to `bpp_goal` we need the pixel count to fall by `bpp_now / bpp_goal`:

```text
f² = bpp_now / bpp_goal      →      f = √(bpp_now / bpp_goal)
```

The ideal new short side is `s · f`. To keep sizes tidy it is snapped **down** to a standard ladder:

```text
2160, 1440, 1080, 720, 540, 480, 360, 288, 240, 180
```

and never above the original (no upscaling). The long side follows from the aspect ratio:

```text
f' = new_short / s
W' = even(W · f'),   H' = even(H · f')
```

where `even(x)` rounds to the nearest even integer (video encoders require even dimensions). The same rule handles portrait videos because it works on the *short* side.

**Step 3 – lower the frame rate if still starving.** After shrinking, if `bpp` is still below `0.6 · bpp_goal`, the frame rate drops to 24, and if still low, to 15 fps.

### 6.4 Two-pass rate control

For a size target a single pass is a gamble: the encoder does not know in advance which scenes are hard. Squishy therefore uses **two passes**:

1. **Pass 1** analyses the whole video (no output file) and records how difficult each part is.
2. **Pass 2** spends the bit budget `B_v` where it is needed (complex action gets more, still scenes get less), so the *average* hits the target.

The encoder is also given safety rails, `maxrate = 2.2 · B_v` and `bufsize = 4 · B_v`, so short spikes do not exceed what players can buffer.

Progress is combined across the passes with weights **0.3** and **0.7**, because the analysis pass is considerably faster:

```text
overall = 0.3 · f₁                 during pass 1
overall = 0.3 + 0.7 · f₂           during pass 2
```

### 6.5 Quality mode: slider → CRF

CRF ("constant rate factor") tells the encoder to hold a constant perceptual quality and let the bitrate vary. Lower CRF means higher quality. The slider value `q` (0–100) maps linearly:

```text
CRF = 18 + 0.16 · (100 − q)       (q = 100 → 18,   q = 50 → 26,   q = 0 → 34)
CRF += 1   if s ≥ 1440            (very large frames tolerate a bit more compression)
CRF += 5   if codec is H.265      (so that H.265 CRF 28 ≈ H.264 CRF 23 in quality)
```

### 6.6 Quality mode: estimating the size

The estimate rests on a well-known rule of thumb: **every +6 CRF roughly halves the bitrate** (the quantiser step size doubles every 6 steps). Anchored at "H.264 at CRF 23 ≈ 0.08 bits per pixel":

```text
bpp(CRF) = 0.08 · eff · 2^((23 + off − CRF) / 6)

eff = 1.00 (H.264), 0.68 (H.265)
off = 0    (H.264), 5    (H.265)
```

Then, for the output picture `W' × H'` at `fps'`:

```text
B_v = bpp(CRF) · W' · H' · fps'
```

Two sanity limits are applied:

* **Cannot exceed the source:** `B_v ≤ 0.9 · B_v(source) · (W'·H')/(W·H)`. Re-encoding a video that is already small rarely makes it bigger by a lot, so the area-scaled source bitrate is a ceiling.
* **Audio:** `B_a = clamp(source_audio_bitrate, 64 000, 128 000)` (rounded to 8 kbps steps), 0 if muted or absent.

Estimated file size:

```text
size ≈ (B_v + B_a) · T / 8 · (1 + 0.04 / 2)
```

If the estimate is ≥ 97 % of the original size, the status chip warns that the video "might not get smaller".

### 6.7 Measured estimate (sampling)

Because content complexity drives real bitrates, **Estimate size** measures instead of guessing.

```text
n = 3 clips, each of length  c = clamp(T / 6, 0.8, 2.5) seconds
clip i starts at   t_i = T · (i + 0.5) / n  −  c / 2        (i = 0, 1, 2)
```

Each clip is encoded at `veryfast` with the plan's exact CRF, resolution and frame rate, written as a raw H.264/HEVC stream (no container, so every byte counted is video). The measured bitrate:

```text
B̂_v = (1/n) · Σ  8 · bytes_i / c
```

Slower presets compress better than `veryfast`, so a correction `k` is applied (Fast 1.00, Balanced 0.92, Thorough 0.87):

```text
size ≈ (B̂_v · k + B_a) · T / 8 · 1.02
```

### 6.8 The size slider is logarithmic

A linear slider from 1 to 2000 MB would make everything useful (5–100 MB) fit in a sliver. A **logarithmic** slider gives equal travel to each doubling of size:

```text
size_MB = 2000^t          t ∈ [0, 1]       (t = 0 → 1 MB,  t = 1 → 2000 MB)
t = ln(size_MB) / ln(2000)                  (used by the preset chips)
```

The value is then rounded for readability: steps of 0.5 MB below 10 MB, 1 MB below 100 MB, and 5 MB above. When a video is loaded the slider starts at **35 % of the original size** (clamped to the 1–2000 MB range).

### 6.9 Progress and time left

ffmpeg reports the position in the video it has processed, `p` (in seconds). Then:

```text
fraction f = clamp(p / T, 0, 1)
```

The displayed progress glides toward the true value with exponential smoothing, so the bar moves smoothly:

```text
display ← display + 0.15 · (f − display)       (every frame, ~30 times a second)
```

Estimated time left, once at least 2 % is done:

```text
ETA = elapsed · (1 − f) / f
```

(The time per unit of progress so far, multiplied by the progress still to go.)

### 6.10 Worked example – Target size

Video: 12 s, 1280 × 720, 30 fps, 8.7 MB, with audio. Target: **2 MB**, H.264.

```text
B_total = 2 · 1 048 576 · 8 · 0.96 / 12      = 1 342 177 bps
B_a     = clamp(0.10 · 1 342 177) = 134 218  → rounded to 136 000 bps
B_v     = 1 342 177 − 136 000                = 1 206 177 bps

bpp_now = 1 206 177 / (1280 · 720 · 30)      = 0.0436      (< 0.060 goal)
fps ≤ 30, so no frame-rate change
f       = √(0.0436 / 0.060)                  = 0.853
ideal short side = 720 · 0.853               = 614  → snap down to ladder → 540
f'      = 540 / 720 = 0.75   →  W' × H' = 960 × 540
new bpp = 1 206 177 / (960 · 540 · 30)       = 0.0776      (comfortably above goal)
```

Plan: **960 × 540, 30 fps, video ≈ 1.21 Mbps + audio 136 kbps → ≈ 2 MB.** The real encode produced **1.92 MB**.

### 6.11 Worked example – Quality

Same video, Quality 60 %, H.264, resolution "Same".

```text
CRF   = 18 + 0.16 · (100 − 60)               = 24.4
bpp   = 0.08 · 2^((23 − 24.4) / 6)           = 0.0681
B_v   = 0.0681 · 1280 · 720 · 30             ≈ 1 881 700 bps
B_a   = 128 000 bps
size  ≈ (1 881 700 + 128 000) · 12 / 8 · 1.02 ≈ 3.07 MB  (2.93 MiB)
```

The formula said 2.93 MB; the real encode gave 3.53 MB; the **Estimate size** button measured **3.25 MB** – far closer, because it sees the actual footage.

---

## 7. The ffmpeg commands Squishy builds

Squishy runs ffmpeg directly (no shell), with hidden console windows on Windows.

**Target size, pass 1 (analysis):**

```bash
ffmpeg -hide_banner -y -loglevel error -nostdin -i INPUT -map 0:v:0 -sn -dn \
  -vf "scale=960:540:flags=lanczos,format=yuv420p" \
  -c:v libx264 -preset medium -b:v 1206177 -maxrate 2653589 -bufsize 4824708 \
  -pass 1 -passlogfile pass -profile:v high -an \
  -progress pipe:1 -nostats -f null /dev/null
```

**Target size, pass 2 (final):**

```bash
ffmpeg ... -map 0:v:0 -map 0:a:0? -sn -dn -vf "..." -c:v libx264 -preset medium \
  -b:v 1206177 -maxrate 2653589 -bufsize 4824708 -pass 2 -passlogfile pass \
  -profile:v high -c:a aac -b:a 136000 \
  -progress pipe:1 -nostats -movflags +faststart -f mp4 OUTPUT.part
```

**Quality mode (single pass):**

```bash
ffmpeg ... -vf "..." -c:v libx264 -preset medium -crf 24.4 -profile:v high \
  -c:a aac -b:a 128000 -movflags +faststart -f mp4 OUTPUT.part
```

Notes on the options:

| Option | Purpose |
|---|---|
| `scale=W:H:flags=lanczos` | high-quality resize (only added if the size changes) |
| `fps=N` | frame-rate reduction (only added if the frame rate changes by more than 0.5) |
| `format=yuv420p` | the pixel format every phone, TV and browser can decode |
| `-profile:v high` | widely compatible H.264 profile |
| `-tag:v hvc1` | lets Apple devices play H.265 in MP4 |
| `-movflags +faststart` | moves the index to the start so videos begin playing before fully downloaded |
| `-map 0:v:0 -map 0:a:0?` | first video track and (if present) first audio track only |
| `-sn -dn` | drop subtitles and data tracks |
| `-ac 2` | added when the source has more than 2 audio channels and the audio bitrate is under 160 kbps |
| `-progress pipe:1` | machine-readable progress that Squishy parses |
| `-passlogfile` / `stats=` | analysis file between passes, kept in a temporary folder that is deleted afterwards |

---

## 8. Interface and animation maths

The "cartoon" look uses simple, classic animation principles expressed as formulas. All run once per frame with a time value `t` (seconds).

| Effect | Formula / rule |
|---|---|
| **Responsive scale** | `scale = clamp(min(window_w / 760, window_h / 790), 0.5, 2.6)`; every size and font is multiplied by it, and the layout is centred. |
| **Squash and stretch (logo)** | `sx = 1 + k`, `sy = 1 − k`, so width grows as height shrinks (volume is preserved). `k = 0.14 · sin(9t)` while compressing, `0.04 · sin(2.2t)` when idle. |
| **Squeezing plates** | While working, two plates sit at `x = ± (r · sx + 0.2 r)`, following the block's width, so they appear to press it. |
| **Completion bounce** | `y_offset = −r · 0.55 · |sin(6 · Δt)| · e^(−0.8 · Δt)` – a hop that decays; plus two expanding rings whose radius is interpolated from `r` to `2.8 r` as they fade. |
| **Error shake** | Horizontal `± 2 · sin(28t)` for the first 0.6 s of every 3 s. |
| **Button feedback** | Hover: grows by 2 px and lifts 2 px. Pressed: shrinks 1.5 px and moves 3 px toward its hard shadow. |
| **Slider knob** | Radius `15 + 0.8 · sin(4t)`, +3 while hovered or dragged. |
| **Progress stripes** | Diagonal bars repeating every 34 px, offset by `(36 · t) mod 34` so they march continuously. |
| **Background** | A subtle grid drifting at 7 px/s and three large soft circles moving on slow sine/cosine paths. |
| **Smooth progress** | Exponential smoothing (see 6.9). |
| **Rounded corners** | A rounded rectangle is a 12-point polygon drawn with Tk's `smooth` option, where doubled corner points produce the curve. |

Interactions are handled by **hit-testing**: each frame the buttons register their rectangles; a click is accepted only if the mouse is released over the same button where it was pressed, and the slider also handles dragging.

---

## 9. Limits and troubleshooting

| Situation | Explanation / fix |
|---|---|
| "ffmpeg was not found" | Install ffmpeg (section 1) or place `ffmpeg` and `ffprobe` next to `squishy.py`, then click **Re-check**. |
| Output is much smaller resolution than expected | In Target mode the resolution follows from the bitrate (section 6.3). Raise the target size, or use Quality mode with a resolution cap. |
| Size is a little under the target | Intentional: 4 % is held in reserve (section 6.2). |
| Size is slightly over the target | Two-pass is accurate but not exact, particularly on short or very uneven videos; leave a few percent of margin under a hard limit. |
| "Already fits that size" | The original is already about that small. Lower the target. |
| Video does not shrink in Quality mode | It was already heavily compressed. Lower the quality or cap the resolution. |
| H.265 file will not play | The device or player predates HEVC support. Use H.264. |
| Compression is slow | Use **Fast** speed and H.264; H.265 + Thorough can be many times slower than real time on long videos. |
| Colours look different on HDR videos | HDR tone-mapping is not implemented; HDR sources are converted straight to 8-bit `yuv420p`. |
| Only one audio track kept | Only the first audio stream is copied; other audio tracks, subtitles and data tracks are dropped. |
| Variable-frame-rate phone clips | Handled using the average frame rate; frame-rate reduction uses ffmpeg's `fps` filter. |
| No drag-and-drop | Plain tkinter has no file drop support; use **Choose video** or Ctrl + O. |
| Text looks slightly different on your computer | The interface picks the first available of Segoe UI, SF Pro Text, Helvetica Neue, Inter, Roboto, Ubuntu, Cantarell, DejaVu Sans, Arial, Helvetica. |

---

## 10. Code map and tuning

`squishy.py` is organised in clear sections:

| Section | Contents |
|---|---|
| **Math engine** | Constants (`OVERHEAD`, `MIN_VBPS`, `LADDER`, `CODECS`, `PRESETS`) and the functions `audio_budget`, `pick_geometry`, `crf_from_quality`, `estimate_bpp`, `make_plan`, `vf_chain`, `build_cmd`. These are pure functions with no interface code, so they are easy to test. |
| **Helpers** | Tool discovery, DPI awareness, size/time/bitrate formatting, `probe()` (ffprobe → dictionary). |
| **Theme / drawing** | Colour constants, `mix`, `lum`, `rrect` (rounded rectangles). |
| **App class** | Events and hit-testing; the background job (`worker`, `run_ffmpeg`); sampling (`predict_worker`); message polling; and all drawing methods (`draw_header`, `draw_file_card`, `draw_settings`, `draw_plan`, `draw_action`, `draw_logo`, …). |

Values you may wish to tune (all at the top of the file):

| Constant | Default | Effect |
|---|---|---|
| `OVERHEAD` | `0.04` | Safety reserve in Target mode. Lower → closer to (or over) the target. |
| `MIN_VBPS` | `40 000` | Smallest allowed video bitrate. |
| `CODECS[...]["bpp"]` | `0.060` / `0.040` | Quality threshold used to decide when to shrink. **Raise** it for sharper but smaller-resolution results; **lower** it to keep resolution at the cost of blockiness. |
| `LADDER` | 2160 … 180 | The resolutions Squishy may choose in Target mode. |
| `PRESETS` | veryfast / medium / slow | Encoder speed levels and their size-correction factors. |
| `crf_from_quality` | `18 + 0.16·(100 − q)` | The quality scale. |
| `audio_budget` | `10 %`, 40–160 kbps | Audio share in Target mode. |

---

## 11. Measured accuracy

Test video: 12 s, 1280 × 720, 30 fps, 8.7 MB, with audio (a synthetic test pattern).

| Test | Estimated | Actual |
|---|---|---|
| Target 2 MB, H.264 (resized to 960 × 540) | 1.96 MB | **1.92 MB** |
| Target 1 MB, H.265 (resized to 854 × 480) | 0.98 MB | **0.97 MB** |
| Target 3 MB, H.264, muted | 2.94 MB | **2.88 MB** |
| Quality 60 %, H.264 – formula estimate | 2.93 MB | 3.53 MB |
| Quality 60 %, H.264 – **measured** by Estimate size | 3.25 MB | 3.21 MB (actual re-encode at Fast) |

Target-size mode landed within about **2–4 % under** the request. Formula estimates in Quality mode can be off by roughly ±20 % because real bitrate depends on footage; the sampled estimate was within about 1 %. Results on real-world footage will differ from this synthetic clip, which is why a few percent of margin is wise when you must stay under a hard limit.

---

*Squishy uses ffmpeg (libx264 / libx265 / AAC) for encoding. Please make sure you have the right to compress and distribute the videos you process.*
