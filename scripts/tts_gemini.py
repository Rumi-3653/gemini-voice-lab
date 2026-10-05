# -*- coding: utf-8 -*-
"""gemini-voice-lab: Gemini 3.8 Flash TTS 引擎（Google 雲端，需 GEMINI_API_KEY，有免費額度）

比 edge-tts 有演技：風格用自然語言描述（speech_metadata.style），文字裡可放 <sigh> <breath>
<short pause> <long pause> <laugh> 這類行內標籤（字幕會自動剝掉）。支援繁體中文。音色用 Google 預製音色，或 voices.py 建的專屬音色（--voice voice_…）。

用法（一律用 python 呼叫；CJK 文字走 --file / --cues，不要塞 --text）:
  python tts_gemini.py --file script.txt --out vo.wav --voice Sulafat --style "溫柔但清醒的朋友，語速偏慢"
  python tts_gemini.py --cues cues.txt --out vo.mp3 --voice Sulafat --srt vo.srt   # 配音譜，格式見 cues.py 檔頭
  python tts_gemini.py --cues cues.txt --styles styles.txt --out vo.mp3 --one-request  # 風格表覆寫/新增 preset，含「底色:」
  python tts_gemini.py --cues cues.txt --out test.wav --dry-run                      # 不呼叫 API，靜音佔位驗切段/串接/SRT
  python tts_gemini.py --list-voices

配音譜的九款情緒 preset（平穩/低語/沉/懸念/感嘆/緊張/激動/高潮/收尾）在這裡不是語速位移，
而是對應成一段英文風格描述（見 STYLE_PRESETS）；--style 是全篇底色，preset 疊在後面。
preset 後面帶的 rate=/pitch=/vol= 覆寫 Gemini 不支援，會警告並忽略。
【停 X】照舊插 X 秒靜音；相鄰兩段之間預設補 --gap 秒換氣。

API key：環境變數 GEMINI_API_KEY（或 GOOGLE_API_KEY）。沒有就去 https://aistudio.google.com/apikey 建一把，
PowerShell 永久設定：setx GEMINI_API_KEY "你的key"（開新終端才生效）。key 不要寫進任何 repo 檔案。
"""
import argparse
import base64
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from cues import PRESETS, parse_cues, srt_ts  # 配音譜解析與 SRT 時間碼
except ImportError as e:
    sys.exit(f"缺 scripts/cues.py（{e}）")

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

MODELS = {"flash": "gemini-3.8-flash-tts", "lite": "gemini-3.8-flash-lite-tts"}
SR, CH, SW = 24000, 1, 2  # Gemini TTS 單次請求預設輸出：24 kHz, mono, 16-bit PCM WAV
TAG = re.compile(r"<[^<>]{1,30}>")  # 行內演技標籤，進字幕前剝掉

# 配音譜九款 preset → Gemini 風格描述（英文對模型最穩；中文 style 也吃，但實測英文更準）
STYLE_PRESETS = {
    "平穩": "calm, even narration, natural pace, warm and clear",
    "低語": "hushed, intimate, almost whispering, very close to the mic, slow",
    "沉":   "heavy, sorrowful, low and slow, speaking from memory, restrained",
    "懸念": "suspenseful, slower, holding the listener, slight tension, deliberate pauses",
    "感嘆": "wistful and reflective, a soft sigh in the voice, lingering ends of phrases",
    "緊張": "tense and urgent, quickening pace, tight breath, pressure building",
    "激動": "excited and emotional, energetic, louder, voice lifting",
    "高潮": "the emotional peak, intense and bright, strong emphasis, release of tension",
    "收尾": "settling down, gentle and conclusive, slower, soft landing, quiet warmth",
}

# Google 預製音色（30 款）；繁中旁白實務首選前六個
VOICES = [
    ("Sulafat", "Warm", "溫暖，療癒/敘事旁白首選"), ("Achernar", "Soft", "柔軟，晚安/靜心"),
    ("Vindemiatrix", "Gentle", "溫和，知識型旁白"), ("Kore", "Firm", "穩定有力，品牌/預告"),
    ("Charon", "Informative", "資訊感，說明/紀錄片"), ("Gacrux", "Mature", "成熟，長者角色"),
    ("Zephyr", "Bright", "明亮"), ("Puck", "Upbeat", "活潑"), ("Fenrir", "Excitable", "興奮外放"),
    ("Leda", "Youthful", "年輕"), ("Orus", "Firm", "有力"), ("Aoede", "Breezy", "輕快"),
    ("Callirrhoe", "Easy-going", "隨性"), ("Autonoe", "Bright", "明亮"), ("Enceladus", "Breathy", "氣音多"),
    ("Iapetus", "Clear", "清晰"), ("Umbriel", "Easy-going", "隨性"), ("Algieba", "Smooth", "平滑"),
    ("Despina", "Smooth", "平滑"), ("Erinome", "Clear", "清晰"), ("Algenib", "Gravelly", "沙啞"),
    ("Rasalgethi", "Informative", "資訊感"), ("Laomedeia", "Upbeat", "活潑"), ("Alnilam", "Firm", "有力"),
    ("Schedar", "Even", "平穩"), ("Pulcherrima", "Forward", "前傾積極"), ("Achird", "Friendly", "親切"),
    ("Zubenelgenubi", "Casual", "口語"), ("Sadachbia", "Lively", "生動"), ("Sadaltager", "Knowledgeable", "博學"),
]


def load_styles(path: str) -> str:
    """--styles 檔：每行「名稱: 英文風格句」（冒號全半形皆可，# 開頭略過）。
    名稱同既有 preset → 覆寫；新名稱 → 新增（配音譜就能用【A】【B】【C】這類自訂段）；
    「底色」行回傳給呼叫端當全篇 --style（--style 有給時以 --style 為準）。"""
    base = ""
    with open(path, "r", encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^(.+?)\s*[:：]\s*(.+)$", line)
            if not m:
                print(f"警告: --styles 略過看不懂的行「{line[:40]}」", file=sys.stderr)
                continue
            name, style = m.group(1).strip(), m.group(2).strip()
            if name == "底色":
                base = style
            else:
                STYLE_PRESETS[name] = style
    return base


def api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        sys.exit("沒有 GEMINI_API_KEY。到 https://aistudio.google.com/apikey 建一把，然後 "
                 "setx GEMINI_API_KEY \"你的key\"（開新終端生效）；或先用 --dry-run 驗流程。")
    return key


def _handle_api_error(e: Exception, attempt: int):
    """4xx 一律一行報錯；429 分兩種：每日額度用完（訊息含 per day）直接停，其他限流等 35 秒再試最多三次。
    免費層實測：gemini-3.8-flash-tts 每天 10 次請求，隔天太平洋午夜（台灣 15:00）重置；SDK 內建退避撐不過。"""
    code = getattr(e, "status_code", None) or getattr(e, "code", None)
    msg = str(e)
    if code == 429 and "per day" in msg:
        sys.exit("今天的免費額度用完了（每天 10 次請求，台灣時間 15:00 重置）。"
                 "可改 --model lite（額度分開算）、加 --one-request 省次數。\n  " + msg[:200])
    if code == 429 and attempt < 4:
        print(f"  限流（429），等 35 秒再試（{attempt}/3）…", file=sys.stderr)
        time.sleep(35)
        return
    sys.exit(f"Gemini 拒絕請求（{code or type(e).__name__}）: {msg[:300]}")  # 其他 4xx = key/音色/模型名錯


def synth_wav(client, model: str, text: str, voice: str, style: str) -> bytes:
    """一段文字 → 音訊 bytes（通常是 WAV）。SDK 自己會對 429/5xx 退避重試，這裡不疊第二層。"""
    content = {"type": "text", "text": text}
    if style:
        content["annotations"] = [{"type": "speech_metadata", "style": style}]
    for attempt in range(1, 5):
        try:
            it = client.interactions.create(
                model=model,
                input=[{"type": "user_input", "content": [content]}],
                response_format={"type": "audio"},
                generation_config={"speech_config": [{"voice": voice}]},
                timeout=120,
            )
            break
        except Exception as e:
            _handle_api_error(e, attempt)
    au = getattr(it, "output_audio", None)
    data = getattr(au, "data", None)
    if not data:
        raise RuntimeError(f"Gemini 沒回音訊: status={getattr(it, 'status', '?')} "
                           f"errors={getattr(it, 'errors', None)} text={str(getattr(it, 'output_text', ''))[:120]}")
    raw = base64.b64decode(data) if isinstance(data, str) else bytes(data)
    mime = (getattr(au, "mime_type", "") or "").lower()
    if not raw.startswith(b"RIFF") and mime.startswith("audio/l16"):  # 裸 PCM → 自己包 WAV 頭
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(int(getattr(au, "channels", None) or CH))
            w.setsampwidth(SW)
            w.setframerate(int(getattr(au, "sample_rate", None) or SR))
            w.writeframes(raw)
        raw = buf.getvalue()
    return raw


def synth_one_request(client, model: str, beats, voice: str, base_style: str) -> bytes:
    """整支配音譜塞進一次請求：每段是一個 content block 帶自己的 style，段後的留白改成 <short/long pause> 標籤。
    免費層以「請求次數」計額度，12 段譜從 12 次變 1 次。代價：拿不到逐段時間軸（SRT 改用字數比例估算）。
    beats = [(text, style, pause_after_sec)]"""
    blocks = []
    for text, style, pause in beats:
        if pause >= 1.0:
            text = text.rstrip() + " <long pause>"
        elif pause > 0:
            text = text.rstrip() + " <short pause>"
        blk = {"type": "text", "text": text}
        full = ". ".join(s.rstrip(".。 ") for s in (base_style, style) if s)
        if full:
            blk["annotations"] = [{"type": "speech_metadata", "style": full}]
        blocks.append(blk)
    for attempt in range(1, 5):
        try:
            it = client.interactions.create(
                model=model,
                input=[{"type": "user_input", "content": blocks}],
                response_format={"type": "audio"},
                generation_config={"speech_config": [{"voice": voice}]},
                timeout=300,
            )
            break
        except Exception as e:
            _handle_api_error(e, attempt)
    au = getattr(it, "output_audio", None)
    data = getattr(au, "data", None)
    if not data:
        raise RuntimeError(f"Gemini 沒回音訊: status={getattr(it, 'status', '?')} errors={getattr(it, 'errors', None)}")
    raw = base64.b64decode(data) if isinstance(data, str) else bytes(data)
    if not raw.startswith(b"RIFF") and (getattr(au, "mime_type", "") or "").lower().startswith("audio/l16"):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(int(getattr(au, "channels", None) or CH)); w.setsampwidth(SW)
            w.setframerate(int(getattr(au, "sample_rate", None) or SR)); w.writeframes(raw)
        raw = buf.getvalue()
    return raw


def to_pcm(wav_bytes: bytes) -> bytes:
    """把回傳音訊轉成 24k/mono/16-bit 的裸 PCM frames；規格不同或非 WAV 時用 ffmpeg 重採樣。"""
    if not wav_bytes:
        raise RuntimeError("回傳音訊為空")
    try:
        with wave.open(io.BytesIO(wav_bytes)) as w:
            if (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (SR, CH, SW):
                frames = w.readframes(w.getnframes())
                if frames:
                    return frames
    except (wave.Error, EOFError):
        pass  # 不是標準 RIFF → 交給 ffmpeg 自動偵測容器
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "s16le", "-ac", str(CH),
                        "-ar", str(SR), "pipe:1"], input=wav_bytes, capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("ffmpeg 轉 PCM 失敗: " + r.stderr.decode("utf-8", "replace")[-400:])
    return r.stdout


def silence(sec: float) -> bytes:
    return b"\x00" * (int(round(max(0.0, sec) * SR)) * CH * SW)


def placeholder_pcm(text: str) -> bytes:
    """--dry-run 用：以中文旁白約 4.5 字/秒估長度，低音量噪聲讓波形看得出段落（1 秒雜訊塊平鋪，常數記憶體）。"""
    import random
    n = int(round(max(0.8, len(text) / 4.5) * SR))
    rnd = random.Random(len(text))
    block = b"".join(int(rnd.uniform(-300, 300)).to_bytes(2, "little", signed=True) for _ in range(SR))
    return (block * (n // SR + 1))[: n * CH * SW]


def write_wav(path: str, pcm: bytes):
    with wave.open(path, "wb") as w:
        w.setnchannels(CH)
        w.setsampwidth(SW)
        w.setframerate(SR)
        w.writeframes(pcm)


def encode_out(wav_path: str, out: str):
    """輸出不是 .wav 時用 ffmpeg 轉檔（mp3 -q:a 2 / m4a aac 192k）。"""
    if out.lower().endswith(".wav"):
        if os.path.abspath(wav_path) != os.path.abspath(out):
            shutil.copyfile(wav_path, out)
        return
    codec = ["-c:a", "aac", "-b:a", "192k"] if out.lower().endswith((".m4a", ".mp4")) else ["-q:a", "2"]
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", wav_path, *codec, out], capture_output=True)
    if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        sys.exit("ffmpeg 轉檔失敗: " + r.stderr.decode("utf-8", "replace")[-800:])


def main():
    ap = argparse.ArgumentParser(description="Gemini 3.8 Flash TTS 配音產生器")
    ap.add_argument("--text", help="短句（長文/CJK 用 --file）")
    ap.add_argument("--file", help="UTF-8 文字檔（整篇同一風格）")
    ap.add_argument("--cues", help="配音譜 UTF-8 txt（格式見 cues.py 檔頭，preset 對應成 style）")
    ap.add_argument("--out", default="vo.wav", help="輸出 .wav（原生）或 .mp3/.m4a（ffmpeg 轉檔）")
    ap.add_argument("--voice", default="Sulafat", help="Google 預製音色名（--list-voices 查）或 voices.py 建的 voice_… ID")
    ap.add_argument("--style", default="", help="全篇風格底色，自然語言（中英皆可，英文較準）")
    ap.add_argument("--styles", help="風格表 UTF-8 txt：每行「名稱: 英文風格句」，覆寫/新增配音譜 preset；「底色:」行＝全篇 --style")
    ap.add_argument("--model", default="flash", choices=list(MODELS), help="flash=最佳演技；lite=便宜大量")
    ap.add_argument("--gap", type=float, default=0.35, help="相鄰情緒段間預設換氣秒數（有【停 X】就不加）")
    ap.add_argument("--sleep", type=float, default=0.0, help="每次 API 呼叫間隔秒數（免費額度被限流時設 2~5）")
    ap.add_argument("--srt", help="輸出 beat 級 SRT 字幕")
    ap.add_argument("--one-request", action="store_true",
                    help="整支譜塞一次請求（免費層一天只有 10 次請求時必開）；留白變 <pause> 標籤，SRT 用字數比例估算")
    ap.add_argument("--dry-run", action="store_true", help="不呼叫 API，用靜音佔位驗證切段/串接/SRT")
    ap.add_argument("--list-voices", action="store_true")
    args = ap.parse_args()

    if args.styles:
        base = load_styles(args.styles)
        if base and not args.style.strip():
            args.style = base

    if args.list_voices:
        for name, trait, note in VOICES:
            print(f"{name:14s} {trait:14s} {note}")
        return

    # 事件序列：say(name, style_suffix, text) / pause(sec)
    if args.cues:
        events = []
        for ev in parse_cues(args.cues):
            if ev[0] == "pause":
                events.append(("pause", ev[1]))
                continue
            _, name, delta, text = ev
            if delta != PRESETS.get(name, (0, 0, 0)):
                print(f"警告: 【{name}】帶的 rate/pitch/vol 覆寫 Gemini 不支援，已忽略（改用 --style 或換 preset）",
                      file=sys.stderr)
            events.append(("say", name, STYLE_PRESETS.get(name, STYLE_PRESETS["平穩"]), text))
    else:
        if args.file:
            with open(args.file, "r", encoding="utf-8-sig") as f:
                text = f.read().strip()
        elif args.text:
            text = args.text.strip()
        else:
            ap.error("需要 --text、--file 或 --cues")
        if not text:
            sys.exit("文字內容是空的")
        events = [("say", "全篇", "", text)]
    if not any(e[0] == "say" for e in events):
        sys.exit("沒有可唸的文字")

    for p in (args.out, args.srt):  # 輸出目錄不存在先建，別等 API 都叫完才炸
        if p:
            os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)

    seq = []
    for ev in events:  # 相鄰 say 之間補換氣
        if ev[0] == "say" and seq and seq[-1][0] == "say" and args.gap > 0:
            seq.append(("pause", args.gap))
        seq.append(ev)

    client = None
    if not args.dry_run:
        try:
            from google import genai
        except ImportError:
            sys.exit("缺 google-genai：pip install google-genai")
        client = genai.Client(api_key=api_key())
    model = MODELS[args.model]

    pcm_parts, subs, table, t = [], [], [], 0.0
    if args.one_request and not args.dry_run:
        # 整支譜一次請求（免費層以請求次數計額度）。段後留白變成 <pause> 標籤，時間軸用字數比例估算。
        beats, cur = [], None
        for ev in seq:
            if ev[0] == "say":
                if cur:
                    beats.append(cur)
                cur = [ev[1], ev[3], ev[2], 0.0]  # name, text, preset_style, pause_after
            elif cur:
                cur[3] += max(0.0, ev[1])
        if cur:
            beats.append(cur)
        pcm = to_pcm(synth_one_request(client, model, [(b[1], b[2], b[3]) for b in beats], args.voice, args.style.strip()))
        if not pcm:
            sys.exit("回傳空音訊")
        total = len(pcm) / (SR * CH * SW)
        weights = [max(1, len(TAG.sub("", b[1]))) + (3 if b[3] >= 1.0 else 1 if b[3] > 0 else 0) for b in beats]
        wsum = sum(weights)
        for b, w in zip(beats, weights):
            dur = total * w / wsum
            shown = TAG.sub("", b[1]).replace("\n", " ").strip()
            if shown:
                subs.append((t, t + dur, shown))
            table.append((b[0], dur, (b[2] or "")[:28], shown[:18]))
            t += dur
        pcm_parts = [pcm]
        srt_note = "（SRT 為字數比例估算）"
    else:
        srt_note = ""
        for ev in seq:
            if ev[0] == "pause":
                sec = max(0.0, ev[1])
                pcm_parts.append(silence(sec))
                t += sec
                continue
            _, name, preset_style, text = ev
            style = ". ".join(s.rstrip(".。 ") for s in (args.style.strip(), preset_style) if s)
            if args.dry_run:
                pcm = placeholder_pcm(text)
            else:
                pcm = to_pcm(synth_wav(client, model, text, args.voice, style))
                if args.sleep:
                    time.sleep(args.sleep)
            if not pcm:
                sys.exit(f"第 {len(table) + 1} 段回傳空音訊")
            dur = len(pcm) / (SR * CH * SW)
            pcm_parts.append(pcm)
            shown = TAG.sub("", text).replace("\n", " ").strip()  # 字幕與表格不要出現 <sigh> 這類標籤
            if shown:
                subs.append((t, t + dur, shown))
            table.append((name, dur, style[:28], shown[:18]))
            t += dur

    tmp = tempfile.mkdtemp(prefix="vo_gemini_")
    try:
        wav_path = os.path.join(tmp, "vo.wav")
        write_wav(wav_path, b"".join(pcm_parts))
        encode_out(wav_path, args.out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if args.srt:
        with open(args.srt, "w", encoding="utf-8") as f:
            for k, (a, b, text) in enumerate(subs, 1):
                f.write(f"{k}\n{srt_ts(a)} --> {srt_ts(b)}\n{text}\n\n")

    print(f"{'beat':6s} {'秒':>5s}  {'style':28s}  文字")
    for name, dur, style, head in table:
        print(f"{name:6s} {dur:5.1f}  {style:28s}  {head}")
    print(f"OK {args.out} ({os.path.getsize(args.out) / 1024:.1f} KB, {t:.1f}s, {len(table)} beats, "
          f"{'DRY-RUN 靜音佔位' if args.dry_run else model + ' / ' + args.voice}){srt_note}")


if __name__ == "__main__":
    main()
