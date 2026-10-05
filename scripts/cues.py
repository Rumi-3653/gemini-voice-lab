# -*- coding: utf-8 -*-
"""gemini-voice-lab: 配音譜（cues）解析——只有純函式，無任何第三方相依。

配音譜格式（UTF-8 txt）:
  # 開頭的行是註解，不會唸
  【懸念】                     ← 情緒 preset，之後的文字都屬這段
  夜裡十一點，實驗室只剩我一個人。
  【停 1.2】                   ← 插入 1.2 秒留白（【停】預設 0.8 秒）
  【高潮】
  螢幕上的數字，動了！
  【收尾 rate=-30% pitch=-10Hz】 ← preset 後可再帶 rate=/pitch=/vol= 覆寫

內建 preset：平穩 / 低語 / 沉 / 懸念 / 感嘆 / 緊張 / 激動 / 高潮 / 收尾。
不認識的名稱（例如拼盤回合的【A】【B】【C】）會警告並當「平穩」處理；
真正的風格句由 tts_gemini.py 的 --styles 檔決定。

rate=/pitch=/vol= 覆寫會被解析並保留在事件裡，但 Gemini 不支援語速／音高／音量位移，
tts_gemini.py 會警告並忽略（PRESETS 的三個數字只用來判斷「有沒有被覆寫」）。

parse_cues(path) → [("say", 名稱, (dr, dp, dv), 文字)] 與 [("pause", 秒數)] 的事件序列。
"""
import re
import sys

# (rate%, pitchHz, volume%) 位移；在 Gemini 引擎裡只當「有沒有被覆寫」的比對基準。
PRESETS = {
    "平穩": (0, 0, 0),        # 敘事底色
    "低語": (-16, -8, -20),   # 貼耳、悄悄話
    "沉":   (-20, -8, -6),    # 沉重、哀傷、回憶
    "懸念": (-22, -5, -8),    # 吊住聽眾、慢半拍
    "感嘆": (-14, -4, 0),     # 唏噓、餘韻
    "緊張": (14, 3, 6),       # 步步逼近
    "激動": (20, 8, 14),      # 情緒外放
    "高潮": (16, 10, 16),     # 爆點（前面要先沉才炸得起來）
    "收尾": (-26, -9, -4),    # 落地、收束
}

MARKER = re.compile(r"^【(.+?)】\s*$")


def _num(s: str) -> int:
    return int(s.replace("%", "").replace("Hz", "").replace("hz", ""))


def parse_cues(path: str):
    """→ [("say", 名稱, (dr,dp,dv), 文字)] 與 [("pause", 秒數)] 的事件序列"""
    events = []
    cur_name, cur_delta, buf = "平穩", PRESETS["平穩"], []

    def flush():
        text = "\n".join(buf).strip()
        if text:
            events.append(("say", cur_name, cur_delta, text))
        buf.clear()

    with open(path, "r", encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line.lstrip().startswith("#"):
                continue
            m = MARKER.match(line.strip())
            if not m:
                buf.append(line)
                continue
            tokens = m.group(1).split()
            if not tokens:
                continue
            if tokens[0].startswith("停"):
                # 【停 1.2】/【停1.2】/【停】(預設0.8s)
                flush()
                arg = tokens[0][1:] or (tokens[1] if len(tokens) > 1 else "0.8")
                try:
                    sec = float(arg)
                except ValueError:
                    sec = 0.8
                events.append(("pause", sec))
                continue
            flush()
            name = tokens[0] if "=" not in tokens[0] else "自訂"
            dr, dp, dv = PRESETS.get(name, (0, 0, 0))
            if name not in PRESETS and name != "自訂":
                print(f"警告: 不認識的 preset「{name}」，當平穩處理（可用: {'/'.join(PRESETS)}）",
                      file=sys.stderr)
            for tok in (tokens if "=" in tokens[0] else tokens[1:]):
                if "=" not in tok:
                    continue
                k, v = tok.split("=", 1)
                if k == "rate":
                    dr = _num(v)
                elif k == "pitch":
                    dp = _num(v)
                elif k in ("vol", "volume"):
                    dv = _num(v)
            cur_name, cur_delta = name, (dr, dp, dv)
    flush()
    return events


def srt_ts(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"
