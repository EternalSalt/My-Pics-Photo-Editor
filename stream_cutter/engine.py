"""Stream Cutter engine: audio, video and speech analysis, automatic moment picking, rendering. No GUI code."""
import bisect, hashlib, json, os, queue, re, shutil, subprocess, threading, time, urllib.error, urllib.request, zipfile
from dataclasses import dataclass
from pathlib import Path
import numpy as np

SR, WIN = 16000, 0.5          # audio sample rate, seconds per analysis window (audio, video, speech share it)
VW, VH = 64, 36               # video frames are shrunk to this size for motion tracking
W, H, FPS = 1920, 1080, 60    # format of the rendered rough cut
CLIP_KEEP_ALL = 90            # files shorter than this (sec) are treated as pre-picked clips and kept whole
MIN_SCORE = 1.5               # lowest bar (sigmas above the local norm) a moment can clear
AUTO_CEIL = 0.5               # auto length never exceeds this share of the footage...
AUTO_MAX_SEC = 2400           # ...or this many seconds, unless you set Max length yourself
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".flv", ".ts", ".webm", ".avi", ".m4v"}
NOWIN = 0x08000000 if os.name == "nt" else 0
ROOT = Path.home() / "Videos" / "StreamCutter"
APPDIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "StreamCutter"
FFBIN, MODELS, CONFIG = APPDIR / "ffmpeg", APPDIR / "models", APPDIR / "config.json"
FFURLS = ["https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
          "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"]
DIRECTOR_MODEL = "claude-sonnet-5-5"          # only used if you choose the Claude API option
SPEECH_MODELS = {"Fast": "tiny", "Balanced": "base", "Best": "small"}
TIGHT = ("Off", "Light", "Tight")


class Cancelled(Exception):
    pass


def mmss(t):
    t = int(t)
    return f"{t // 3600}:{t // 60 % 60:02}:{t % 60:02}" if t >= 3600 else f"{t // 60}:{t % 60:02}"


def load_config():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(d):
    try:
        APPDIR.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except OSError:
        pass


# ---------- ffmpeg ----------
def ffmpeg_ready():
    if (FFBIN / "ffmpeg.exe").exists():
        os.environ["PATH"] = str(FFBIN) + os.pathsep + os.environ["PATH"]
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def download_ffmpeg(progress):
    """First-run download. progress(fraction) or progress(None) when the size is unknown."""
    FFBIN.mkdir(parents=True, exist_ok=True)
    zp, last = FFBIN / "download.zip", None
    for url in FFURLS:
        try:
            with urllib.request.urlopen(url, timeout=30) as r, open(zp, "wb") as fh:
                total, got = int(r.headers.get("Content-Length") or 0), 0
                while chunk := r.read(1 << 18):
                    fh.write(chunk)
                    got += len(chunk)
                    progress(got / total if total else None)
            with zipfile.ZipFile(zp) as z:
                for n in z.namelist():
                    if n.endswith(("/bin/ffmpeg.exe", "/bin/ffprobe.exe")):
                        (FFBIN / Path(n).name).write_bytes(z.read(n))
            zp.unlink()
            if ffmpeg_ready():
                return
        except Exception as e:
            last = e
    raise RuntimeError(f"Could not download ffmpeg ({last}). Check your internet connection and retry.")


def ff(cmd):
    r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, creationflags=NOWIN)
    if r.returncode:
        raise RuntimeError(r.stderr.decode(errors="ignore")[-400:])
    return r.stdout


def probe(f):
    """(duration in seconds, rounded fps) of a video file."""
    d = json.loads(ff(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                       "stream=r_frame_rate:format=duration", "-of", "json", str(f)]))
    n, m = d["streams"][0]["r_frame_rate"].split("/")
    return float(d["format"]["duration"]), round(int(n) / int(m))


# ---------- raw analysis: audio, video, speech ----------
def loudness(path, track, dur, on_frac, cancel):
    """Loudness in dB per WIN-second window of one audio track, streamed so memory stays small."""
    n = int(SR * WIN)
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-map", f"0:a:{track}", "-ac", "1",
           "-ar", str(SR), "-f", "s16le", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=NOWIN)
    out, buf, done = [], b"", 0
    try:
        while chunk := p.stdout.read(n * 2 * 60):
            if cancel.is_set():
                raise Cancelled()
            buf += chunk
            k = len(buf) // (n * 2)
            if k:
                x = np.frombuffer(buf[: k * n * 2], np.int16).astype(np.float32).reshape(k, n) / 32768
                out.append(20 * np.log10(np.sqrt((x * x).mean(1)) + 1e-6))
                buf = buf[k * n * 2:]
                done += k
                on_frac(min(1.0, done * WIN / dur))
    finally:
        if p.poll() is None:
            p.kill()
        p.wait()
    return np.concatenate(out) if out else np.zeros(0)


def watch(path, dur, on_frac, cancel):
    """Motion per WIN-second window: how much the picture changed since the previous sample.
    Frames are shrunk to 64x36 grey, so big or sudden changes stand out and a frozen screen reads ~0."""
    fsz = VW * VH
    for hw in (["-hwaccel", "auto"], []):          # try GPU decoding first, fall back to CPU
        cmd = ["ffmpeg", "-v", "error", *hw, "-i", str(path), "-an", "-sn", "-vf",
               f"fps={1 / WIN:g},scale={VW}:{VH}:flags=fast_bilinear,format=gray", "-f", "rawvideo", "-"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, creationflags=NOWIN)
        out, buf, prev, done = [], b"", None, 0
        try:
            while chunk := p.stdout.read(fsz * 120):
                if cancel.is_set():
                    raise Cancelled()
                buf += chunk
                k = len(buf) // fsz
                if k:
                    x = np.frombuffer(buf[: k * fsz], np.uint8).reshape(k, fsz).astype(np.float32)
                    buf = buf[k * fsz:]
                    d = np.abs(np.diff(x if prev is None else np.vstack([prev, x]), axis=0)).mean(1)
                    out.append(np.concatenate([[0.0], d]) if prev is None else d)
                    prev = x[-1:]
                    done += k
                    on_frac(min(1.0, done * WIN / dur))
        finally:
            if p.poll() is None:
                p.kill()
            p.wait()
        if out:
            return np.concatenate(out)
    return np.zeros(0)


def transcribe(path, track, dur, model_name, on_frac, cancel, on_status):
    """Speech to text with faster-whisper on the CPU, 10 minutes of audio at a time.
    Returns [(start, end, text)], or None when the speech library is not part of this build."""
    try:
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        from faster_whisper import WhisperModel
    except Exception:
        return None
    on_status("Loading the speech model (the first run downloads it, about 150 MB)…")
    MODELS.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(model_name, device="cpu", compute_type="int8",
                         cpu_threads=min(8, os.cpu_count() or 4), download_root=str(MODELS))
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-map", f"0:a:{track}", "-ac", "1",
           "-ar", str(SR), "-f", "s16le", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=NOWIN)
    out, off, lang = [], 0.0, None
    try:
        while True:
            data = p.stdout.read(SR * 2 * 600)
            data = data[: len(data) // 2 * 2]
            if not data:
                break
            x = np.frombuffer(data, np.int16).astype(np.float32) / 32768
            segs, info = model.transcribe(x, language=lang, beam_size=1, vad_filter=True,
                                          condition_on_previous_text=False)
            if lang is None and getattr(info, "language_probability", 0) >= 0.6:
                lang = info.language                      # lock the language once it is clear
            for sg in segs:
                if cancel.is_set():
                    raise Cancelled()
                text = sg.text.strip()
                if text and sg.no_speech_prob <= 0.7 and sg.avg_logprob >= -1.3:   # drop likely hallucinations
                    out.append((float(off + sg.start), float(off + sg.end), text))
                on_frac(min(1.0, (off + sg.end) / dur))
            off += len(x) / SR
    finally:
        if p.poll() is None:
            p.kill()
        p.wait()
    return out


# ---------- turning raw analysis into "how exciting is each half second" ----------
STRONG = {"omg", "wtf", "lmao", "lmfao", "holy", "bruh", "fuck", "fucking", "shit", "damn", "dammit", "dude",
          "bro", "god", "wow", "oof", "ow", "ahh", "ahhh", "yooo", "jesus", "goddamn"}
MILD = {"no", "what", "wait", "oh", "yes", "yeah", "stop", "help", "nope", "yo", "dang", "hell", "ah", "ha",
        "hey", "whoa", "ooh", "uh"}
LAUGH = re.compile(r"^(ha|he|hah|heh|haha|hehe)+h?$")
TOKEN = re.compile(r"[a-z']+")


@dataclass
class Sc:
    a: np.ndarray            # audio excitement (sigmas above the local norm, -99 = dead air)
    v: object = None         # visual excitement, or None if the video was not scanned
    s: object = None         # speech excitement, or None if speech was not read
    tr: object = None        # transcript [(start, end, text)] or None
    talk: object = None      # share of the footage with someone talking, or None


def _avg(x, k):
    """Moving average that stays honest at the start and end of the file (no zero-padding bias)."""
    ones = np.ones(k)
    return np.convolve(x, ones, "same") / np.convolve(np.ones(len(x)), ones, "same")


def _excess(x, live, floor_sigma):
    """How far each window sits above its local norm (the surrounding minute), in 'sigmas'.
    Dead windows (silence / frozen picture / nobody talking) score -99 and never drag the norm around."""
    if not live.any():
        return np.full(len(x), -99.0)
    flat = np.where(live, x, np.median(x[live]))
    e = _avg(flat - _avg(flat, 120), 3)
    sig = max(1.4826 * np.median(np.abs(e[live] - np.median(e[live]))), floor_sigma)
    return np.where(live, e / sig, -99.0)


def _seg_excite(text):
    toks = TOKEN.findall(text.lower())
    if not toks:
        return 0.0, 0
    s = sum(1.0 if t in STRONG or LAUGH.match(t) else 0.4 if t in MILD else 0.0 for t in toks)
    s += 0.5 * text.count("!") + 0.1 * text.count("?")
    s += 0.5 * sum(1 for a, b in zip(toks, toks[1:]) if a == b)          # "no no no no"
    return s, len(toks)


def _speech(tr, n):
    """Reaction words, laughter, exclamations and repetition in each spoken phrase, spread over the windows it covers."""
    x, covered = np.zeros(n), np.zeros(n, bool)
    for st, en, text in tr:
        a, b = int(st / WIN), min(n, int(np.ceil(en / WIN)))
        v, words = _seg_excite(text)
        if a >= n or b <= a or not words:
            continue
        x[a:b] = np.maximum(x[a:b], min(v, 6.0))        # per phrase, so a short burst is not diluted
        covered[a:b] = True
    return _excess(x, covered, 0.5), float(covered.mean()) if n else 0.0


def scores(db, vis=None, tr=None):
    n = len(db) if vis is None or not len(vis) else min(len(db), len(vis))
    db = db[:n]
    sc = Sc(_excess(db, db > np.percentile(db, 10) + 6, 2.0))
    if vis is not None and len(vis):
        v = vis[:n]
        sc.v = _excess(np.log1p(v), v > 0.5, 0.35)
    if tr is not None:
        sc.tr = sorted(tr)
        sc.s, sc.talk = _speech(sc.tr, n)
    return sc


# ---------- weights, picking, planning ----------
def _peak(x):
    live = None if x is None else x > -98
    return float(np.percentile(x[live], 99.5)) if live is not None and live.sum() > 20 else 0.0


def calibrate(sc):
    """Automatic balance: scale each signal so its strongest moments are comparable, and ignore a signal
    that has no real peaks at all. Returns weights for (audio, visual, speech)."""
    return tuple(0.0 if _peak(x) < 2.2 else float(np.clip(5.0 / _peak(x), 0.6, 1.6)) for x in (sc.a, sc.v, sc.s))


def manual_weights(sc, balance):
    wa = min(1.5, 2 * (1 - balance)) if sc.v is not None else 1.0
    return wa, (min(1.5, 2 * balance) if sc.v is not None else 0.0), (1.0 if sc.s is not None else 0.0)


def bar_for(picky):
    """'How picky' slider 0..1 -> how many sigmas a moment must stand out by to make the cut."""
    return MIN_SCORE + 3.0 * picky


def _signals(sc, w):
    n = len(sc.a)
    parts = [np.where(x > -98, wt * x, -99.0) if x is not None and wt > 0 else np.full(n, -99.0)
             for x, wt in zip((sc.a, sc.v, sc.s), w)]
    return parts, np.maximum.reduce(parts)


def best_moment(sc, w):
    """(score, seconds) of the single strongest moment. Used for the cold open."""
    score = _signals(sc, w)[1]
    i = int(np.argmax(score))
    return float(score[i]), (i + 0.5) * WIN


def _merge(segs):
    segs = sorted(segs)
    out = [list(segs[0])] if segs else []
    for s, e, k in segs[1:]:
        if s <= out[-1][1] + 0.5:
            out[-1][1] = max(out[-1][1], e)
            out[-1][2] = "".join(c for c in "avs" if c in out[-1][2] + k)
        else:
            out.append([s, e, k])
    return [(float(s), float(e), k) for s, e, k in out]


def _snap(segs, tr, dur):
    """Never start or end a clip in the middle of a sentence: pull edges out to the nearest spoken-phrase edge."""
    if not tr or not segs:
        return segs
    starts = [t[0] for t in tr]

    def at(t):
        i = bisect.bisect_right(starts, t) - 1
        return tr[i] if i >= 0 else None

    out = []
    for s, e, k in segs:
        a, b = at(s), at(e)
        if a and a[0] < s < a[1] and s - a[0] <= 8:
            s = max(0.0, a[0] - 0.15)
        if b and b[1] > e and b[1] - e <= 8:
            e = min(dur, b[1] + 0.3)
        out.append((s, e, k))
    return _merge(out)


def pick(sc, w, bar, max_sec, pre, post, gap=5.0, min_len=6.0, tighten=0):
    """Choose moments as (start, end, kind); kind is a mix of 'a' audio, 'v' visual, 's' speech.
    Length is automatic: every moment that clears `bar` (in sigmas) is kept, so an eventful stream gives a
    longer video and a quiet one a shorter video. max_sec > 0 adds a hard cap by raising the bar.
    tighten 1/2 removes low-energy stretches (4 s / 2 s or longer) between peaks inside one moment."""
    dur = len(sc.a) * WIN
    if dur < CLIP_KEEP_ALL:
        return [(0.0, dur, "a")] if dur else []
    parts, score = _signals(sc, w)
    if not (score > -98).any():
        return []
    low = np.maximum.reduce([x for x in (sc.a, sc.v, sc.s) if x is not None]) < -0.5   # calmer than the norm
    minrun = {1: 8, 2: 4}.get(tighten, 0)
    pw, qw = int(np.ceil(post / WIN)), int(np.ceil(pre / WIN))

    def cut(thr):
        groups = []
        for i in np.flatnonzero(score >= thr):
            flags = [bool(p[i] >= thr) for p in parts]
            if groups and i * WIN - pre - ((groups[-1][1] + 1) * WIN + post) <= gap:
                g = groups[-1]
                g[1], g[2] = i, [x or y for x, y in zip(g[2], flags)]
                g[3].append(i)
            else:
                groups.append([i, i, flags, [i]])
        segs = []
        for a, b, flags, idx in groups:
            s, e = max(0.0, a * WIN - pre), min(dur, (b + 1) * WIN + post)
            if e - s < min_len:
                continue
            kind = "".join(c for c, f in zip("avs", flags) if f)
            gone = []
            if minrun:
                for p, q in zip(idx, idx[1:]):            # the stretch between two peaks of the same moment
                    i, end = p + 1 + pw, q - qw           # keep the wind-down and run-up around each peak
                    while i < end:
                        if low[i]:
                            j = i
                            while j < end and low[j]:
                                j += 1
                            if j - i >= minrun:
                                gone.append((i * WIN + 0.3, j * WIN - 0.3))
                            i = j
                        else:
                            i += 1
            pos = s
            for c0, c1 in gone + [(e, e)]:
                if c0 - pos >= 1.5:
                    segs.append((pos, c0, kind))
                pos = c1
        return _snap(segs, sc.tr, dur)

    length = lambda segs: sum(e - s for s, e, _ in segs)
    cap = min(max_sec if max_sec > 0 else dur, AUTO_CEIL * dur)
    segs = cut(bar)
    if length(segs) <= cap:
        return segs
    lo, hi, best = bar, float(score.max()), []
    for _ in range(10):                                    # raise the bar until the cap is met
        mid = (lo + hi) / 2
        trial = cut(mid)
        if length(trial) <= cap:
            hi, best = mid, trial
        else:
            lo = mid
    return best


def plan(sc, cfg):
    """Everything about HOW to cut one file. Auto mode tunes it from the footage itself; manual mode uses
    the person's own settings. Returns dict(segs, w, params, notes)."""
    dur = len(sc.a) * WIN
    if cfg.get("manual"):
        w = manual_weights(sc, cfg["balance"])
        p = dict(bar=bar_for(cfg["select"]), cap=cfg["max_min"] * 60, pre=round(cfg["breathe"]),
                 post=max(1, round(cfg["breathe"] * 0.7)), gap=5.0, min_len=6.0, tighten=cfg["tighten"])
        segs = pick(sc, w, *[p[k] for k in ("bar", "cap", "pre", "post", "gap", "min_len", "tighten")])
        notes = ["Using your own settings"]
    else:
        w = calibrate(sc)
        talk = sc.talk if sc.talk is not None else 0.4
        heavy = talk >= 0.45
        pre = round(2 + 4 * talk)
        p = dict(bar=2.8, cap=(cfg["max_min"] * 60) or min(AUTO_CEIL * dur, AUTO_MAX_SEC), pre=pre,
                 post=max(1, round(pre * 0.7)), gap=8.0 if heavy else 4.0, min_len=8.0 if heavy else 5.0,
                 tighten=1 if heavy else 2)
        args = ("cap", "pre", "post", "gap", "min_len", "tighten")
        segs = pick(sc, w, p["bar"], *[p[k] for k in args])
        floor = 0.04 * dur if dur >= 1200 else 0                 # long footage should not come out nearly empty
        for b in (2.4, 2.0, 1.7):
            if sum(e - s for s, e, _ in segs) >= floor:
                break
            p["bar"] = b
            segs = pick(sc, w, b, *[p[k] for k in args])
        used = [n for n, x in zip(("audio", "visual", "speech"), w) if x > 0]
        notes = [f"Watching: {', '.join(used) or 'nothing stands out here'}",
                 (f"{'Talk-heavy' if heavy else 'Action-heavy'} footage ({round(talk * 100)}% speech)"
                  if sc.talk is not None else "Pacing set from audio and video (speech reading is off)"),
                 f"Run-up {p['pre']} s, trimming {TIGHT[p['tighten']]}, bar {p['bar']:.1f}"]
    notes.append(f"Keeps {len(segs)} moments, {mmss(sum(e - s for s, e, _ in segs))} of {mmss(dur)}")
    return dict(segs=segs, w=w, params=p, notes=notes)


# ---------- optional AI director: a model on this PC (free) or the Anthropic API ----------
LOCAL_BASES = ["http://127.0.0.1:11434", "http://127.0.0.1:8080", "http://127.0.0.1:1234"]   # Ollama, llama.cpp, LM Studio
PULL_CHOICES = ["phi3.5", "llama3.2:3b"]       # small models with commercial-friendly licenses, for a one-time download
NOT_CHAT = ("embed", "nomic", "bge", "minilm", "rerank", "whisper", "clip")
_local = urllib.request.build_opener(urllib.request.ProxyHandler({}))          # localhost must never go via a proxy
SCHEMA = {"type": "object", "properties": {
    "moments": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "integer"}, "score": {"type": "number"}, "title": {"type": "string"}},
        "required": ["id", "score", "title"]}},
    "hook_id": {"type": "integer"}}, "required": ["moments", "hook_id"]}


def _get_json(url, timeout=1.5):
    try:
        with _local.open(url, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


def _params_b(m):
    ps = str(m.get("details", {}).get("parameter_size", "")).upper().strip()
    try:
        return float(ps[:-1]) * {"B": 1.0, "M": 0.001}.get(ps[-1:])
    except (TypeError, ValueError):
        return m.get("size", 0) / 0.6e9          # rough: a 4-bit model is about 0.6 GB per billion parameters


def usable_models(tags):
    """Chat models in an Ollama /api/tags reply that really run on this PC: [(name, billions of parameters)].
    Ollama 'cloud' models run on someone else's servers, and embedding models cannot chat, so both are skipped."""
    out = []
    for m in tags.get("models", []):
        name = m.get("name") or m.get("model") or ""
        low = name.lower()
        if (not name or m.get("remote_host") or m.get("remote_model") or "cloud" in low
                or any(k in low for k in NOT_CHAT) or m.get("details", {}).get("family") in ("bert", "nomic-bert")):
            continue
        out.append((name, _params_b(m)))
    return out


def choose_model(models, prefer=""):
    """Best installed model for rating moments: big enough to judge, small enough to be quick on a PC."""
    if prefer in [n for n, _ in models]:
        return prefer
    mid = [x for x in models if 2.5 <= x[1] <= 8.5]
    small = [x for x in models if x[1] < 2.5]
    if mid or small:
        return max(mid or small, key=lambda x: x[1])[0]
    return min(models, key=lambda x: x[1])[0] if models else ""          # only big ones installed: the smallest


def ollama_exe():
    p = shutil.which("ollama")
    if p:
        return p
    d = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return str(d) if d.exists() else None


def find_local_ai(start=False, wait=15):
    """Look for a local AI server: Ollama, llama.cpp's server or LM Studio. Returns dict(kind, base, models) or None.
    start=True also launches Ollama if it is installed but closed."""
    def probe():
        for b in LOCAL_BASES:
            j = _get_json(b + "/api/tags")
            if isinstance(j, dict) and "models" in j:
                return dict(kind="ollama", base=b, models=usable_models(j))
            j = _get_json(b + "/v1/models")
            if isinstance(j, dict) and "data" in j:
                ids = [m["id"] for m in j["data"] if not any(k in str(m["id"]).lower() for k in NOT_CHAT)]
                return dict(kind="openai", base=b, models=[(i, 0.0) for i in ids] or [("local-model", 0.0)])
        return None

    found, exe = probe(), ollama_exe()
    if found is None and start and exe:
        try:
            subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, creationflags=NOWIN)
        except OSError:
            return None
        for _ in range(int(wait * 2)):
            time.sleep(0.5)
            found = probe()
            if found:
                break
    return found


def _stream_lines(url, payload, cancel, on_json, sse=False):
    """POST JSON to a local server and feed every streamed JSON line to on_json, staying cancellable."""
    q, box = queue.Queue(), {}

    def worker():
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                         headers={"content-type": "application/json"})
            box["r"] = r = _local.open(req, timeout=1800)
            for raw in r:
                line = raw.decode("utf-8", "ignore").strip()
                if sse:
                    if not line.startswith("data:"):
                        continue
                    line = line[5:].strip()
                    if line == "[DONE]":
                        break
                if line:
                    q.put(("j", json.loads(line)))
            q.put(("end", None))
        except Exception as e:
            q.put(("err", e))

    def stop():                                     # closing the connection also makes the server stop generating
        try:
            box["r"].close()
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()
    while True:
        try:
            kind, val = q.get(timeout=0.25)
        except queue.Empty:
            if cancel.is_set():
                stop()
                raise Cancelled()
            continue
        if cancel.is_set():
            stop()
            raise Cancelled()
        if kind == "j":
            on_json(val)
        elif kind == "end":
            return
        else:
            stop()
            raise val


def pull_model(info, cancel, on_frac):
    """One-time download of a small model into Ollama (it must have been confirmed by the person first)."""
    if info["kind"] != "ollama":
        raise RuntimeError("no model is loaded in your local AI server")
    last = None
    for name in PULL_CHOICES:
        def line(j):
            if j.get("error"):
                raise RuntimeError(str(j["error"]))
            if j.get("total", 0) > 50e6:                # ignore the tiny manifest layers
                on_frac(min(1.0, j.get("completed", 0) / j["total"]))
        try:
            _stream_lines(info["base"] + "/api/pull", {"model": name, "name": name, "stream": True}, cancel, line)
            return name
        except Cancelled:
            raise
        except Exception as e:
            last = e
    raise RuntimeError(f"could not download a model ({last})")


def _text(tr, s, e, limit):
    return " ".join(t for a, b, t in (tr or []) if b > s and a < e)[:limit]


def _director_prompt(cands):
    lines = [f'[{c["id"]}] {c["where"]}, {c["e"] - c["s"]:.0f}s, driven by {c["why"]}\n'
             f'    just before: "{c["before"] or "(nothing)"}"\n    moment: "{c["text"] or "(no speech)"}"' for c in cands]
    return ("You edit a successful YouTube gaming channel. Below are candidate moments cut from a long stream, in "
            "stream order. Transcripts are automatic and may be wrong or empty. Judge each moment the way a viewer "
            "would: funny, surprising, great reactions, clear setup and payoff, or story progress is good; dead air, "
            "repetitive chatter, fragments with no context, technical trouble and meandering talk are bad. Be "
            "selective: most moments should score below 6.\n\n" + "\n".join(lines) +
            '\n\nReply with ONLY JSON like {"moments":[{"id":1,"score":7,"title":"Three to six word title"}],'
            '"hook_id":1}. Score each moment 0 to 10, include every id, and set hook_id to the single best moment '
            "to open the video with.")


def _parse_director(text):
    try:
        j = json.loads(text[text.index("{"): text.rindex("}") + 1])
        rated = {int(m["id"]): (float(m["score"]), str(m.get("title", "")).strip()) for m in j["moments"]}
    except (ValueError, KeyError, TypeError):
        raise RuntimeError("its reply was not in the expected format")
    try:
        hook = int(j.get("hook_id"))
    except (TypeError, ValueError):
        hook = None
    return rated, hook


def ask_claude(cands, key):
    """Anthropic API. Returns ({id: (score, title)}, hook_id). Raises RuntimeError with a readable reason."""
    body = json.dumps({"model": DIRECTOR_MODEL, "max_tokens": 4000,
                       "messages": [{"role": "user", "content": _director_prompt(cands)}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, method="POST", headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError({401: "the API key was rejected", 404: f"model {DIRECTOR_MODEL} was not found",
                            429: "rate limited or out of credit"}.get(e.code, f"HTTP {e.code}"))
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError("could not reach the Anthropic API")
    return _parse_director("".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"))


def ask_local(cands, info, model, cancel, on_progress):
    """Rate the candidates with a model on this PC (Ollama native API, or any OpenAI-style local server)."""
    prompt, ollama = _director_prompt(cands), info["kind"] == "ollama"
    msgs, state = [{"role": "user", "content": prompt}], {"text": "", "n": -1}

    def feed(piece):
        state["text"] += piece
        n = state["text"].count('"id"')                      # one per rated moment: a progress signal
        if n != state["n"]:
            state["n"] = n
            on_progress(min(1.0, n / max(1, len(cands))))

    def attempt(strict):
        state["text"], state["n"] = "", -1
        if ollama:
            def line(j):
                if j.get("error"):
                    raise RuntimeError(str(j["error"]))
                feed(j.get("message", {}).get("content", ""))
            body = {"model": model, "messages": msgs, "stream": True, "format": SCHEMA if strict else "json",
                    "options": {"temperature": 0.2, "num_ctx": min(16384, max(4096, len(prompt) // 3 + 2500))}}
            _stream_lines(info["base"] + "/api/chat", body, cancel, line)
        else:
            body = {"model": model, "messages": msgs, "stream": True, "temperature": 0.2, "max_tokens": 3000}
            if strict:
                body["response_format"] = {"type": "json_object"}
            _stream_lines(info["base"] + "/v1/chat/completions", body, cancel,
                          lambda j: feed((j.get("choices") or [{}])[0].get("delta", {}).get("content") or ""), sse=True)

    try:
        try:
            attempt(True)
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
            attempt(False)                                   # an older server that does not know structured output
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8", "ignore")).get("error", "")
            detail = detail.get("message", "") if isinstance(detail, dict) else str(detail)
        except Exception:
            pass
        raise RuntimeError(f"model {model} was not found" if e.code == 404 else
                           f"the local AI said: {detail or 'HTTP ' + str(e.code)}")
    except (urllib.error.URLError, OSError):
        raise RuntimeError("could not reach the local AI")
    return _parse_director(state["text"])


def direct(plans, order, cfg, info, rate, max_cands, tlim, label):
    """Let an AI choose among a wider set of candidates. rate(cands) -> ({id: (score, title)}, hook_id).
    Returns (cuts, hook, message) or raises."""
    cands = []
    for f in order:
        sc, pl = plans[f]
        p, w = pl["params"], pl["w"]
        more = pick(sc, w, max(1.5, p["bar"] - 0.8), 2 * p["cap"], p["pre"], p["post"], p["gap"], p["min_len"],
                    p["tighten"])
        score = _signals(sc, w)[1]
        for s, e, k in _merge(pl["segs"] + more):
            seg = score[int(s / WIN): int(e / WIN) + 1]
            cands.append(dict(f=f, s=s, e=e, peak=float(seg.max()), t=(int(s / WIN) + int(np.argmax(seg)) + 0.5) * WIN,
                              why=" + ".join({"a": "audio", "v": "visual", "s": "speech"}[c] for c in k),
                              where=f"{Path(f).name} @ {mmss(s)}", text=_text(sc.tr, s, e, tlim),
                              before=_text(sc.tr, s - 15, s, tlim // 3)))
    cands = sorted(sorted(cands, key=lambda c: -c["peak"])[:max_cands], key=lambda c: (order.index(c["f"]), c["s"]))
    for i, c in enumerate(cands, 1):
        c["id"] = i
    rated, hook_id = rate(cands)
    keep = [c for c in cands if rated.get(c["id"], (0, ""))[0] >= 6]
    if len(keep) < 3:
        raise RuntimeError("it kept fewer than 3 moments")
    total = sum(d for d, _ in info.values())
    cap = (cfg["max_min"] * 60) or min(AUTO_CEIL * total, AUTO_MAX_SEC)
    keep.sort(key=lambda c: -rated[c["id"]][0])
    while len(keep) > 3 and sum(c["e"] - c["s"] for c in keep) > cap:
        keep.pop()                                              # drop the lowest-rated until it fits
    keep.sort(key=lambda c: (order.index(c["f"]), c["s"]))
    top = next((c for c in cands if c["id"] == hook_id), None)
    return ([(c["f"], c["s"], c["e"], rated[c["id"]][1]) for c in keep], (top["f"], top["t"]) if top else None,
            f"AI director ({label}) kept {len(keep)} of {len(cands)} candidate moments")


def quick_title(tr, s, e):
    """A free, no-AI chapter title: the most reactive phrase in the moment, trimmed, with strong swearing masked."""
    segs = [x for x in (tr or []) if x[1] > s and x[0] < e]
    if not segs:
        return ""
    best = max(segs, key=lambda x: (_seg_excite(x[2])[0], len(x[2])))
    if _seg_excite(best[2])[0] < 1.0:                  # ordinary chatter makes a bland chapter, so leave it unnamed
        return ""
    words = re.findall(r"[\w']+", best[2])
    while words and words[0].lower() in {"okay", "ok", "so", "um", "uh", "like", "and", "but", "well", "yeah", "alright"}:
        words.pop(0)
    mask = {"fuck": "f***", "fucking": "f***ing", "shit": "s***", "dammit": "d***it", "goddamn": "g****mn"}
    title = " ".join(mask.get(w.lower(), w) for w in words[:6])
    return title[:1].upper() + title[1:]


def chapters(cuts, hook):
    """YouTube chapter lines for the finished video, from the director's titles (none without a director)."""
    t, lines = 0.0, []
    for i, (f, s, e, title) in enumerate(cuts):
        name = "Cold open" if i == 0 and hook else title
        if name and (not lines or (e - s >= 10 and lines[-1][1] != name)):
            lines.append((t, name))
        t += e - s
    if len(lines) < 3:
        return ""
    if lines[0][0] > 0:
        lines.insert(0, (0.0, "Intro"))
    return "\n".join(f"{mmss(t)} {n}" for t, n in lines)


# ---------- cache ----------
def cache_paths(proj, f, track, model="base"):
    k = hashlib.md5(f"{f}{os.path.getmtime(f)}".encode()).hexdigest()[:10]
    d = proj / "cache"
    return d / f"{k}_a{track}.npy", d / f"{k}_v.npy", d / f"{k}_t{track}_{model}.json"


def cached(proj, f, track, visual, speech, model):
    """(audio, visual-or-None, transcript-or-None) from an earlier analysis, or None if it was not done yet."""
    try:
        a, v, t = cache_paths(proj, f, track, model)
        if not a.exists() or (visual and not v.exists()) or (speech and not t.exists()):
            return None
        tr = [tuple(x) for x in json.loads(t.read_text(encoding="utf-8"))] if speech else None
        return np.load(a), (np.load(v) if visual else None), tr
    except (OSError, ValueError):
        return None


# ---------- full pipeline ----------
def build(proj, files, cfg, emit, cancel):
    """Runs everything and reports through emit(tuple). Events:
    stage(i, run|done), prog(0-1), sub(0-1 or None, text), file(path, col, text), dur(path, sec),
    analysis(path, audio, visual, transcript), done(dict)."""
    out, tmp = proj / "output", proj / "output" / "_tmp"
    (proj / "cache").mkdir(parents=True, exist_ok=True)
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    vis_on, sp_on = cfg["visual"], cfg["speech"]
    wts = [1.0, 2.0 if vis_on else 0.0, 4.0 if sp_on else 0.0, {"off": 0.3, "claude": 0.6, "local": 3.0}[cfg["ai"]],
           4.0, 0.3]                                                                  # share of the progress bar
    cum = [0.0]
    for x in wts:
        cum.append(cum[-1] + x / sum(wts))
    S = lambda i, x: cum[i] + (cum[i + 1] - cum[i]) * x
    run_notes = []

    def chk():
        if cancel.is_set():
            raise Cancelled()

    try:
        info = {}
        for f in files:
            try:
                info[f] = probe(f)
            except Exception:
                raise RuntimeError(f"Couldn't read {Path(f).name}. Is it a valid video file?")
            emit(("file", f, "len", mmss(info[f][0])))
            emit(("dur", f, info[f][0]))
        total = sum(d for d, _ in info.values()) or 1

        # 1. listen
        emit(("stage", 0, "run"))
        dbs, base = {}, 0.0
        for f in files:
            chk()
            dur, name = info[f][0], Path(f).name
            apath = cache_paths(proj, f, cfg["track"], cfg["speech_model"])[0]

            def frac(x):
                emit(("prog", S(0, (base + x * dur) / total)))
                emit(("sub", x, f"Listening to {name}  ·  {x * 100:.0f}%"))
                emit(("file", f, "status", f"Listening {x * 100:.0f}%"))

            if apath.exists():
                db = np.load(apath)
                frac(1)
            else:
                db = loudness(f, cfg["track"], dur, frac, cancel)
                if not len(db) and cfg["track"]:
                    db = loudness(f, 0, dur, frac, cancel)
                if len(db):
                    np.save(apath, db)
            dbs[f] = db
            base += dur
        emit(("stage", 0, "done"))

        # 2. watch
        emit(("stage", 1, "run"))
        viss, base = {}, 0.0
        if vis_on:
            for f in files:
                chk()
                dur, name = info[f][0], Path(f).name
                vpath = cache_paths(proj, f, cfg["track"], cfg["speech_model"])[1]

                def frac(x):
                    emit(("prog", S(1, (base + x * dur) / total)))
                    emit(("sub", x, f"Watching {name}  ·  {x * 100:.0f}%"))
                    emit(("file", f, "status", f"Watching {x * 100:.0f}%"))

                if vpath.exists():
                    v = np.load(vpath)
                    frac(1)
                else:
                    v = watch(f, dur, frac, cancel)
                    if len(v):
                        np.save(vpath, v)
                viss[f] = v if len(v) else None
                base += dur
        emit(("stage", 1, "done"))

        # 3. read speech
        emit(("stage", 2, "run"))
        trs, base = {}, 0.0
        if sp_on:
            for f in files:
                chk()
                dur, name = info[f][0], Path(f).name
                tpath = cache_paths(proj, f, cfg["track"], cfg["speech_model"])[2]

                def frac(x):
                    emit(("prog", S(2, (base + x * dur) / total)))
                    emit(("sub", x, f"Reading speech in {name}  ·  {x * 100:.0f}%"))
                    emit(("file", f, "status", f"Reading speech {x * 100:.0f}%"))

                if tpath.exists():
                    trs[f] = [tuple(x) for x in json.loads(tpath.read_text(encoding="utf-8"))]
                    frac(1)
                else:
                    tr = transcribe(f, cfg["track"], dur, cfg["speech_model"], frac, cancel,
                                    lambda m: emit(("sub", None, m)))
                    if tr is None:
                        run_notes.append("Speech reading is not part of this build, so it went without it")
                        break
                    tpath.write_text(json.dumps(tr), encoding="utf-8")
                    trs[f] = tr
                base += dur
        emit(("stage", 2, "done"))

        # 4. pick
        emit(("stage", 3, "run"))
        plans, cuts, peaks = {}, [], []
        for f in files:
            if not len(dbs[f]):
                emit(("file", f, "status", "No audio"))
                continue
            emit(("analysis", f, dbs[f], viss.get(f), trs.get(f)))
            sc = scores(dbs[f], viss.get(f), trs.get(f))
            pl = plan(sc, cfg)
            plans[f] = (sc, pl)
            peaks.append((*best_moment(sc, pl["w"]), f, len(sc.a) * WIN))
            emit(("file", f, "status", f"{len(pl['segs'])} moments"))
            emit(("file", f, "kept", mmss(sum(e - s for s, e, _ in pl["segs"]))))
            cuts += [(f, s, e, "") for s, e, _ in pl["segs"]]
        hook_at, mode = None, cfg["ai"]
        if mode != "off" and not any(sc.tr for sc, _ in plans.values()):
            run_notes.append("The AI director needs speech reading, so it was skipped")
        elif mode != "off":
            try:
                if mode == "claude":
                    if not cfg.get("api_key"):
                        raise RuntimeError("no API key was entered")
                    emit(("sub", None, "Asking Claude which moments are worth keeping…"))
                    rate, label, mx, tl = (lambda c: ask_claude(c, cfg["api_key"])), "Claude", 60, 450
                else:
                    emit(("sub", None, "Looking for your local AI…"))
                    ai = find_local_ai(start=True)
                    if ai is None:
                        raise RuntimeError("no local AI was found. Is Ollama running?")
                    model = choose_model(ai["models"], cfg.get("ai_model", ""))
                    if not model:
                        if not cfg.get("ai_pull_ok"):
                            raise RuntimeError("no model is installed in your local AI")
                        model = pull_model(ai, cancel, lambda x: (
                            emit(("prog", S(3, 0.4 * x))), emit(("sub", x, f"Downloading a small AI model, one time  ·  {x * 100:.0f}%"))))

                    def rate(c):
                        emit(("sub", None, f"AI director ({model}) is reading the moments…"))
                        return ask_local(c, ai, model, cancel, lambda x: (
                            emit(("prog", S(3, 0.4 + 0.6 * x))), emit(("sub", x, f"AI director ({model}) is rating moments  ·  {x * 100:.0f}%"))))
                    label, mx, tl = f"local AI, {model}", 40, 300
                cuts, hook_at, msg = direct(plans, [f for f in files if f in plans], cfg, info, rate, mx, tl, label)
                run_notes.append(msg)
            except Cancelled:
                raise
            except Exception as ex:
                run_notes.append(f"AI director skipped: {ex}. Used the automatic picks instead")
        cuts = [(f, s, e, t or quick_title(trs.get(f), s, e)) for f, s, e, t in cuts]      # free titles where the AI gave none
        hook = bool(cfg["hook"] and len(cuts) >= 3)
        if hook:                                           # cold open: tease the single best moment first
            if hook_at:
                f0, t0 = hook_at
                d0 = info[f0][0]
            else:
                _, t0, f0, d0 = max(peaks)
            cuts.insert(0, (f0, max(0.0, t0 - 5), min(d0, t0 + 5), ""))    # 10 s, the shortest YouTube chapter
        emit(("prog", S(3, 1)))
        emit(("stage", 3, "done"))
        if not cuts:
            raise RuntimeError("No good moments found. Try 'Fine-tune', lowering 'How picky', or choosing a "
                               "different audio track.")

        # 5. render
        emit(("stage", 4, "run"))
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
              f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={FPS},format=yuv420p")
        enc = (["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "21"] if cfg["gpu"]
               else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"])
        tot, done, parts = sum(e - s for _, s, e, _ in cuts), 0.0, []
        for i, (f, s, e, _t) in enumerate(cuts):
            chk()
            emit(("sub", done / tot, f"Rendering clip {i + 1} of {len(cuts)}"))
            emit(("prog", S(4, done / tot)))
            p = tmp / f"{i:04d}.mp4"
            ff(["ffmpeg", "-y", "-v", "error", "-ss", f"{s:.2f}", "-i", f, "-t", f"{e - s:.2f}",
                "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf, *enc,
                "-af", f"afade=t=in:d=0.04,afade=t=out:st={max(0.0, e - s - 0.04):.3f}:d=0.04",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(p)])
            done += e - s
            parts.append(p)
        emit(("stage", 4, "done"))

        # 6. export
        emit(("stage", 5, "run"))
        emit(("sub", 0.5, "Joining clips and writing edit files"))
        stem = "rough_cut_" + time.strftime("%Y%m%d_%H%M")
        (out / f"{stem}_cuts.txt").write_text(
            "\n".join(("[cold open] " if i == 0 and hook else "") + f"{Path(f).name}   {mmss(s)} - {mmss(e)}"
                      + (f"   {t}" if t else "") for i, (f, s, e, t) in enumerate(cuts)), encoding="utf-8")
        ch = chapters(cuts, hook)
        if ch:
            (out / f"{stem}_chapters.txt").write_text(ch, encoding="utf-8")
            run_notes.append("Wrote YouTube chapters")
        fps = info[files[0]][1]
        tc = lambda n: f"{n // (fps * 3600):02}:{n // (fps * 60) % 60:02}:{n // fps % 60:02}:{n % fps:02}"
        lines, rec = [f"TITLE: {proj.name}", "FCM: NON-DROP FRAME", ""], fps * 3600
        for i, (f, s, e, _t) in enumerate(cuts, 1):
            a, b = round(s * fps), round(e * fps)
            lines += [f"{i:03}  AX       AA/V  C        {tc(a)} {tc(b)} {tc(rec)} {tc(rec + b - a)}",
                      f"* FROM CLIP NAME: {Path(f).name}", ""]
            rec += b - a
        (out / f"{stem}.edl").write_text("\n".join(lines), encoding="utf-8")
        lst = tmp / "list.txt"
        lst.write_text("".join("file '" + q.as_posix().replace("'", "'\\''") + "'\n" for q in parts), encoding="utf-8")
        final = out / f"{stem}.mp4"
        post = (["-c:v", "copy", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k"]
                if cfg["normalize"] else ["-c", "copy"])          # level the volume for YouTube (-14 LUFS)
        ff(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), *post, str(final)])
        emit(("stage", 5, "done"))
        emit(("prog", 1.0))
        emit(("sub", 1.0, "Finished"))
        emit(("done", dict(video=final, folder=out, moments=len(cuts), kept=tot, src=total, hook=hook,
                           notes=run_notes)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
