# -*- coding: utf-8 -*-
"""gemini-voice-lab: 把一支旁白裡「句與句之間的空白」各拉長固定秒數（後製，0 API 請求）。

為什麼：Gemini 行內 <short pause> 標籤對停頓長度沒有可量測的影響（2026-10-05 lite 實測），
而每次重新合成都要花額度；「斷句再拖一點點」這種 cue 層需求用後製最準、最省。

  python stretch_pauses.py --in r05.mp3 --out r06.mp3 --add 0.35 --min 0.5
  python stretch_pauses.py --selfcheck

--min  只處理長度 ≥ 這麼多秒的空白（句間停頓通常 ≥0.5 s；字間小停頓 <0.3 s 不動）
--add  每個空白插入多少秒的靜音（在空白中點插）
--thresh  判定「空白」的 RMS 門檻（dBFS，預設 -38）
輸入任何 ffmpeg 讀得懂的檔；輸出 .wav 原生，.mp3/.m4a 走 ffmpeg 轉檔。
"""
import argparse
import io
import os
import subprocess
import sys
import tempfile
import wave

import numpy as np

SR, CH, SW = 24000, 1, 2
WIN = 0.01  # 10 ms 分析窗


def decode(path: str) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-ac", str(CH), "-ar", str(SR), "pipe:1"],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        sys.exit("ffmpeg 解碼失敗: " + r.stderr.decode("utf-8", "replace")[-300:])
    return np.frombuffer(r.stdout, dtype=np.int16)


def silent_runs(pcm: np.ndarray, thresh_db: float, min_sec: float):
    """回傳 [(start_sample, end_sample)]：連續 RMS 低於門檻、且長度 ≥ min_sec 的區段。"""
    w = int(SR * WIN)
    n = len(pcm) // w
    x = pcm[: n * w].astype(np.float32).reshape(n, w) / 32768.0
    rms_db = 20 * np.log10(np.sqrt((x ** 2).mean(axis=1)) + 1e-9)
    quiet = rms_db < thresh_db
    runs, start = [], None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) * WIN >= min_sec:
                runs.append((start * w, i * w))
            start = None
    if start is not None and (n - start) * WIN >= min_sec:
        runs.append((start * w, n * w))
    return runs


def stretch(pcm: np.ndarray, runs, add_sec: float) -> np.ndarray:
    pad = np.zeros(int(SR * add_sec), dtype=np.int16)
    out, pos = [], 0
    for s, e in runs:
        mid = (s + e) // 2
        out.append(pcm[pos:mid]); out.append(pad); pos = mid
    out.append(pcm[pos:])
    return np.concatenate(out)


def write_out(pcm: np.ndarray, out: str):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(CH); w.setsampwidth(SW); w.setframerate(SR); w.writeframes(pcm.tobytes())
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    if out.lower().endswith(".wav"):
        open(out, "wb").write(buf.getvalue()); return
    tmp = tempfile.mkdtemp(prefix="stretch_")
    wav = os.path.join(tmp, "x.wav"); open(wav, "wb").write(buf.getvalue())
    codec = ["-c:a", "aac", "-b:a", "192k"] if out.lower().endswith((".m4a", ".mp4")) else ["-q:a", "2"]
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", wav, *codec, out], capture_output=True)
    if r.returncode != 0:
        sys.exit("ffmpeg 轉檔失敗: " + r.stderr.decode("utf-8", "replace")[-300:])


def selfcheck():
    t = np.arange(int(SR * 1.0)) / SR
    tone = (8000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    gap = np.zeros(int(SR * 0.6), dtype=np.int16)
    tiny = np.zeros(int(SR * 0.2), dtype=np.int16)
    pcm = np.concatenate([tone, gap, tone, tiny, tone])            # 兩個音、一個 0.6 s 空白、一個 0.2 s 小空白
    runs = silent_runs(pcm, -38.0, 0.5)
    assert len(runs) == 1, f"應只抓到 1 個 ≥0.5 s 的空白，抓到 {len(runs)}"
    s, e = runs[0]
    assert abs((e - s) / SR - 0.6) < 0.05, f"空白長度 {(e - s) / SR:.2f} ≠ 0.6"
    out = stretch(pcm, runs, 0.3)
    assert abs(len(out) / SR - (len(pcm) / SR + 0.3)) < 1e-6, "拉長後總長應剛好 +0.3 s"
    assert np.array_equal(out[:s], pcm[:s]) and np.array_equal(out[-len(tone):], pcm[-len(tone):]), "空白以外的樣本不可被動到"
    print("selfcheck OK")


def main():
    ap = argparse.ArgumentParser(description="拉長句間空白（後製）")
    ap.add_argument("--in", dest="src")
    ap.add_argument("--out")
    ap.add_argument("--add", type=float, default=0.35, help="每個空白插入的靜音秒數")
    ap.add_argument("--min", type=float, default=0.5, help="只處理 ≥ 此秒數的空白")
    ap.add_argument("--thresh", type=float, default=-38.0, help="空白門檻 dBFS")
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()
    if a.selfcheck:
        selfcheck(); return
    if not (a.src and a.out):
        ap.error("要 --in 與 --out")
    pcm = decode(a.src)
    runs = silent_runs(pcm, a.thresh, a.min)
    out = stretch(pcm, runs, a.add)
    write_out(out, a.out)
    print(f"OK {a.out}  空白 {len(runs)} 處各 +{a.add:.2f}s  {len(pcm) / SR:.1f}s → {len(out) / SR:.1f}s")


if __name__ == "__main__":
    main()
