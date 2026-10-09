# -*- coding: utf-8 -*-
"""gemini-voice-lab: 本機微調介面——生成（花額度）＋後製（0 額度）＋回合日誌，只綁 127.0.0.1。

  python scripts\\ui.py                          # 開 http://127.0.0.1:8765/ 並自動開瀏覽器
  python scripts\\ui.py --port 9000 --no-browser
  python scripts\\ui.py --selfcheck              # 離線自檢（不連網、0 額度）

生成呼叫同目錄 tts_gemini.py（固定 --one-request）；後製呼叫 stretch_pauses.py ＋ ffmpeg rubberband。
每輪寫進 lab\\<角色>\\回合日誌.md 的「輪次」表；評語歸納成 learnings/rules.md 仍走 SKILL.md 回合迴圈，這裡不碰。
金鑰只交給子程序，不回前端、不寫檔、不印。
"""
import argparse
import datetime
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import webbrowser
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
try:
    from tts_gemini import VOICES
    from voices import _key
except ImportError as e:
    sys.exit(f"找不到同目錄的 tts_gemini.py／voices.py（{e}）")

LAB = os.path.join(ROOT, "lab")  # selfcheck 會暫時換成暫存夾
TEMPLATE_LOG = os.path.join(ROOT, "examples", "範例角色", "回合日誌.md")
LOG_NAME, LAST_NAME, PREVIEW_NAME = "回合日誌.md", ".ui_last.json", ".preview.mp3"
def _zones(zi=ZoneInfo):
    """Windows 的 Python 沒內建 IANA 時區庫，要 pip 裝 tzdata；缺了就講清楚再退出。"""
    try:
        return zi("America/Los_Angeles"), zi("Asia/Taipei")
    except ZoneInfoNotFoundError as e:
        sys.exit(f"缺時區資料（{e}）：pip install tzdata")


PT, TW = _zones()
FLASH_DAILY, FLASH_RESERVE = 10, 2
AUDIO_EXT = (".mp3", ".wav", ".m4a")
BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
VOICE_OK = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,79}$")
RND = re.compile(r"^r(\d+)", re.I)
BASE_LINE = re.compile(r"^\s*底色\s*[:：]\s*(.*)$")
GEN_KEYS = [("voice", "音色"), ("model", "模型"), ("base", "底色"), ("cues", "台詞"), ("styles", "風格表")]


# ---------- 名稱與路徑 ----------
def safe_name(name: str) -> str:
    """角色名／檔名只能是單層名稱：不含路徑分隔、磁碟代號、..、控制字元、前後空白。違反 → ValueError。"""
    if not isinstance(name, str) or not name or name != name.strip() or ".." in name or BAD_NAME.search(name):
        raise ValueError(f"不合法的名稱：{name!r}")
    return name


def role_dir(role: str) -> str:
    d = os.path.join(LAB, safe_name(role))
    if not os.path.isdir(d):
        raise ValueError(f"沒有這個角色資料夾：lab\\{role}")
    return d


def list_roles() -> list:
    if not os.path.isdir(LAB):
        return []
    return sorted(n for n in os.listdir(LAB) if not n.startswith(".") and os.path.isdir(os.path.join(LAB, n)))


def list_audio(d: str) -> list:
    return sorted(n for n in os.listdir(d) if n.lower().endswith(AUDIO_EXT) and not n.startswith("."))


# ---------- 回合日誌（lab\<角色>\回合日誌.md 的「## 輪次」表） ----------
def _read_lines(path) -> list:
    with open(path, "r", encoding="utf-8-sig") as f:
        return f.read().splitlines()


def _write_text(path, text: str):
    """先編碼、寫暫存檔再 os.replace：編碼或寫入失敗都不會把原檔截成空的（lab\\ 不進 git，壞了救不回）。"""
    data = text.encode("utf-8")
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _write_lines(path, lines):
    _write_text(path, "\n".join(lines) + "\n")


def _split_row(line: str) -> list:
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") else s
    return [c.strip() for c in s.split("|")]


def _join_row(cells) -> str:
    return "| " + " | ".join(cells) + " |"


def _cell(v) -> str:
    """寫進表格的值：| 會切壞欄位、換行會切壞列 → 換成全形斜線與空白。"""
    return re.sub(r"\s*[\r\n]+\s*", " ", str(v)).replace("|", "／").strip()


def _table(lines):
    """→ (表頭 index, 最後一列 index, 欄名)。找不到「## 輪次」或其下表格 → ValueError（不覆寫使用者手改的檔）。"""
    h = next((i for i, l in enumerate(lines) if l.strip().startswith("## 輪次")), None)
    if h is None:
        raise ValueError("回合日誌找不到「## 輪次」段落，請檢查檔案")
    i = h + 1
    while i < len(lines) and not lines[i].lstrip().startswith("|") and not lines[i].startswith("## "):
        i += 1
    if i >= len(lines) or not lines[i].lstrip().startswith("|"):
        raise ValueError("回合日誌「## 輪次」底下沒有表格，請檢查檔案")
    start = i
    while i + 1 < len(lines) and lines[i + 1].lstrip().startswith("|"):
        i += 1
    return start, i, _split_row(lines[start])


def ensure_log(d: str) -> str:
    """沒有日誌就用 examples\\範例角色\\回合日誌.md 的空表建一份（<角色> 換成資料夾名）。"""
    path = os.path.join(d, LOG_NAME)
    if not os.path.exists(path):
        _write_lines(path, [l.replace("<角色>", os.path.basename(d)) for l in _read_lines(TEMPLATE_LOG)])
    return path


def read_rounds(path: str) -> list:
    lines = _read_lines(path)
    start, end, head = _table(lines)
    rows = []
    for l in lines[start + 1: end + 1]:
        cells = _split_row(l)
        if all(set(c) <= set("-: ") for c in cells):  # |---| 分隔列
            continue
        rows.append(dict(zip(head, cells + [""] * (len(head) - len(cells)))))
    return rows


def append_round(path: str, row: dict):
    lines = _read_lines(path)
    _, end, head = _table(lines)
    lines.insert(end + 1, _join_row([_cell(row.get(c, "")) for c in head]))
    _write_lines(path, lines)


def set_comment(path: str, rnd: str, comment: str):
    lines = _read_lines(path)
    start, end, head = _table(lines)
    if "使用者原話" not in head:
        raise ValueError("回合日誌表頭沒有「使用者原話」欄")
    k = head.index("使用者原話")
    for i in range(start + 1, end + 1):
        cells = _split_row(lines[i])
        if cells and cells[0] == rnd:
            cells += [""] * (len(head) - len(cells))
            cells[k] = _cell(comment)
            lines[i] = _join_row(cells)
            _write_lines(path, lines)
            return
    raise ValueError(f"回合日誌裡沒有 {rnd}")


def next_round(d: str, rows: list) -> str:
    names = [r.get("輪", "") for r in rows] + os.listdir(d)
    nums = [int(m.group(1)) for m in (RND.match(n) for n in names) if m]
    return f"r{max(nums, default=0) + 1:02d}"


# ---------- 額度帳面（429 才是真相） ----------
def now_tw() -> datetime.datetime:
    return datetime.datetime.now(TW)


def pt_day(t: datetime.datetime) -> datetime.date:
    return t.astimezone(PT).date()


def parse_tw(s: str):
    """日誌日期欄：「2026-10-09 14:30」或舊格式「2026-10-09」（視為台灣 00:00）。看不懂 → None。"""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s.strip(), fmt).replace(tzinfo=TW)
        except ValueError:
            pass
    return None


def flash_used(rows: list, now=None) -> int:
    """今天（太平洋時間日界）模型＝flash 的列數。"""
    today = pt_day(now or now_tw())
    n = 0
    for r in rows:
        t = parse_tw(r.get("日期", ""))
        if r.get("模型", "").strip().lower() == "flash" and t and pt_day(t) == today:
            n += 1
    return n


def flash_used_all(now=None) -> int:
    """額度是整把 key 共用：lab\\ 底下所有角色日誌的 flash 列加總（讀不了的日誌略過）。"""
    n = 0
    for r in list_roles():
        path = os.path.join(LAB, r, LOG_NAME)
        if os.path.exists(path):
            try:
                n += flash_used(read_rounds(path), now)
            except (ValueError, OSError):
                pass
    return n


def reset_at(now=None) -> str:
    """下一次額度重置的台灣時間 HH:MM（PDT 15:00、PST 16:00）。"""
    n = now or now_tw()
    nxt = datetime.datetime.combine(pt_day(n) + datetime.timedelta(days=1), datetime.time(0), PT)
    return nxt.astimezone(TW).strftime("%H:%M")


def is_quota_error(msg: str) -> bool:
    return "額度用完" in msg or "per day" in msg.lower() or "PerDay" in msg


# ---------- 風格表與參數 ----------
def split_styles(text: str):
    """styles.txt → (底色, 其餘行)。只取第一行「底色:」。"""
    base, rest = None, []
    for line in text.splitlines():
        m = BASE_LINE.match(line)
        if m and base is None:
            base = m.group(1).strip()
        else:
            rest.append(line)
    return base or "", "\n".join(rest).strip("\n")


def join_styles(base: str, rest: str) -> str:
    out = [f"底色: {base.strip()}"] if base.strip() else []
    if rest.strip():
        out.append(rest.strip("\n"))
    return "\n".join(out) + "\n" if out else ""


def diff_params(prev: dict, cur: dict) -> str:
    if not prev:
        return "基線"
    ch = [label for k, label in GEN_KEYS if (prev.get(k) or "").strip() != (cur.get(k) or "").strip()]
    return "、".join(ch) if ch else "沒改（重抽）"


def load_last(d: str) -> dict:
    try:
        with open(os.path.join(d, LAST_NAME), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_last(d: str, cur: dict):
    _write_text(os.path.join(d, LAST_NAME), json.dumps(cur, ensure_ascii=False, indent=1))


# ---------- 子程序與後製（0 額度） ----------
def _env(extra=None) -> dict:
    return {**os.environ, "PYTHONUTF8": "1", **(extra or {})}


def _run(cmd: list, timeout=600) -> str:
    """跑子程序；非 0 → RuntimeError（訊息＝stderr 末段，tts_gemini／ffmpeg 本身已是可讀訊息）。回 stdout。"""
    try:
        r = subprocess.run(cmd, capture_output=True, env=_env(), timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"逾時（{timeout} 秒）：{os.path.basename(cmd[1] if cmd[0] == sys.executable else cmd[0])}")
    out, err = (b.decode("utf-8", "replace") for b in (r.stdout, r.stderr))
    if r.returncode != 0:
        raise RuntimeError((err.strip() or out.strip() or f"exit {r.returncode}")[-600:])
    return out


FX_RANGE = {"semitones": (-3.0, 3.0, 0.0), "tempo": (0.8, 1.2, 1.0), "pause_add": (0.0, 1.0, 0.0), "gain_db": (-6.0, 6.0, 0.0)}


def fx_params(p: dict) -> dict:
    """前端滑桿值 → 夾在範圍內的 float；缺值用預設；非數字 → ValueError。"""
    out = {}
    for k, (lo, hi, dv) in FX_RANGE.items():
        try:
            v = float(p.get(k, dv))
        except (TypeError, ValueError):
            raise ValueError(f"{k} 不是數字")
        if not math.isfinite(v):
            raise ValueError(f"{k} 不是數字")
        out[k] = min(hi, max(lo, v))
    return out


def postfx_filter(semitones=0.0, tempo=1.0, gain_db=0.0) -> str:
    """ffmpeg -af 字串；全為預設 → ""。formant=preserved：升降調不變花栗鼠。"""
    parts = []
    if abs(semitones) > 1e-9 or abs(tempo - 1.0) > 1e-9:
        parts.append(f"rubberband=pitch={2 ** (semitones / 12):.4f}:tempo={tempo:.4f}:formant=preserved")
    if abs(gain_db) > 1e-9:
        parts.append(f"volume={gain_db:g}dB")
    return ",".join(parts)


def render_post(src: str, out: str, semitones=0.0, tempo=1.0, pause_add=0.0, gain_db=0.0):
    """停頓（stretch_pauses.py）→ 音高／語速／音量（ffmpeg）→ out。試聽與存檔共用，試聽即成品。"""
    tmp = tempfile.mkdtemp(prefix="gvl_ui_")
    try:
        cur = src
        if pause_add > 0:
            cur = os.path.join(tmp, "stretched.wav")
            _run([sys.executable, os.path.join(HERE, "stretch_pauses.py"), "--in", src, "--out", cur,
                  "--add", f"{pause_add:g}", "--min", "0.5"])
        af = postfx_filter(semitones, tempo, gain_db)
        codec = ["-q:a", "2"] if out.lower().endswith(".mp3") else []
        _run(["ffmpeg", "-y", "-v", "error", "-i", cur, *(["-af", af] if af else []), *codec, out])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def post_summary(src_name: str, fx: dict) -> str:
    parts = []
    if fx["semitones"]:
        parts.append(f"音高 {fx['semitones']:+g} 半音")
    if fx["tempo"] != 1.0:
        parts.append(f"語速 {fx['tempo']:.2f}×")
    if fx["pause_add"]:
        parts.append(f"停頓 +{fx['pause_add']:.2f} 秒")
    if fx["gain_db"]:
        parts.append(f"音量 {fx['gain_db']:+g} dB")
    return f"後製 {src_name}：" + ("、".join(parts) if parts else "無變更（轉檔）")


# ---------- 動作（HTTP 路由呼叫；ValueError→400、Busy→409、RuntimeError→502） ----------
class Busy(Exception):
    pass


GEN_LOCK, PREVIEW_LOCK = threading.Lock(), threading.Lock()
STATE = {"exhausted_day": None}  # 今天收過每日額度 429 → 記太平洋日期
_VOICES = {}


def need_key():
    """沒金鑰 → RuntimeError；有就補進本程序 env（子程序繼承）。selfcheck 會換掉它。"""
    try:
        _key()
    except SystemExit:
        raise RuntimeError("沒有 GEMINI_API_KEY（到 https://aistudio.google.com/apikey 建一把，setx 後重開介面）")


def _gen_params(p: dict) -> dict:
    voice, model = str(p.get("voice") or "Sulafat"), str(p.get("model") or "flash")
    if not VOICE_OK.match(voice):
        raise ValueError(f"音色名稱不合法：{voice!r}")
    if model not in ("flash", "lite"):
        raise ValueError(f"模型只能是 flash 或 lite：{model!r}")
    return {"voice": voice, "model": model, "base": str(p.get("base") or ""),
            "cues": str(p.get("cues") or ""), "styles": str(p.get("styles") or "")}


def save_inputs(d: str, g: dict):
    _write_text(os.path.join(d, "cues.txt"), g["cues"].replace("\r\n", "\n"))
    _write_text(os.path.join(d, "styles.txt"), join_styles(g["base"], g["styles"].replace("\r\n", "\n")))


def tts_cmd(d: str, g: dict, out: str, dry: bool) -> list:
    return [sys.executable, os.path.join(HERE, "tts_gemini.py"), "--cues", os.path.join(d, "cues.txt"),
            "--styles", os.path.join(d, "styles.txt"), "--voice", g["voice"], "--model", g["model"],
            "--out", out, "--dry-run" if dry else "--one-request"]


def get_state(role: str) -> dict:
    d = role_dir(role)

    def read(name):
        try:
            with open(os.path.join(d, name), encoding="utf-8-sig") as f:
                return f.read()
        except OSError:
            return ""
    base, styles = split_styles(read("styles.txt"))
    last, rows, log_error = load_last(d), [], ""
    if os.path.exists(os.path.join(d, LOG_NAME)):
        try:
            rows = read_rounds(os.path.join(d, LOG_NAME))
        except ValueError as e:
            log_error = str(e)
    return {"cues": read("cues.txt"), "base": base, "styles": styles, "last": last,
            "voice": last.get("voice") or "Sulafat", "model": last.get("model") or "flash",
            "rounds": rows, "log_error": log_error, "audio": list_audio(d), "next": next_round(d, rows),
            "used": flash_used_all(), "daily": FLASH_DAILY, "reserve": FLASH_RESERVE,
            "exhausted": STATE["exhausted_day"] == pt_day(now_tw()), "reset": reset_at()}


def get_voices() -> dict:
    """預製 30 款＋自建音色（voices.py list --json；不計 TTS 額度）。成功才快取，失敗下次重試。"""
    presets = [{"name": n, "trait": t, "note": note} for n, t, note in VOICES]
    if "custom" not in _VOICES:
        try:
            listed = json.loads(_run([sys.executable, os.path.join(HERE, "voices.py"), "list", "--json"], timeout=60))
            _VOICES["custom"] = [{"id": v["id"], "display_name": v.get("display_name") or v["id"]} for v in listed]
        except (RuntimeError, ValueError, KeyError, TypeError) as e:
            return {"presets": presets, "custom": [], "error": str(e)[-200:]}
    return {"presets": presets, "custom": _VOICES["custom"], "error": ""}


def do_dryrun(p: dict) -> dict:
    d, g = role_dir(p.get("role", "")), _gen_params(p)
    save_inputs(d, g)
    out = os.path.join(tempfile.gettempdir(), "gvl_ui_dry.wav")
    return {"table": _run(tts_cmd(d, g, out, dry=True)).strip()}


def do_generate(p: dict) -> dict:
    d, g = role_dir(p.get("role", "")), _gen_params(p)
    if not GEN_LOCK.acquire(blocking=False):
        raise Busy("上一個生成還在跑，等它完成")
    try:
        log = ensure_log(d)
        rows = read_rounds(log)  # 日誌壞了就在花額度前停下
        save_inputs(d, g)
        need_key()
        rnd = next_round(d, rows)
        out_name = rnd + ".mp3"
        try:
            stdout = _run(tts_cmd(d, g, os.path.join(d, out_name), dry=False))
        except RuntimeError as e:
            if is_quota_error(str(e)):
                STATE["exhausted_day"] = pt_day(now_tw())
            raise
        flash = g["model"] == "flash"
        used = flash_used_all() + (1 if flash else 0)
        append_round(log, {"輪": rnd, "日期": now_tw().strftime("%Y-%m-%d %H:%M"), "模型": g["model"],
                           "flash": f"{used}/{FLASH_DAILY}" if flash else "-", "只改了什麼": diff_params(load_last(d), g)})
        save_last(d, g)
        m = re.search(r"(\d+) beats", stdout)
        return {"round": rnd, "file": out_name, "beats": int(m.group(1)) if m else 0, "used": used}
    finally:
        GEN_LOCK.release()


def _src(d: str, p: dict):
    name = safe_name(str(p.get("src") or ""))
    path = os.path.join(d, name)
    if not name.lower().endswith(AUDIO_EXT) or not os.path.isfile(path):
        raise ValueError(f"來源檔不存在：{name}")
    return name, path


def do_preview(p: dict) -> dict:
    d = role_dir(p.get("role", ""))
    _, src = _src(d, p)
    fx = fx_params(p)
    with PREVIEW_LOCK:
        render_post(src, os.path.join(d, PREVIEW_NAME), **fx)
    return {"file": PREVIEW_NAME}


def do_save(p: dict) -> dict:
    d = role_dir(p.get("role", ""))
    name, src = _src(d, p)
    fx = fx_params(p)
    if not GEN_LOCK.acquire(blocking=False):  # 生成中 rNN.mp3 還沒落地，搶號會撞同一輪
        raise Busy("生成還在跑，等它完成再存檔（試聽照常可用）")
    try:
        log = ensure_log(d)
        rnd = next_round(d, read_rounds(log))
        render_post(src, os.path.join(d, rnd + ".mp3"), **fx)
        append_round(log, {"輪": rnd, "日期": now_tw().strftime("%Y-%m-%d %H:%M"), "模型": "後製", "flash": "-",
                           "只改了什麼": post_summary(name, fx)})
        return {"round": rnd, "file": rnd + ".mp3"}
    finally:
        GEN_LOCK.release()


def do_comment(p: dict) -> dict:
    d = role_dir(p.get("role", ""))
    set_comment(ensure_log(d), str(p.get("round") or ""), str(p.get("comment") or ""))
    return {"ok": True}


# ---------- HTTP ----------
TOKEN = secrets.token_urlsafe(16)
ALLOWED_HOSTS = set()  # 綁好埠後填入；擋 DNS rebinding
ROUTES = {"/api/dryrun": do_dryrun, "/api/generate": do_generate, "/api/preview": do_preview,
          "/api/save": do_save, "/api/comment": do_comment}
MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _guard(self, post: bool) -> bool:
        if self.headers.get("Host", "") not in ALLOWED_HOSTS:
            self._send(403, {"error": "Host 不符（只接受 127.0.0.1／localhost）"})
            return False
        if post and not secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN):
            self._send(403, {"error": "token 不符，請重新整理頁面"})
            return False
        return True

    def _dispatch(self, fn, *a):
        try:
            self._send(200, fn(*a))
        except Busy as e:
            self._send(409, {"error": str(e)})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 — 子程序／檔案錯誤原樣給前端看
            self._send(502 if isinstance(e, RuntimeError) else 500, {"error": str(e)})

    def _audio(self, rel: str):
        try:
            parts = rel.split("/")
            if len(parts) != 2:
                raise ValueError("路徑不合法")
            d, name = role_dir(parts[0]), safe_name(parts[1])
            ext = os.path.splitext(name)[1].lower()
            path = os.path.join(d, name)
            if ext not in MIME or not os.path.isfile(path):
                return self._send(404, {"error": f"沒有這個音檔：{name}"})
            with open(path, "rb") as f:
                self._send(200, f.read(), MIME[ext])
        except ValueError as e:
            self._send(400, {"error": str(e)})

    def do_GET(self):
        if not self._guard(post=False):
            return
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/":
            with open(os.path.join(HERE, "ui.html"), encoding="utf-8") as f:
                return self._send(200, f.read().replace("__TOKEN__", TOKEN).encode("utf-8"), "text/html; charset=utf-8")
        if u.path == "/api/roles":
            return self._dispatch(lambda: {"roles": list_roles()})
        if u.path == "/api/state":
            return self._dispatch(get_state, q.get("role", ""))
        if u.path == "/api/voices":
            return self._dispatch(get_voices)
        if u.path.startswith("/audio/"):
            return self._audio(unquote(u.path[len("/audio/"):]))
        self._send(404, {"error": "找不到"})

    def do_POST(self):
        if not self._guard(post=True):
            return
        fn = ROUTES.get(urlparse(self.path).path)
        if not fn:
            return self._send(404, {"error": "找不到"})
        try:
            p = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if not isinstance(p, dict):
                raise ValueError
        except ValueError:
            return self._send(400, {"error": "請求內容不是 JSON 物件"})
        self._dispatch(fn, p)


def serve(port: int, open_browser: bool):
    os.makedirs(LAB, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    p = srv.server_address[1]
    ALLOWED_HOSTS.update({f"127.0.0.1:{p}", f"localhost:{p}"})
    url = f"http://127.0.0.1:{p}/"
    print(f"聲音實驗室介面：{url}　（Ctrl+C 結束）", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


# ---------- 自檢 ----------
def _check_core(tmp):
    # 名稱防護
    for bad in ["", "..", "../x", "a\\b", "a/b", "C:x", " a", "a\n"]:
        try:
            safe_name(bad)
            raise AssertionError(f"{bad!r} 不該通過")
        except ValueError:
            pass
    assert safe_name("範例角色") == "範例角色" and safe_name("r01.mp3") == "r01.mp3"
    # 日誌：範本建表 → 追加兩列 → 寫評語（含 | 與換行）→ 讀回；其他段落原樣保留
    d = os.path.join(tmp, "測試角色")
    os.makedirs(d)
    log = ensure_log(d)
    before = _read_lines(log)
    assert before[0] == "# 配音實驗室：測試角色" and read_rounds(log) == []
    append_round(log, {"輪": "r01", "日期": "2026-10-09 14:30", "模型": "flash", "flash": "1/10", "只改了什麼": "基線"})
    append_round(log, {"輪": "r02", "日期": "2026-10-09 14:40", "模型": "後製", "flash": "-",
                       "只改了什麼": "後製 r01.mp3：音高 +0.5 半音"})
    set_comment(log, "r01", "尾音拖|太平\n第二行")
    rows = read_rounds(log)
    assert [r["輪"] for r in rows] == ["r01", "r02"]
    assert rows[0]["使用者原話"] == "尾音拖／太平 第二行" and rows[0]["判定"] == "" and rows[1]["模型"] == "後製"
    after = _read_lines(log)
    k = before.index("## 輪次") + 3  # 段落標題、表頭、分隔列
    assert after[:k] == before[:k]
    assert after[after.index("## 下一棒"):] == before[before.index("## 下一棒"):]
    # Windows 記事本：BOM＋CRLF 也要能追加，且只有一張表
    crlf = os.path.join(d, "crlf.md")
    with open(crlf, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write("\n".join(_read_lines(TEMPLATE_LOG)) + "\n")
    append_round(crlf, {"輪": "r01", "模型": "flash"})
    assert [r["輪"] for r in read_rounds(crlf)] == ["r01"]
    with open(crlf, encoding="utf-8") as f:
        text = f.read()
    assert text.count("## 輪次") == 1 and text.count("| r01 |") == 1
    # 壞日誌：讀與寫都拒絕，檔案不動
    bad = os.path.join(d, "bad.md")
    _write_lines(bad, ["# x", "## 輪次", "沒有表格", "## 下一棒"])
    for fn in (lambda: read_rounds(bad), lambda: append_round(bad, {"輪": "r01"})):
        try:
            fn()
            raise AssertionError("壞日誌不該通過")
        except ValueError as e:
            assert "輪次" in str(e)
    assert _read_lines(bad) == ["# x", "## 輪次", "沒有表格", "## 下一棒"]
    # 輪次編號：日誌與檔名取最大值 +1；非 rNN 名稱不算；音檔清單略過 . 開頭
    for f in ("r01.mp3", "r03.wav", "h3_ref_01.wav", ".preview.mp3"):
        open(os.path.join(d, f), "wb").close()
    assert next_round(d, [{"輪": "r02"}, {"輪": "h3ref01"}]) == "r04"
    assert list_audio(d) == ["h3_ref_01.wav", "r01.mp3", "r03.wav"]
    assert "測試角色" in list_roles() and role_dir("測試角色") == d
    # 今日 flash 次數：太平洋日界（PDT：台灣 15:00；PST：台灣 16:00）
    tw = lambda s: datetime.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=TW)  # noqa: E731
    rows = [{"模型": "flash", "日期": "2026-10-09 14:59"},  # PDT 10-08 23:59 → 前一個額度日
            {"模型": "flash", "日期": "2026-10-09 15:00"},  # PDT 10-09 00:00 → 今天
            {"模型": "flash", "日期": "2026-10-10 14:00"},  # PDT 10-09 23:00 → 今天
            {"模型": "lite", "日期": "2026-10-10 14:00"},
            {"模型": "後製", "日期": "2026-10-10 14:00"},
            {"模型": "flash", "日期": "看不懂"}]
    assert flash_used(rows, tw("2026-10-10 14:30")) == 2
    assert parse_tw("2026-10-06") == tw("2026-10-06 00:00")
    assert reset_at(tw("2026-10-10 14:30")) == "15:00" and reset_at(tw("2026-12-10 14:30")) == "16:00"
    assert is_quota_error("今天的免費額度用完了（每天 10 次請求")
    assert is_quota_error("quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    assert not is_quota_error("Gemini 拒絕請求（400）: bad voice")
    # 風格表：底色行拆出來、再合回去不失真
    b, rest = split_styles("# 註解\n底色: calm, warm\n平穩: soft\n收尾: gentle\n")
    assert b == "calm, warm" and rest == "# 註解\n平穩: soft\n收尾: gentle"
    assert split_styles(join_styles(b, rest)) == (b, rest)
    assert join_styles("", "") == "" and split_styles("底色：全形冒號")[0] == "全形冒號"
    # 參數比對
    p = {"voice": "Sulafat", "model": "flash", "base": "a", "cues": "x", "styles": ""}
    assert diff_params({}, p) == "基線"
    assert diff_params(p, dict(p, base="b")) == "底色"
    assert diff_params(p, dict(p, base="b", cues="y")) == "底色、台詞"
    assert diff_params(p, dict(p)) == "沒改（重抽）"
    save_last(d, p)
    assert load_last(d) == p and load_last(tmp) == {}
    # 寫檔失敗（孤立 surrogate 編碼不了）不可把日誌截成空的
    snap = _read_lines(log)
    try:
        set_comment(log, "r01", "壞字\ud83d")
        raise AssertionError("孤立 surrogate 應失敗")
    except ValueError:
        pass
    assert _read_lines(log) == snap
    # Windows 沒裝 tzdata：明確叫人 pip install，不是 import 就炸 traceback

    class NoTz:
        def __init__(self, key):
            raise ZoneInfoNotFoundError(key)
    try:
        _zones(NoTz)
        raise AssertionError("沒有時區資料應退出")
    except SystemExit as e:
        assert "tzdata" in str(e)


def _tone_wav(path):
    """0.6 秒 440Hz ＋ 0.8 秒靜音 ＋ 0.6 秒 440Hz，24k mono 16-bit。"""
    import wave
    sr = 24000
    tone = [int(8000 * math.sin(2 * math.pi * 440 * i / sr)) for i in range(int(sr * 0.6))]
    pcm = tone + [0] * int(sr * 0.8) + tone
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(x.to_bytes(2, "little", signed=True) for x in pcm))


def _check_post(tmp):
    import wave

    def dur(p):
        with wave.open(p) as w:
            return w.getnframes() / w.getframerate()
    d = os.path.join(tmp, "後製角色")
    os.makedirs(d)
    src = os.path.join(d, "src.wav")
    _tone_wav(src)
    assert postfx_filter() == ""
    assert postfx_filter(semitones=1) == "rubberband=pitch=1.0595:tempo=1.0000:formant=preserved"
    assert postfx_filter(gain_db=-2) == "volume=-2dB"
    out = os.path.join(d, "a.wav")
    render_post(src, out)                      # 全預設＝直接轉檔
    assert abs(dur(out) - 2.0) < 0.02, dur(out)
    render_post(src, out, tempo=1.2)           # 快 1.2× → 約 1.67 秒
    assert abs(dur(out) - 2.0 / 1.2) < 0.05, dur(out)
    render_post(src, out, pause_add=0.3)       # 0.8 秒空白 +0.3 → 2.3 秒
    assert abs(dur(out) - 2.3) < 0.03, dur(out)
    mp3 = os.path.join(d, "b.mp3")
    render_post(src, mp3, semitones=1, gain_db=-2)
    assert os.path.getsize(mp3) > 1000
    try:
        render_post(os.path.join(d, "沒這個.wav"), out)
        raise AssertionError("來源不存在應失敗")
    except RuntimeError:
        pass
    assert fx_params({"semitones": 9, "tempo": "1.1"}) == {"semitones": 3.0, "tempo": 1.1, "pause_add": 0.0, "gain_db": 0.0}
    for bad in ({"tempo": "快"}, {"gain_db": None}, {"semitones": float("nan")}):
        try:
            fx_params(bad)
            raise AssertionError(f"{bad} 不該通過")
        except ValueError:
            pass
    assert post_summary("r01.mp3", fx_params({})) == "後製 r01.mp3：無變更（轉檔）"
    assert post_summary("r01.mp3", fx_params({"semitones": 0.5, "pause_add": 0.3})) == "後製 r01.mp3：音高 +0.5 半音、停頓 +0.30 秒"
    assert post_summary("r01.mp3", fx_params({"tempo": 0.9, "gain_db": -2})) == "後製 r01.mp3：語速 0.90×、音量 -2 dB"


def _check_http(tmp):
    import http.client
    from urllib.parse import quote
    global _run, need_key
    role = "介面角色"
    d = os.path.join(tmp, role)
    os.makedirs(d)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    ALLOWED_HOSTS.update({f"127.0.0.1:{port}", f"localhost:{port}"})
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def req(method, path, body=None, token=TOKEN, host=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
        h = {"Content-Type": "application/json", "X-Token": token}
        if host:
            h["Host"] = host
        c.request(method, path, None if body is None else json.dumps(body), h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, (json.loads(raw) if r.getheader("Content-Type", "").startswith("application/json") else raw)

    state = lambda: req("GET", "/api/state?role=" + quote(role))[1]  # noqa: E731
    real_run, real_key = _run, need_key
    try:
        # 首頁注入 token；Host／token 防護
        st, html = req("GET", "/")
        assert st == 200 and TOKEN.encode() in html and b"__TOKEN__" not in html
        assert req("GET", "/api/roles", host="evil.example:80")[0] == 403
        assert req("POST", "/api/comment", {"role": role}, token="x")[0] == 403
        assert role in req("GET", "/api/roles")[1]["roles"]
        # 試切段：真跑 tts_gemini --dry-run（0 額度）；cues／styles 寫回檔
        form = {"role": role, "voice": "Sulafat", "model": "flash", "base": "calm",
                "cues": "【平穩】\n你好啊。\n【停 1.0】\n【收尾】\n晚安。\n", "styles": "平穩: soft"}
        st, j = req("POST", "/api/dryrun", form)
        assert st == 200 and "DRY-RUN" in j["table"], j
        s = state()
        assert s["base"] == "calm" and s["styles"] == "平穩: soft" and s["cues"] == form["cues"]
        assert s["rounds"] == [] and s["next"] == "r01" and s["daily"] == FLASH_DAILY
        base_used = s["used"]  # 其他自檢角色的 flash 列也算（額度是整把 key 共用）
        assert req("POST", "/api/dryrun", dict(form, cues="壞\ud83d"))[0] == 400
        assert state()["cues"] == form["cues"]  # 寫不進去也不可把 cues.txt 截空
        assert req("POST", "/api/dryrun", dict(form, voice="--evil"))[0] == 400
        assert req("POST", "/api/dryrun", dict(form, model="pro"))[0] == 400
        assert req("POST", "/api/dryrun", dict(form, role="../x"))[0] == 400
        # 生成：假 _run／need_key（不連網、不花額度）
        calls = []

        def fake_run(cmd, timeout=600):
            calls.append(cmd)
            with open(cmd[cmd.index("--out") + 1], "wb") as f:
                f.write(b"ID3fake")
            return "OK x (1 KB, 2.0s, 2 beats, gemini / Sulafat)\n"
        _run, need_key = fake_run, (lambda: None)
        st, j = req("POST", "/api/generate", form)
        assert st == 200 and j == {"round": "r01", "file": "r01.mp3", "beats": 2, "used": base_used + 1}, j
        assert "--one-request" in calls[-1] and "--dry-run" not in calls[-1]
        st, j = req("POST", "/api/generate", dict(form, base="warm"))
        assert st == 200 and j["round"] == "r02" and j["used"] == base_used + 2, j
        s = state()
        assert [(r["輪"], r["flash"], r["只改了什麼"]) for r in s["rounds"]] == [
            ("r01", f"{base_used + 1}/10", "基線"), ("r02", f"{base_used + 2}/10", "底色")]
        assert s["used"] == base_used + 2 and s["last"]["base"] == "warm"
        # 別的角色今天用掉的 flash 也要算進來
        other = os.path.join(tmp, "別的角色")
        os.makedirs(other)
        append_round(ensure_log(other), {"輪": "r01", "日期": now_tw().strftime("%Y-%m-%d %H:%M"), "模型": "flash"})
        base_used += 1
        assert state()["used"] == base_used + 2
        # 連點：生成中第二個請求 409
        GEN_LOCK.acquire()
        try:
            assert req("POST", "/api/generate", form)[0] == 409
        finally:
            GEN_LOCK.release()

        # 每日額度用完：502、不寫日誌、state 標記 exhausted
        def quota_run(cmd, timeout=600):
            raise RuntimeError("今天的免費額度用完了（每天 10 次請求，台灣時間 15:00 重置）。")
        _run = quota_run
        st, j = req("POST", "/api/generate", form)
        assert st == 502 and "額度用完" in j["error"], j
        s = state()
        assert s["exhausted"] and len(s["rounds"]) == 2
        STATE["exhausted_day"] = None
        # 日誌壞了 → 花額度前就停（_run 不能被叫到）、檔案不動
        _run = fake_run
        calls.clear()
        logp = os.path.join(d, LOG_NAME)
        good = _read_lines(logp)
        _write_lines(logp, ["# 壞掉的日誌"])
        st, j = req("POST", "/api/generate", form)
        assert st == 400 and "輪次" in j["error"] and calls == [], j
        assert _read_lines(logp) == ["# 壞掉的日誌"]
        _write_lines(logp, good)
    finally:
        _run, need_key = real_run, real_key
    # 後製（真 ffmpeg）＋評語＋音檔供應
    _tone_wav(os.path.join(d, "src.wav"))
    # 生成中不可存檔：tts_gemini 最後才寫 rNN.mp3，同時存檔會搶到同一個輪次編號
    GEN_LOCK.acquire()
    try:
        assert req("POST", "/api/save", {"role": role, "src": "src.wav"})[0] == 409
    finally:
        GEN_LOCK.release()
    st, j = req("POST", "/api/preview", {"role": role, "src": "src.wav", "semitones": 0.5})
    assert st == 200 and j == {"file": PREVIEW_NAME}, j
    st, j = req("POST", "/api/save", {"role": role, "src": "src.wav", "pause_add": 0.3})
    assert st == 200 and j == {"round": "r03", "file": "r03.mp3"}, j
    assert req("POST", "/api/save", {"role": role, "src": "沒這個.wav"})[0] == 400
    assert req("POST", "/api/preview", {"role": role, "src": "src.wav", "tempo": "快"})[0] == 400
    assert req("POST", "/api/comment", {"role": role, "round": "r03", "comment": "停頓剛好"})[0] == 200
    assert req("POST", "/api/comment", {"role": role, "round": "r99", "comment": "x"})[0] == 400
    s = state()
    last = s["rounds"][-1]
    assert (last["輪"], last["模型"], last["只改了什麼"], last["使用者原話"]) == ("r03", "後製", "後製 src.wav：停頓 +0.30 秒", "停頓剛好")
    assert s["used"] == base_used + 2 and "r03.mp3" in s["audio"] and PREVIEW_NAME not in s["audio"]
    st, raw = req("GET", f"/audio/{quote(role)}/r03.mp3")
    assert st == 200 and len(raw) > 1000
    assert req("GET", f"/audio/{quote(role)}/{quote(PREVIEW_NAME)}")[0] == 200
    assert req("GET", f"/audio/{quote(role)}/{quote(LAST_NAME)}")[0] == 404
    assert req("GET", "/audio/%2E%2E%2Fx/a.mp3")[0] == 400
    assert req("GET", f"/audio/{quote(role)}/..%5Cx.mp3")[0] == 400
    srv.shutdown()
    srv.server_close()


def selfcheck():
    global LAB
    tmp = tempfile.mkdtemp(prefix="gvl_ui_check_")
    old, LAB = LAB, tmp
    try:
        _check_core(tmp)
        _check_post(tmp)
        _check_http(tmp)
        print("selfcheck OK")
    finally:
        LAB = old
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="gemini-voice-lab 本機微調介面")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="不自動開瀏覽器")
    ap.add_argument("--selfcheck", action="store_true", help="離線自檢（不連網、0 額度）")
    a = ap.parse_args()
    if a.selfcheck:
        selfcheck()
        return
    serve(a.port, not a.no_browser)


if __name__ == "__main__":
    main()
