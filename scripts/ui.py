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
from zoneinfo import ZoneInfo

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
PT, TW = ZoneInfo("America/Los_Angeles"), ZoneInfo("Asia/Taipei")
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


def _write_lines(path, lines):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


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
    with open(os.path.join(d, LAST_NAME), "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=1)


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


def selfcheck():
    global LAB
    tmp = tempfile.mkdtemp(prefix="gvl_ui_check_")
    old, LAB = LAB, tmp
    try:
        _check_core(tmp)
        print("selfcheck OK")
    finally:
        LAB = old
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="gemini-voice-lab 本機微調介面")
    ap.add_argument("--selfcheck", action="store_true", help="離線自檢（不連網、0 額度）")
    a = ap.parse_args()
    if a.selfcheck:
        selfcheck()
        return
    ap.error("伺服器還沒做（Task 3）")


if __name__ == "__main__":
    main()
