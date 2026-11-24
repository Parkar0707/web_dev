#!/usr/bin/env python3
"""
Comp-1 - a smart video compressor.

Python standard library + tkinter only.  The actual H.264/H.265 encoding is done
by the `ffmpeg` / `ffprobe` programs (not Python packages), and everything that
makes it "smart" is plain mathematics in this file:

  * target-size mode : bitrate = size * 8 / duration, split between audio and video,
                       then resolution / fps picked from a bits-per-pixel budget,
                       then a real two-pass encode so the file lands on the target.
  * quality mode     : slider -> CRF, size estimated from a bits-per-pixel model
                       (every +6 CRF halves the bitrate), optional sample-encode
                       "Guess size" that measures 3 short clips for a better estimate.

Run:   python compressor.py
Needs: ffmpeg + ffprobe on PATH (or next to this file).
"""
import os
import sys
import math
import json
import time
import queue
import shutil
import tempfile
import threading
import subprocess
import tkinter as tk
from tkinter import filedialog, font as tkfont

# --------------------------------------------------------------------------------------
#  MATH ENGINE
# --------------------------------------------------------------------------------------
OVERHEAD = 0.04          # container + rate-control slack kept free (aim slightly under target)
MIN_VBPS = 40_000        # below this video bitrate the result would be unwatchable
MB = 1024 * 1024
LADDER = [2160, 1440, 1080, 720, 540, 480, 360, 288, 240, 180]   # short-side heights

CODECS = {
    # bpp   : bits-per-pixel-per-frame we consider "good enough" for this codec
    # crf_off: CRF offset so that quality is comparable between codecs (x265 28 ~ x264 23)
    # eff   : relative efficiency of the bitrate model
    "h264": dict(lib="libx264", label="H.264", bpp=0.060, crf_off=0, eff=1.00, raw="h264"),
    "h265": dict(lib="libx265", label="H.265", bpp=0.040, crf_off=5, eff=0.68, raw="hevc"),
}
PRESETS = {  # ffmpeg preset, size factor relative to "veryfast"
    "fast": ("veryfast", 1.00),
    "balanced": ("medium", 0.92),
    "thorough": ("slow", 0.87),
}


def clamp(x, a, b):
    return max(a, min(b, x))


def lerp(a, b, t):
    return a + (b - a) * t


def even(n):
    return max(2, int(round(n / 2.0)) * 2)


def parse_fraction(s):
    try:
        a, b = str(s).split("/")
        b = float(b)
        return float(a) / b if b else 0.0
    except Exception:
        try:
            return float(s)
        except Exception:
            return 0.0


def audio_budget(total_bps, has_audio):
    """Audio gets ~10 % of the total budget, clamped to 40..160 kbps, in 8 kbps steps."""
    if not has_audio:
        return 0
    a = clamp(total_bps * 0.10, 40_000, 160_000)
    return int(round(a / 8000.0) * 8000)


def pick_geometry(w, h, fps, video_bps, codec_key):
    """
    Choose output width/height/fps so the video bitrate still gives about `bpp` bits per
    pixel per frame.  If bits are plentiful nothing changes.  Otherwise:
        1) cap high frame rates at 30,
        2) shrink linearly by f = sqrt(bpp_now / bpp_goal)   (area scales with f^2),
           snapped *down* to a standard ladder height (short side),
        3) if still starving at the bottom of the ladder, step fps down to 24 then 15.
    """
    goal = CODECS[codec_key]["bpp"]
    fps_out = fps if fps > 0 else 30.0
    short = min(w, h)
    cur = video_bps / (w * h * fps_out)
    if cur >= goal:
        return w, h, fps_out
    if fps_out > 30.5:
        fps_out = 30.0
        cur = video_bps / (w * h * fps_out)
        if cur >= goal:
            return w, h, fps_out
    f = math.sqrt(cur / goal)
    target_short = short * f
    new_short = next((L for L in LADDER if L <= target_short), LADDER[-1])
    new_short = min(new_short, short)
    f2 = new_short / float(short)
    nw, nh = (w, h) if new_short == short else (even(w * f2), even(h * f2))
    cur2 = video_bps / (nw * nh * fps_out)
    for lower in (24.0, 15.0):
        if cur2 < goal * 0.6 and fps_out > lower:
            fps_out = lower
            cur2 = video_bps / (nw * nh * fps_out)
    return nw, nh, fps_out


def crf_from_quality(q, codec_key, short):
    """q in 0..100  ->  CRF (18 at q=100 ... 34 at q=0), big frames tolerate +1."""
    crf = 18 + (100 - q) * 0.16
    if short >= 1440:
        crf += 1
    crf += CODECS[codec_key]["crf_off"]
    return round(crf, 1)


def estimate_bpp(crf, codec_key):
    """Bits per pixel per frame at a given CRF: 0.08 at x264 CRF 23, halves every +6 CRF."""
    c = CODECS[codec_key]
    return 0.08 * c["eff"] * 2 ** ((23 + c["crf_off"] - crf) / 6.0)


def make_plan(info, st):
    """All the maths in one place. Returns a dict describing what will be encoded."""
    dur = max(info["duration"], 0.1)
    has_a = info["has_audio"] and st["audio"]
    w, h, fps = info["w"], info["h"], info["fps"]
    plan = dict(mode=st["mode"], codec=st["codec"], preset=PRESETS[st["speed"]][0],
                note="", ok=True, crf=None, audio=has_a, channels=info.get("channels", 2))
    if st["mode"] == "target":
        tbytes = st["size_mb"] * MB
        total_bps = tbytes * 8 * (1 - OVERHEAD) / dur
        abps = audio_budget(total_bps, has_a)
        vbps = total_bps - abps
        nw, nh, nfps = pick_geometry(w, h, fps, vbps, st["codec"])
        est = tbytes * (1 - OVERHEAD / 2)
        if vbps < MIN_VBPS:
            plan["ok"] = False
            plan["note"] = "That is too tiny for this video - pick a bigger size."
        elif tbytes >= info["size"] * 0.97:
            plan["ok"] = False
            plan["note"] = "The video already fits that size - choose a smaller one!"
    else:
        q = st["q"]
        cap = st["res"]
        short = min(w, h)
        nw, nh, nfps = w, h, fps
        if cap and short > cap:
            f = cap / float(short)
            nw, nh = even(w * f), even(h * f)
        crf = crf_from_quality(q, st["codec"], min(nw, nh))
        plan["crf"] = crf
        vbps = estimate_bpp(crf, st["codec"]) * nw * nh * nfps
        if info["v_bps"]:
            vbps = min(vbps, 0.9 * info["v_bps"] * (nw * nh) / float(w * h))
        abps = 0
        if has_a:
            abps = int(round(clamp(info["a_bps"] or 128_000, 64_000, 128_000) / 8000.0) * 8000)
        est = (vbps + abps) * dur / 8.0 * (1 + OVERHEAD / 2)
        if est >= info["size"] * 0.97:
            plan["note"] = "Might not get smaller - try lower quality or a smaller resolution."
    plan.update(w=nw, h=nh, fps=nfps, vbps=int(max(vbps, 0)), abps=int(abps), est=est)
    return plan


def vf_chain(plan, info):
    vf = []
    if (plan["w"], plan["h"]) != (info["w"], info["h"]):
        vf.append("scale=%d:%d:flags=lanczos" % (plan["w"], plan["h"]))
    if abs(plan["fps"] - info["fps"]) > 0.5:
        vf.append("fps=%g" % plan["fps"])
    vf.append("format=yuv420p")
    return ",".join(vf)


def build_cmd(ffmpeg, src, out, plan, info, pass_no, passes):
    """One ffmpeg command line.  Run with cwd = temp dir (pass-log files live there)."""
    c = CODECS[plan["codec"]]
    last = pass_no == passes
    cmd = [ffmpeg, "-hide_banner", "-y", "-loglevel", "error", "-nostdin", "-i", src, "-map", "0:v:0"]
    if last and plan["audio"]:
        cmd += ["-map", "0:a:0?"]
    cmd += ["-sn", "-dn", "-vf", vf_chain(plan, info), "-c:v", c["lib"], "-preset", plan["preset"]]
    x265 = []
    if plan["mode"] == "target":
        vb = plan["vbps"]
        cmd += ["-b:v", str(vb), "-maxrate", str(int(vb * 2.2)), "-bufsize", str(int(vb * 4))]
        if plan["codec"] == "h264":
            cmd += ["-pass", str(pass_no), "-passlogfile", "pass"]
        else:
            x265.append("pass=%d:stats=pass.log" % pass_no)
    else:
        cmd += ["-crf", str(plan["crf"])]
    if plan["codec"] == "h264":
        cmd += ["-profile:v", "high"]
    else:
        cmd += ["-tag:v", "hvc1", "-x265-params", ":".join(x265 + ["log-level=error"])]
    if last and plan["audio"]:
        cmd += ["-c:a", "aac", "-b:a", str(plan["abps"])]
        if plan["abps"] < 160_000 and plan.get("channels", 2) > 2:
            cmd += ["-ac", "2"]
    else:
        cmd += ["-an"]
    cmd += ["-progress", "pipe:1", "-nostats"]
    if last:
        cmd += ["-movflags", "+faststart", "-f", "mp4", out]
    else:
        cmd += ["-f", "null", os.devnull]
    return cmd


# --------------------------------------------------------------------------------------
#  HELPERS
# --------------------------------------------------------------------------------------
def enable_dpi():
    if sys.platform.startswith("win"):
        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def find_tool(name):
    exe = name + (".exe" if os.name == "nt" else "")
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, exe), os.path.join(here, "bin", exe)):
        if os.path.isfile(p):
            return p
    return shutil.which(name)


def popen_flags():
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def fmt_size(b):
    if b >= 1024 * MB:
        return "%.2f GB" % (b / (1024.0 * MB))
    if b >= 10 * MB:
        return "%.0f MB" % (b / MB)
    if b >= MB:
        return "%.1f MB" % (b / MB)
    return "%.0f KB" % (b / 1024.0)


def fmt_dur(s):
    s = int(round(s))
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return "%d:%02d:%02d" % (h, m, sec) if h else "%d:%02d" % (m, sec)


def fmt_rate(bps):
    return "%.1f Mbps" % (bps / 1e6) if bps >= 1e6 else "%.0f kbps" % (bps / 1e3)


def default_out(src):
    base, _ = os.path.splitext(src)
    cand, n = base + "_compressed.mp4", 2
    while os.path.exists(cand):
        cand = "%s_compressed%d.mp4" % (base, n)
        n += 1
    return cand


def probe(ffprobe, path):
    r = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format",
                        "-show_streams", path], capture_output=True, text=True, timeout=40,
                       creationflags=popen_flags(), errors="replace")
    data = json.loads(r.stdout or "{}")
    streams = data.get("streams", [])
    vs = [s for s in streams if s.get("codec_type") == "video"
          and not s.get("disposition", {}).get("attached_pic")]
    if not vs:
        raise ValueError("I could not find a video stream in that file.")
    v = vs[0]
    aus = [s for s in streams if s.get("codec_type") == "audio"]
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    try:
        rot = int(float(v.get("tags", {}).get("rotate", 0)))
    except Exception:
        pass
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    if abs(rot) % 180 == 90:
        w, h = h, w
    fps = parse_fraction(v.get("avg_frame_rate")) or parse_fraction(v.get("r_frame_rate")) or 30.0
    fps = clamp(fps, 1.0, 120.0)
    fmt = data.get("format", {})
    dur = float(fmt.get("duration") or v.get("duration") or 0)
    if dur <= 0:
        raise ValueError("I could not work out how long that video is.")
    size = int(fmt.get("size") or os.path.getsize(path))
    total_bps = float(fmt.get("bit_rate") or size * 8 / dur)
    a_bps = float(aus[0].get("bit_rate") or 0) if aus else 0.0
    v_bps = float(v.get("bit_rate") or 0) or max(total_bps - a_bps, 1.0)
    return dict(path=path, name=os.path.basename(path), w=w, h=h, fps=fps, duration=dur,
                size=size, v_bps=v_bps, a_bps=a_bps, has_audio=bool(aus),
                channels=int(aus[0].get("channels", 2)) if aus else 0, total_bps=total_bps)


class Cancelled(Exception):
    pass


# --------------------------------------------------------------------------------------
#  THEME / DRAWING HELPERS   (bold ink outlines + hard offset shadows = the "cartoon" look,
#  kept on a calm slate / indigo palette)
# --------------------------------------------------------------------------------------
INK = "#141B34"          # outlines and primary text
MUTED = "#5B6482"
BG1, BG2 = "#F3F5FC", "#DCE4F5"
CARD = "#FFFFFF"
ACCENT = "#4F46E5"       # indigo: primary actions and selections
TEAL = "#0FB5A3"        # success / progress
AMBER = "#F59E0B"        # working / warnings
RED = "#E5484D"
SOFT = ["#E0E7FF", "#CCFBF1", "#FEF3C7"]
GRAY = "#D5DAEA"
IDLE = "#9AA3C2"
DESIGN_W, DESIGN_H = 760.0, 790.0


def hex2rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def mix(c1, c2, t):
    a, b = hex2rgb(c1), hex2rgb(c2)
    return "#%02x%02x%02x" % tuple(int(lerp(x, y, t)) for x, y in zip(a, b))


def lum(c):
    r, g, b = hex2rgb(c)
    return 0.299 * r + 0.587 * g + 0.114 * b


def rrect(cv, x1, y1, x2, y2, r, **kw):
    r = max(1, min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
           x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


# --------------------------------------------------------------------------------------
#  APP
# --------------------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("Comp-1 - smart video compressor")
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = int(min(800, sw * 0.92)), int(min(840, sh * 0.9))
        root.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, max(0, (sh - h) // 3)))
        root.minsize(440, 480)
        self.cv = tk.Canvas(root, highlightthickness=0, bd=0, bg=BG1)
        self.cv.pack(fill="both", expand=True)
        fams = set(tkfont.families())
        self.fam = next((f for f in ("Segoe UI", "SF Pro Text", "Helvetica Neue", "Inter", "Roboto", "Ubuntu",
                                     "Cantarell", "DejaVu Sans", "Arial", "Helvetica")
                         if f in fams), "TkDefaultFont")
        self.fonts = {}
        self.q = queue.Queue()
        self.hits, self.hover, self.pressed, self.drag = [], None, None, False
        self.mx = self.my = 0
        self.s, self.ox, self.oy = 1.0, 0.0, 0.0
        self.t, self.last = 0.0, time.perf_counter()
        self.slider_geo = (0, 1)
        self.stage = "empty"
        self.mood = "idle"
        self.body_col = ACCENT
        self.done_t = -99.0
        self.info = None
        self.plan = None
        self.pred = None
        self.predicting = False
        self.out_path = ""
        self.err = ""
        self.flash = ("", 0.0)
        self.job = {}
        self.proc = None
        self.cancel_flag = False
        self.result = None
        self.st = dict(mode="target", t=math.log(16) / math.log(2000), qt=0.55, codec="h264",
                       speed="balanced", audio=True, res=0)
        self.ffmpeg = self.ffprobe = None
        self.find_tools()

        cv = self.cv
        cv.bind("<Motion>", self.on_motion)
        cv.bind("<ButtonPress-1>", self.on_press)
        cv.bind("<ButtonRelease-1>", self.on_release)
        root.bind("<Return>", lambda e: self.click("go"))
        root.bind("<Escape>", lambda e: self.click("stop"))
        root.bind("<Control-o>", lambda e: self.click("pick"))
        root.bind("<Left>", lambda e: self.nudge(-0.02))
        root.bind("<Right>", lambda e: self.nudge(0.02))
        self.frame()

    # ------------------------------------------------------------------ tools / state
    def find_tools(self):
        self.ffmpeg, self.ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
        if self.ffmpeg and self.ffprobe:
            if self.stage == "notools":
                self.stage = "empty"
        else:
            self.stage = "notools"

    def size_mb(self):
        mb = 1.0 * (2000.0 / 1.0) ** self.st["t"]
        mb = round(mb * 2) / 2 if mb < 10 else (round(mb) if mb < 100 else round(mb / 5) * 5)
        return max(1.0, float(mb))

    def quality(self):
        return int(round(self.st["qt"] * 100))

    def settings(self):
        d = dict(self.st)
        d["size_mb"] = self.size_mb()
        d["q"] = self.quality()
        return d

    def nudge(self, d):
        key = "t" if self.st["mode"] == "target" else "qt"
        self.st[key] = clamp(self.st[key] + d, 0.0, 1.0)

    def set_slider(self, x):
        x1, x2 = self.slider_geo
        t = clamp((x - x1) / float(x2 - x1), 0.0, 1.0)
        self.st["t" if self.st["mode"] == "target" else "qt"] = t

    def plan_sig(self, p):
        return (p["mode"], p["w"], p["h"], round(p["fps"], 2), p["crf"], p["codec"], p["preset"])

    # ------------------------------------------------------------------ events
    def hit(self, x, y):
        for (x1, y1, x2, y2, i) in reversed(self.hits):
            if x1 <= x <= x2 and y1 <= y <= y2:
                return i
        return None

    def on_motion(self, e):
        self.mx, self.my = e.x, e.y
        self.hover = self.hit(e.x, e.y)
        self.cv.config(cursor="hand2" if self.hover else "")
        if self.drag:
            self.set_slider(e.x)

    def on_press(self, e):
        self.pressed = self.hit(e.x, e.y)
        if self.pressed == "slider":
            self.drag = True
            self.set_slider(e.x)

    def on_release(self, e):
        h = self.hit(e.x, e.y)
        if self.pressed and self.pressed == h and h != "slider":
            self.click(h)
        self.pressed, self.drag = None, False

    def click(self, i):
        locked = self.stage == "working"
        if i == "pick" and not locked:
            self.pick_file()
        elif i == "saveas" and self.info and not locked:
            p = filedialog.asksaveasfilename(title="Save compressed video as", defaultextension=".mp4",
                                             initialfile=os.path.basename(self.out_path),
                                             initialdir=os.path.dirname(self.out_path),
                                             filetypes=[("MP4 video", "*.mp4")])
            if p:
                self.out_path = p
        elif i and i.startswith("mode:") and not locked:
            self.st["mode"] = i[5:]
        elif i and i.startswith("pre:") and not locked:
            v = float(i[4:])
            if self.st["mode"] == "target":
                self.st["t"] = clamp(math.log(v) / math.log(2000.0), 0, 1)
            else:
                self.st["qt"] = v / 100.0
        elif i and i.startswith("codec:") and not locked:
            self.st["codec"] = i[6:]
        elif i and i.startswith("speed:") and not locked:
            self.st["speed"] = i[6:]
        elif i and i.startswith("audio:") and not locked:
            self.st["audio"] = i[6:] == "on"
        elif i and i.startswith("res:") and not locked:
            self.st["res"] = int(i[4:])
        elif i == "go":
            self.start_job()
        elif i == "stop" and self.stage == "working":
            self.cancel_flag = True
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
        elif i == "predict":
            self.start_predict()
        elif i == "open":
            self.open_folder()
        elif i == "again":
            self.stage = "ready"
        elif i == "recheck":
            self.find_tools()

    def open_folder(self):
        p = self.out_path
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select," + os.path.normpath(p)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", p])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(p)])
        except Exception:
            pass

    # ------------------------------------------------------------------ file + jobs
    def pick_file(self):
        p = filedialog.askopenfilename(
            title="Pick a video",
            filetypes=[("Videos", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.wmv *.flv *.mts *.m2ts *.3gp *.mpg *.mpeg"),
                       ("All files", "*.*")])
        if p:
            self.stage = "probing"
            threading.Thread(target=self.probe_worker, args=(p,), daemon=True).start()

    def probe_worker(self, p):
        try:
            self.q.put(("probed", probe(self.ffprobe, p)))
        except Exception as e:
            self.q.put(("probe_err", str(e) or "Could not read that file."))

    def start_job(self):
        if self.stage != "ready" or not self.plan or not self.plan["ok"]:
            return
        plan, info = dict(self.plan), self.info
        dst = self.out_path
        if os.path.abspath(dst) == os.path.abspath(info["path"]):
            dst = self.out_path = default_out(info["path"])
        passes = 2 if plan["mode"] == "target" else 1
        self.job = dict(frac=0.0, disp=0.0, fps=0.0, speed="", pass_no=1, passes=passes, start=time.time())
        self.cancel_flag = False
        self.stage = "working"
        threading.Thread(target=self.worker, args=(plan, info, dst, passes), daemon=True).start()

    def run_ffmpeg(self, cmd, cwd, duration, cb):
        known = {"frame", "fps", "stream_0_0_q", "bitrate", "total_size", "out_time_us", "out_time_ms",
                 "out_time", "dup_frames", "drop_frames", "speed", "progress"}
        self.proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, errors="replace", bufsize=1, creationflags=popen_flags())
        tail, fps, speed = [], 0.0, ""
        for line in self.proc.stdout:
            line = line.strip()
            k, _, v = line.partition("=")
            if k in known:
                if k in ("out_time_us", "out_time_ms") and v.lstrip("-").isdigit():
                    cb(clamp(int(v) / 1e6 / duration, 0.0, 1.0), fps, speed)
                elif k == "fps":
                    try:
                        fps = float(v)
                    except ValueError:
                        pass
                elif k == "speed":
                    speed = v
            elif line:
                tail.append(line)
        rc = self.proc.wait()
        if self.cancel_flag:
            raise Cancelled()
        if rc != 0:
            raise RuntimeError("\n".join(tail[-5:]) or "ffmpeg stopped with code %d" % rc)

    def worker(self, plan, info, dst, passes):
        tmp = tempfile.mkdtemp(prefix="compressor_")
        part = dst + ".part"
        try:
            weights = [0.3, 0.7] if passes == 2 else [1.0]
            base = 0.0
            for p in range(1, passes + 1):
                cmd = build_cmd(self.ffmpeg, os.path.abspath(info["path"]), os.path.abspath(part),
                                plan, info, p, passes)
                self.run_ffmpeg(cmd, tmp, info["duration"],
                                lambda f, fps, sp, p=p, b=base: self.q.put(("progress", b + weights[p - 1] * f, fps, sp, p)))
                base += weights[p - 1]
            os.replace(part, dst)
            self.q.put(("done", os.path.getsize(dst)))
        except Cancelled:
            self.q.put(("cancelled",))
        except Exception as e:
            self.q.put(("fail", str(e)))
        finally:
            try:
                if os.path.exists(part):
                    os.remove(part)
            except OSError:
                pass
            shutil.rmtree(tmp, ignore_errors=True)

    def start_predict(self):
        if self.predicting or self.stage != "ready" or not self.plan or self.plan["mode"] != "quality":
            return
        self.predicting = True
        threading.Thread(target=self.predict_worker, args=(dict(self.plan), self.info), daemon=True).start()

    def predict_worker(self, plan, info):
        tmp = tempfile.mkdtemp(prefix="compressor_p_")
        sig = self.plan_sig(plan)
        try:
            n = 3
            clip = clamp(info["duration"] / (n * 2.0), 0.8, 2.5)
            c = CODECS[plan["codec"]]
            total = 0.0
            for i in range(n):
                ss = max(0.0, info["duration"] * (i + 0.5) / n - clip / 2)
                out = os.path.join(tmp, "s%d.%s" % (i, c["raw"]))
                cmd = [self.ffmpeg, "-hide_banner", "-y", "-loglevel", "error", "-nostdin", "-ss", "%.2f" % ss,
                       "-i", os.path.abspath(info["path"]), "-t", "%.2f" % clip, "-map", "0:v:0", "-an", "-sn",
                       "-dn", "-vf", vf_chain(plan, info), "-c:v", c["lib"], "-preset", "veryfast",
                       "-crf", str(plan["crf"]), "-f", c["raw"], out]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                                   creationflags=popen_flags(), errors="replace")
                if r.returncode != 0:
                    raise RuntimeError(r.stderr.strip()[-200:] or "sample encode failed")
                total += os.path.getsize(out) * 8.0 / clip
            vbps = total / n * dict(PRESETS_BY_NAME)[plan["preset"]]
            est = (vbps + plan["abps"]) * info["duration"] / 8.0 * (1 + OVERHEAD / 2)
            self.q.put(("pred", sig, est))
        except Exception as e:
            self.q.put(("pred_err", str(e)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def poll(self):
        while True:
            try:
                m = self.q.get_nowait()
            except queue.Empty:
                break
            k = m[0]
            if k == "probed":
                info = m[1]
                self.info, self.out_path, self.pred = info, default_out(info["path"]), None
                mb = clamp(info["size"] / MB * 0.35, 1.0, 2000.0)
                self.st["t"] = clamp(math.log(mb) / math.log(2000.0), 0, 1)
                self.stage = "ready"
            elif k == "probe_err":
                self.err, self.stage = m[1], "error"
                self.info = None
            elif k == "progress":
                j = self.job
                j["frac"], j["fps"], j["speed"], j["pass_no"] = m[1], m[2], m[3], m[4]
            elif k == "done":
                self.result = m[1]
                self.stage, self.done_t = "done", self.t
                self.job["disp"] = 1.0
            elif k == "cancelled":
                self.stage = "ready"
                self.flash = ("Stopped! Nothing was saved.", time.time() + 4)
            elif k == "fail":
                self.err, self.stage = m[1], "error"
            elif k == "pred":
                self.predicting = False
                self.pred = (m[1], m[2])
            elif k == "pred_err":
                self.predicting = False
                self.flash = ("Test bite failed: " + m[1][:60], time.time() + 5)

    # ------------------------------------------------------------------ text helpers
    def font(self, px, bold=True):
        px = max(8, int(px))
        key = (px, bold)
        if key not in self.fonts:
            self.fonts[key] = tkfont.Font(family=self.fam, size=-px, weight="bold" if bold else "normal")
        return self.fonts[key]

    def elide(self, text, f, maxw):
        if f.measure(text) <= maxw:
            return text
        for n in range(len(text) - 1, 3, -1):
            hd = (n + 1) // 2
            tl = n - hd
            c = text[:hd] + "…" + (text[len(text) - tl:] if tl > 0 else "")
            if f.measure(c) <= maxw:
                return c
        return "…"

    def txt(self, x, y, text, px, fill=INK, anchor="center", maxw=None, bold=True, **kw):
        f = self.font(px * self.s, bold)
        if maxw:
            text = self.elide(text, f, maxw)
        return self.cv.create_text(x, y, text=text, font=f, fill=fill, anchor=anchor, **kw)

    def X(self, x):
        return self.ox + x * self.s

    def Y(self, y):
        return self.oy + y * self.s

    # ------------------------------------------------------------------ widgets
    def card(self, x1, y1, x2, y2, fill=CARD):
        s = self.s
        X, Y = self.X, self.Y
        rrect(self.cv, X(x1) + 4 * s, Y(y1) + 5 * s, X(x2) + 4 * s, Y(y2) + 5 * s, 18 * s, fill=INK, outline=INK)
        rrect(self.cv, X(x1), Y(y1), X(x2), Y(y2), 18 * s, fill=fill, outline=INK, width=max(2, 2.6 * s))

    def button(self, i, x1, y1, x2, y2, label, fill, px=16, enabled=True, active=True, pulse=0.0, tcol=None):
        """x/y in design units. active=False gives the plain white (unselected / secondary) look."""
        s, cv = self.s, self.cv
        a, b, c, d = self.X(x1), self.Y(y1), self.X(x2), self.Y(y2)
        hov = enabled and self.hover == i
        prs = hov and self.pressed == i
        grow = (2 * s if hov else 0) + pulse
        if prs:
            grow = -1.5 * s                       # squish on press
        lift = 3 * s if prs else (-2 * s if hov else 0)
        if enabled:
            body = fill if active else "#FFFFFF"
            if tcol is None:
                tcol = "#FFFFFF" if (active and lum(fill) < 150) else INK
        else:
            body = mix(fill, "#FFFFFF", 0.6) if active else GRAY
            tcol = "#8A92B0" if not active else "#FFFFFF"
        rrect(cv, a, b + 4 * s, c, d + 4 * s, 13 * s, fill=INK, outline=INK)
        rrect(cv, a - grow, b - grow + lift, c + grow, d + grow + lift, 13 * s, fill=body, outline=INK,
              width=max(2, 2.6 * s))
        f = self.font(px * s)
        while f.measure(label) > (c - a) - 14 * s and px > 8:
            px -= 1
            f = self.font(px * s)
        cv.create_text((a + c) / 2, (b + d) / 2 + lift, text=label, font=f, fill=tcol)
        if enabled:
            self.hits.append((a, b, c, d, i))

    def pill(self, x, y, text, fill, px=13):
        s = self.s
        f = self.font(px * s)
        w = f.measure(text) + 22 * s
        h = 28 * s
        rrect(self.cv, self.X(x), self.Y(y), self.X(x) + w, self.Y(y) + h, 14 * s, fill=fill, outline=INK,
              width=max(2, 2 * s))
        self.cv.create_text(self.X(x) + w / 2, self.Y(y) + h / 2, text=text, font=f, fill=INK)
        return w / s + 8

    # ------------------------------------------------------------------ background
    def draw_bg(self, W, H, dt):
        cv, s, t = self.cv, self.s, self.t
        n = 22
        for i in range(n):
            cv.create_rectangle(0, H * i / n, W, H * (i + 1) / n + 1, fill=mix(BG1, BG2, i / float(n)), width=0)
        for k, (fx, fy, rad, col, amt) in enumerate(((0.08, 0.12, 250, ACCENT, 0.10), (0.97, 0.55, 290, TEAL, 0.09),
                                                     (0.35, 1.02, 230, ACCENT, 0.07))):
            x = W * fx + math.sin(t * 0.20 + k) * 40 * s
            y = H * fy + math.cos(t * 0.17 + k * 2) * 30 * s
            r = rad * s
            cv.create_oval(x - r, y - r, x + r, y + r, fill=mix(BG2, col, amt), outline="")
        g = 46 * s
        off = (t * 7 * s) % g
        col = mix(BG2, INK, 0.06)
        for k in range(-1, int(W / g) + 2):
            cv.create_line(k * g + off, 0, k * g + off, H, fill=col)
        for k in range(-1, int(H / g) + 2):
            cv.create_line(0, k * g + off, W, k * g + off, fill=col)

    # ------------------------------------------------------------------ logo (animated film block)
    def draw_logo(self, cx, cy, r):
        cv, s, t, st = self.cv, self.s, self.t, self.mood
        target = {"done": TEAL, "error": RED}.get(st, ACCENT)
        self.body_col = mix(self.body_col, target, 0.14)
        k = {"work": 0.14 * math.sin(t * 9), "think": 0.05 * math.sin(t * 5)}.get(st, 0.04 * math.sin(t * 2.2))
        sx, sy = 1 + k, 1 - k
        ground = cy + r * 1.12
        if st == "done":
            dt = t - self.done_t
            cy -= abs(math.sin(dt * 6)) * r * 0.55 * math.exp(-dt * 0.8)
        if st == "error":
            cx += 2 * s * math.sin(t * 28) * (1 if (t % 3) < 0.6 else 0)
        lw = max(2, 2.6 * s)
        cv.create_oval(cx - r * 0.9, ground, cx + r * 0.9, ground + r * 0.2, fill=mix(BG2, INK, 0.12), outline="")
        if st == "work":      # two plates squeezing the block
            for sign in (-1, 1):
                px = cx + sign * (r * sx + r * 0.2)
                cv.create_line(px, cy, px + sign * r * 0.5, cy, fill=INK, width=lw * 1.4, capstyle="round")
                rrect(cv, px - r * 0.11, cy - r * 0.85, px + r * 0.11, cy + r * 0.85, 5 * s, fill=AMBER, outline=INK,
                      width=lw)
        x1, x2, y1, y2 = cx - r * sx, cx + r * sx, cy - r * sy, cy + r * sy
        rrect(cv, x1 + 4 * s, y1 + 5 * s, x2 + 4 * s, y2 + 5 * s, r * 0.35, fill=INK, outline=INK)
        rrect(cv, x1, y1, x2, y2, r * 0.35, fill=self.body_col, outline=INK, width=lw + 0.4 * s)
        hole = mix(self.body_col, "#FFFFFF", 0.75)
        for sign in (-1, 1):
            for i in range(3):
                hx, hy = cx + sign * r * 0.72 * sx, cy + (i - 1) * r * 0.52 * sy
                rrect(cv, hx - r * 0.085, hy - r * 0.1, hx + r * 0.085, hy + r * 0.1, 3 * s, fill=hole, outline="")
        if st == "done":
            cv.create_line(cx - r * 0.3, cy + r * 0.02, cx - r * 0.08, cy + r * 0.26, cx + r * 0.32, cy - r * 0.24,
                           fill="#FFFFFF", width=max(3, 5 * s), capstyle="round", joinstyle="round")
        elif st == "error":
            cv.create_text(cx, cy, text="!", font=self.font(r * 1.1), fill="#FFFFFF")
        else:
            cv.create_polygon(cx - r * 0.2, cy - r * 0.32, cx - r * 0.2, cy + r * 0.32, cx + r * 0.34, cy,
                              fill="#FFFFFF", outline="#FFFFFF", width=max(2, 2 * s), joinstyle="round")
            if st == "think":
                a = t * 5
                cv.create_oval(cx + math.cos(a) * r * 0.62 - 3 * s, cy + math.sin(a) * r * 0.62 - 3 * s,
                               cx + math.cos(a) * r * 0.62 + 3 * s, cy + math.sin(a) * r * 0.62 + 3 * s,
                               fill="#FFFFFF", outline="")

    # ------------------------------------------------------------------ screen sections
    def status(self):
        st = self.stage
        if self.flash[1] > time.time() and st == "ready":
            return self.flash[0], AMBER
        if st == "notools":
            return "ffmpeg was not found on this computer", RED
        if st == "empty":
            return "Ready - choose a video to begin", IDLE
        if st == "probing":
            return "Reading video details...", AMBER
        if st == "ready":
            if self.predicting:
                return "Sampling short clips for a better estimate...", AMBER
            if self.plan and self.plan["note"]:
                return self.plan["note"], RED if not self.plan["ok"] else AMBER
            return "Ready to compress", TEAL
        if st == "working":
            return "Compressing - %d%% complete" % int(self.job.get("frac", 0) * 100), AMBER
        if st == "done" and self.result is not None:
            pct = 100 * (1 - self.result / float(self.info["size"]))
            return "Finished - %.0f%% smaller (%s)" % (pct, fmt_size(self.result)), TEAL
        if st == "error":
            return "Something went wrong", RED
        return "", IDLE

    def draw_header(self):
        s, X, Y, cv = self.s, self.X, self.Y, self.cv
        lx, ly, lr = X(68), Y(62), 30 * s
        if self.stage == "done":      # completion ripple
            for delay in (0.0, 0.25):
                p = (self.t - self.done_t - delay) / 1.1
                if 0 < p < 1:
                    rr = lerp(lr, lr * 2.8, p)
                    cv.create_oval(lx - rr, ly - rr, lx + rr, ly + rr, outline=mix(TEAL, BG1, p),
                                   width=max(1, 4 * s * (1 - p)))
        self.draw_logo(lx, ly, lr)
        self.txt(X(122), Y(46), "Comp-1", 38, anchor="w")
        self.txt(X(124), Y(82), "Smart video compressor", 14, fill=MUTED, anchor="w", bold=False)
        # status chip
        text, col = self.status()
        bx1, by1, bx2, by2 = 410, 34, 730, 90
        rrect(cv, X(bx1) + 4 * s, Y(by1) + 5 * s, X(bx2) + 4 * s, Y(by2) + 5 * s, 16 * s, fill=INK, outline=INK)
        rrect(cv, X(bx1), Y(by1), X(bx2), Y(by2), 16 * s, fill="#FFFFFF", outline=INK, width=max(2, 2.6 * s))
        pr = (7 + (2.5 * math.sin(self.t * 6) if self.stage in ("working", "probing") else 0)) * s
        dx, dy = X(bx1 + 26), Y((by1 + by2) / 2)
        cv.create_oval(dx - pr, dy - pr, dx + pr, dy + pr, fill=col, outline=INK, width=max(1, 2 * s))
        self.txt(X(bx1 + 46), dy, text, 14, anchor="w", width=int(255 * s), bold=False)

    def draw_file_card(self):
        s, X, Y, cv = self.s, self.X, self.Y, self.cv
        self.card(30, 125, 730, 255)
        locked = self.stage == "working"
        info = self.info
        if not info:
            x0, y0 = X(58), Y(150)
            rrect(cv, x0 + 3 * s, y0 + 4 * s, x0 + 73 * s, y0 + 74 * s, 14 * s, fill=INK, outline=INK)
            rrect(cv, x0, y0, x0 + 70 * s, y0 + 70 * s, 14 * s, fill=SOFT[0], outline=INK, width=max(2, 2.6 * s))
            for i in range(3):
                for hx in (x0 + 7 * s, x0 + 57 * s):
                    rrect(cv, hx, y0 + (10 + i * 19) * s, hx + 6 * s, y0 + (19 + i * 19) * s, 2 * s, fill="#FFFFFF",
                          outline="")
            cv.create_polygon(x0 + 27 * s, y0 + 21 * s, x0 + 27 * s, y0 + 49 * s, x0 + 48 * s, y0 + 35 * s,
                              fill=ACCENT, outline=ACCENT, width=max(2, 2 * s), joinstyle="round")
            self.txt(X(150), Y(173), "Choose a video to begin", 22, anchor="w", maxw=340 * s)
            self.txt(X(150), Y(204), "MP4 · MOV · MKV · AVI · WEBM and more", 14, fill=MUTED, anchor="w", bold=False)
            self.button("pick", 515, 150, 710, 230, "Choose video", ACCENT, px=21,
                        enabled=self.stage in ("empty", "error", "ready", "done"),
                        pulse=(1 + math.sin(self.t * 3)) * 1.0 * s)
            return
        self.txt(X(55), Y(148), info["name"], 20, anchor="w", maxw=490 * s)
        chips = [("%d×%d" % (info["w"], info["h"]), SOFT[0]), ("%g fps" % round(info["fps"], 2), SOFT[1]),
                 (fmt_dur(info["duration"]), SOFT[2]), (fmt_size(info["size"]), SOFT[0]),
                 (fmt_rate(info["total_bps"]), SOFT[1])]
        x = 52
        for text, col in chips:
            x += self.pill(x, 168, text, col)
        self.txt(X(55), Y(224), "Saves to:  " + os.path.basename(self.out_path), 14, fill=MUTED, anchor="w",
                 maxw=490 * s, bold=False)
        self.button("pick", 570, 140, 710, 182, "Change", ACCENT, px=16, enabled=not locked, active=False)
        self.button("saveas", 570, 194, 710, 236, "Save as...", ACCENT, px=16, enabled=not locked, active=False)

    def draw_help_card(self):
        s, X, Y, cv = self.s, self.X, self.Y, self.cv
        self.card(30, 270, 730, 570)
        steps = [("1", "Choose a video", "Length, resolution and frame rate are read automatically."),
                 ("2", "Pick a size or quality", "Hit an exact file size, or tune the quality yourself."),
                 ("3", "Compress", "Bitrate, resolution and frame rate are calculated for you.")]
        for i, (n, a, b) in enumerate(steps):
            y = 300 + i * 78
            cv.create_oval(X(55) + 3 * s, Y(y) + 4 * s, X(105) + 3 * s, Y(y + 50) + 4 * s, fill=INK, outline=INK)
            cv.create_oval(X(55), Y(y), X(105), Y(y + 50), fill=ACCENT, outline=INK, width=max(2, 2.6 * s))
            self.txt(X(80), Y(y + 25), n, 24, fill="#FFFFFF")
            self.txt(X(125), Y(y + 14), a, 20, anchor="w")
            self.txt(X(125), Y(y + 42), b, 14, fill=MUTED, anchor="w", maxw=560 * s, bold=False)
        self.txt(X(55), Y(540), "bitrate = size × 8 ÷ duration  ·  bits per pixel decides the resolution", 13,
                 fill=ACCENT, anchor="w", maxw=650 * s)

    def draw_tools_card(self):
        s, X, Y = self.s, self.X, self.Y
        self.card(30, 270, 730, 570)
        self.txt(X(55), Y(305), "ffmpeg is required for the encoding", 22, anchor="w", maxw=650 * s)
        lines = ["Install it, then press Re-check:", "   Windows:  winget install ffmpeg", "   macOS:      brew install ffmpeg",
                 "   Linux:       sudo apt install ffmpeg", "",
                 "Or place ffmpeg and ffprobe next to compressor.py."]
        for i, l in enumerate(lines):
            self.txt(X(55), Y(350 + i * 28), l, 16, fill=INK if i in (0, 5) else ACCENT, anchor="w", maxw=650 * s,
                     bold=i not in (0, 5))
        self.button("recheck", 270, 520, 490, 560, "Re-check", ACCENT, px=18)

    def draw_settings(self):
        s, X, Y, cv = self.s, self.X, self.Y, self.cv
        self.card(30, 270, 730, 570)
        st, locked = self.st, self.stage == "working"
        tgt = st["mode"] == "target"
        en = not locked
        self.button("mode:target", 50, 288, 240, 334, "Target size", ACCENT, px=18, enabled=en, active=tgt)
        self.button("mode:quality", 252, 288, 442, 334, "Quality", ACCENT, px=18, enabled=en, active=not tgt)
        self.txt(X(462), Y(311), "Hit an exact file size" if tgt else "Keep quality, size follows", 14, fill=MUTED,
                 anchor="w", maxw=250 * s, bold=False)
        presets = [(8, "8 MB"), (16, "16 MB"), (25, "25 MB"), (50, "50 MB"), (100, "100 MB")] if tgt else \
                  [(15, "Low"), (35, "Medium"), (55, "Good"), (75, "High"), (95, "Max")]
        for i, (v, lab) in enumerate(presets):
            x = 50 + i * 104
            on = abs(self.size_mb() - v) < 0.01 if tgt else abs(self.quality() - v) < 10
            self.button("pre:%d" % v, x, 346, x + 96, 384, lab, ACCENT, px=15, enabled=en, active=on)
        # slider
        x1, x2, ty = 62, 698, 442
        t = st["t"] if tgt else st["qt"]
        self.txt(X(50), Y(410), "File size" if tgt else "Quality", 16, anchor="w", fill=MUTED)
        if tgt:
            val = "%g MB" % self.size_mb()
        else:
            val = "%d%%  ·  CRF %g" % (self.quality(), self.plan["crf"] if self.plan and self.plan["crf"] else 0)
        self.txt(X(710), Y(410), val, 24, fill=ACCENT, anchor="e")
        self.slider_geo = (X(x1), X(x2))
        rrect(cv, X(x1) - 2 * s, Y(ty - 8) + 3 * s, X(x2) + 2 * s, Y(ty + 8) + 3 * s, 9 * s, fill=INK, outline=INK)
        rrect(cv, X(x1), Y(ty - 8), X(x2), Y(ty + 8), 9 * s, fill="#E9EDFA", outline=INK, width=max(2, 2.6 * s))
        fx = X(x1) + (X(x2) - X(x1)) * t
        if t > 0.01:
            rrect(cv, X(x1), Y(ty - 8), max(fx, X(x1) + 18 * s), Y(ty + 8), 9 * s, fill=ACCENT, outline=INK,
                  width=max(2, 2.6 * s))
        active = self.hover == "slider" or self.drag
        kr = (15 + (3 if active else 0) + math.sin(self.t * 4) * 0.8) * s
        cv.create_oval(fx - kr + 2 * s, Y(ty) - kr + 3 * s, fx + kr + 2 * s, Y(ty) + kr + 3 * s, fill=INK, outline=INK)
        cv.create_oval(fx - kr, Y(ty) - kr, fx + kr, Y(ty) + kr, fill="#FFFFFF", outline=INK, width=max(2, 2.6 * s))
        cv.create_oval(fx - kr * 0.38, Y(ty) - kr * 0.38, fx + kr * 0.38, Y(ty) + kr * 0.38, fill=ACCENT, outline="")
        if en:
            self.hits.append((X(x1) - 20 * s, Y(ty - 26), X(x2) + 20 * s, Y(ty + 26), "slider"))
        # codec + audio
        self.button("codec:h264", 50, 476, 205, 514, "H.264 · everywhere", ACCENT, px=14, enabled=en, active=st["codec"] == "h264")
        self.button("codec:h265", 213, 476, 368, 514, "H.265 · smaller", ACCENT, px=14, enabled=en, active=st["codec"] == "h265")
        self.button("audio:on", 410, 476, 540, 514, "Sound on", ACCENT, px=15, enabled=en, active=st["audio"])
        self.button("audio:off", 548, 476, 650, 514, "Mute", ACCENT, px=15, enabled=en, active=not st["audio"])
        # speed + resolution
        for i, (key, lab) in enumerate((("fast", "Fast"), ("balanced", "Balanced"), ("thorough", "Thorough"))):
            x = 50 + i * 106
            self.button("speed:" + key, x, 526, x + 98, 562, lab, ACCENT, px=15, enabled=en, active=st["speed"] == key)
        if tgt:
            self.txt(X(410), Y(544), "Resolution is chosen automatically", 13, fill=MUTED, anchor="w", maxw=290 * s,
                     bold=False)
        else:
            for i, (v, lab) in enumerate(((0, "Same"), (1080, "1080p"), (720, "720p"), (480, "480p"))):
                x = 410 + i * 76
                self.button("res:%d" % v, x, 526, x + 68, 562, lab, ACCENT, px=14, enabled=en, active=st["res"] == v)

    def draw_plan(self):
        s, X, Y = self.s, self.X, self.Y
        self.card(30, 585, 730, 665, fill="#F6F8FF")
        p = self.plan
        if not p:
            return
        line1 = "%d×%d · %g fps · %s · %s" % (p["w"], p["h"], round(p["fps"], 2), CODECS[p["codec"]]["label"],
                                              {"veryfast": "fast", "medium": "balanced", "slow": "thorough"}[p["preset"]])
        self.txt(X(55), Y(608), "Plan:  " + line1, 16, anchor="w", maxw=650 * s)
        est, tag = p["est"], "≈ "
        if self.pred and self.pred[0] == self.plan_sig(p):
            est, tag = self.pred[1], "measured ≈ "
        save = 100 * (1 - est / float(self.info["size"]))
        self.txt(X(55), Y(640), "%s%s  (%s%.0f%%)" % (tag, fmt_size(est), "−" if save >= 0 else "+", abs(save)), 22,
                 fill=TEAL if save > 5 else AMBER, anchor="w", maxw=360 * s)
        right = "video %s + audio %s" % (fmt_rate(p["vbps"]), fmt_rate(p["abps"]) if p["abps"] else "off")
        self.txt(X(710), Y(640), right, 13, fill=MUTED, anchor="e", maxw=320 * s, bold=False)

    def draw_action(self):
        s, X, Y, cv = self.s, self.X, self.Y, self.cv
        st = self.stage
        if st == "ready":
            ok = bool(self.plan and self.plan["ok"]) and not self.predicting
            self.button("go", 190, 682, 570, 752, "Compress", ACCENT, px=32, enabled=ok,
                        pulse=(1 + math.sin(self.t * 4)) * 1.0 * s if ok else 0)
            if self.st["mode"] == "quality":
                self.button("predict", 30, 700, 170, 736, "Estimate size" if not self.predicting else "Sampling...",
                            ACCENT, px=14, enabled=not self.predicting, active=False)
        elif st == "working":
            j = self.job
            j["disp"] += (j["frac"] - j["disp"]) * 0.15
            x1, x2, y1, y2 = 40, 590, 690, 732
            a, b, c, d = X(x1), Y(y1), X(x2), Y(y2)
            rrect(cv, a + 3 * s, b + 4 * s, c + 3 * s, d + 4 * s, 16 * s, fill=INK, outline=INK)
            rrect(cv, a, b, c, d, 16 * s, fill="#FFFFFF", outline=INK, width=max(2, 2.6 * s))
            fw = (c - a) * clamp(j["disp"], 0, 1)
            if fw > 24 * s:
                rrect(cv, a, b, a + fw, d, 16 * s, fill=TEAL, outline=INK, width=max(2, 2.6 * s))
                step, sw = 34 * s, 12 * s
                off = (self.t * 36 * s) % step
                k = a + 14 * s - step + off
                while k < a + fw - 30 * s:
                    if k > a + 8 * s:
                        cv.create_polygon(k, d - 5 * s, k + sw, d - 5 * s, k + sw + 11 * s, b + 5 * s, k + 11 * s,
                                          b + 5 * s, fill=mix(TEAL, "#FFFFFF", 0.3), outline="")
                    k += step
                bx, by = a + fw, (b + d) / 2 - abs(math.sin(self.t * 5)) * 3 * s
                cv.create_oval(bx - 13 * s + 2 * s, by - 13 * s + 3 * s, bx + 13 * s + 2 * s, by + 13 * s + 3 * s,
                               fill=INK, outline=INK)
                cv.create_oval(bx - 13 * s, by - 13 * s, bx + 13 * s, by + 13 * s, fill="#FFFFFF", outline=INK,
                               width=max(2, 2.6 * s))
                cv.create_oval(bx - 5 * s, by - 5 * s, bx + 5 * s, by + 5 * s, fill=TEAL, outline="")
            el = time.time() - j["start"]
            eta = "~" + fmt_dur(el * (1 - j["frac"]) / j["frac"]) + " left" if j["frac"] > 0.02 else "estimating..."
            info = "%d%% · %s · %s%s · pass %d/%d" % (int(j["frac"] * 100), eta, "%.0f fps" % j["fps"] if j["fps"] else "starting",
                                               (" · " + j["speed"]) if j["speed"] and j["speed"] != "N/A" else "",
                                               j["pass_no"], j["passes"])
            self.txt((a + c) / 2, Y(756), info, 14, fill=MUTED, maxw=560 * s, bold=False)
            self.button("stop", 608, 690, 720, 732, "Stop", RED, px=17)
        elif st == "done":
            ratio = clamp(self.result / float(self.info["size"]), 0.02, 1.0)
            for i, (lab, col, w, val) in enumerate((("Before", "#B8C2E0", 1.0, self.info["size"]),
                                                    ("After", TEAL, ratio, self.result))):
                y = 684 + i * 36
                self.txt(X(40), Y(y + 13), lab, 15, anchor="w", fill=MUTED)
                a, b = X(120), Y(y)
                full = X(500) - a
                shown = max(12 * s, full * w * min(1, (self.t - self.done_t) * 1.5))
                rrect(cv, a + 2 * s, b + 3 * s, a + shown + 2 * s, Y(y + 26) + 3 * s, 10 * s, fill=INK, outline=INK)
                rrect(cv, a, b, a + shown, Y(y + 26), 10 * s, fill=col, outline=INK, width=max(2, 2.6 * s))
                if shown > 90 * s:
                    self.txt(a + 10 * s, Y(y + 13), fmt_size(val), 14, anchor="w",
                             fill=INK if lum(col) > 150 else "#FFFFFF")
                else:
                    self.txt(a + shown + 10 * s, Y(y + 13), fmt_size(val), 14, anchor="w")
            self.button("open", 530, 680, 720, 718, "Show in folder", ACCENT, px=16)
            self.button("again", 530, 728, 720, 766, "Compress again", ACCENT, px=16, active=False)
        elif st == "error":
            self.txt(X(40), Y(700), (self.err or "Unknown error").replace("\n", " "), 14, fill=RED, anchor="w",
                     maxw=480 * s, bold=False)
            self.button("pick", 530, 684, 720, 724, "Choose video", ACCENT, px=16)
            if self.info:
                self.button("again", 530, 732, 720, 770, "Try again", ACCENT, px=16, active=False)
        elif st == "empty":
            self.button("go", 190, 682, 570, 752, "Compress", ACCENT, px=32, enabled=False)

    # ------------------------------------------------------------------ main loop
    def draw(self, dt):
        cv = self.cv
        W, H = cv.winfo_width(), cv.winfo_height()
        if W < 60 or H < 60:
            return
        self.s = clamp(min(W / DESIGN_W, H / DESIGN_H), 0.5, 2.6)
        self.ox = (W - DESIGN_W * self.s) / 2
        self.oy = max(0, (H - DESIGN_H * self.s) / 2)
        cv.delete("all")
        self.hits = []
        st = self.stage
        if st in ("ready", "working", "done") and self.info:
            self.plan = make_plan(self.info, self.settings())
        self.mood = ("work" if st == "working" else "done" if st == "done" else "error" if st in ("error", "notools")
                     else "think" if (st == "probing" or self.predicting) else "idle")
        self.draw_bg(W, H, dt)
        self.draw_header()
        self.draw_file_card()
        if st == "notools":
            self.draw_tools_card()
        elif st in ("ready", "working", "done") or (st == "error" and self.info):
            self.draw_settings()
            self.draw_plan()
        else:
            self.draw_help_card()
        if st != "notools":
            self.draw_action()

    def frame(self):
        now = time.perf_counter()
        dt = min(0.06, now - self.last)
        self.last = now
        self.t += dt
        try:
            self.poll()
            self.draw(dt)
        except tk.TclError:
            return
        self.root.after(33, self.frame)


PRESETS_BY_NAME = [(v[0], v[1]) for v in PRESETS.values()]


def main():
    enable_dpi()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()