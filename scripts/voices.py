# -*- coding: utf-8 -*-
"""gemini-voice-lab: Gemini Voices API 驅動——自建音色的建／列／查／刪／探針。

日常只用 list/get/delete（造聲預設走 AI Studio 網頁版；有 Google AI Pro 的話網頁版配額較高）；
design/replicate 是網頁版不可用時的退路；probe 只在要走 API 造聲前跑一次。
合成不在這裡——用同目錄的 tts_gemini.py --voice <voice_id>。

  python voices.py --selfcheck
  python voices.py list [--all] [--json]
  python voices.py get voice_xxx
  python voices.py delete voice_xxx --yes
  python voices.py design --name my-char --gender female --lang cmn-TW --desc-file desc.txt --out sample.wav
  python voices.py replicate --source me.m4a --consent consent.m4a --name my-voice --lang cmn-TW --out private\\gemini\\sample.wav
  python voices.py probe [--langs cmn-TW,zh-TW,zh,cmn-CN,en-US] [--keep] [--out DIR]

鐵則：免費層設計（429 per day 直接停，不重試）；ID／音檔不進 git（private\\ 與 lab\\ 已被 .gitignore 擋）；複製只做使用者本人；CJK 走檔案不走 argv。
"""
import argparse
import base64
import io
import json
import os
import re
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from tts_gemini import api_key, _handle_api_error, to_pcm, write_wav, SR, CH, SW, MODELS
except ImportError as e:
    sys.exit(f"找不到同目錄的 tts_gemini.py（{e}）")

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SLUG = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
LANG_CANDIDATES = ["cmn-TW", "zh-TW", "zh", "cmn-CN", "en-US"]
CONSENT_TEXT = "我是此声音的拥有者并授权谷歌使用此声音创建语音合成模型"  # 官方僅簡中文本；國語照唸即可
PROBE_DESC = "Adult woman, Taiwanese Mandarin accent, warm conversational tone, natural pace."
SRC_MIN, SRC_MAX, CONSENT_MIN = 10.0, 30.0, 3.0  # 官方：參考音 10~30 秒；授權句至少唸完一句
MODEL = MODELS["flash"]


# ---------- 金鑰與錯誤 ----------
def _key() -> str:
    """Claude 工具環境常讀不到新設的使用者層變數：env 沒有就從登錄檔 HKCU\\Environment 補進 env，再交給 tts_gemini.api_key()。絕不印值。"""
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")) and sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                val, _ = winreg.QueryValueEx(k, "GEMINI_API_KEY")
            if val:
                os.environ["GEMINI_API_KEY"] = val
        except OSError:
            pass
    return api_key()


def _err(e: Exception, attempt: int):
    """先於 tts_gemini._handle_api_error 的分流（所有分支都 exit，voices.py 不重試）：
    429 含 per day／PerDay = 某個每日額度用完（metric/model 指向 flash TTS 才算「create 與 TTS 共用」，才記 api-facts）；
    429 不含 = Voices 自己的配額／stored voice 上限；403 或非 429 的 billing = 免費層不開放（probe 模式回 exit 2）。
    注意：Google 所有配額 429 的原文都含 "please check your plan and billing details"，所以 billing 只在非 429 時當作「不開放」。"""
    code = getattr(e, "status_code", None) or getattr(e, "code", None)
    msg = str(e)
    if code == 429:
        if re.search(r"per[ _]?day", msg, re.I):
            sys.exit("429 每日額度（quotaId 含 PerDay）。記 api-facts 前先看下方 metric/model 是否指向 gemini-3.8-flash-tts"
                     "——是才算『create 與 TTS 共用』。台灣 15:00 重置，造聲改走 AI Studio 網頁版。\n  " + msg[:600])
        sys.exit("Voices 配額擠爆（RESOURCE_EXHAUSTED，非每日 TTS 額度；多半是 stored voice 上限或 create 配額）: " + msg[:300])
    if code == 403 or (code != 429 and "billing" in msg.lower()):
        if os.environ.get("GVL_PROBE"):
            print(f"免費層不開放此操作（{code}）: {msg[:300]}", file=sys.stderr)
            sys.exit(2)
        sys.exit(f"免費層不開放此操作（{code}）: {msg[:300]}")
    _handle_api_error(e, attempt)  # 其他 4xx → tts_gemini 一行退出（attempt 固定 1，不會進它的 429 重試分支）


def _client(api_key=None, **http_options):
    try:
        from google import genai
    except ImportError:
        sys.exit("缺 google-genai：pip install google-genai")
    c = genai.Client(api_key=api_key or _key(), http_options=http_options or None)
    # SDK 2.27 的 voices 預設對 408/409/429/5xx／連線錯誤重試 3 次；retry_options attempts=0 仍會留 1 次重試
    # （parent 把 0 夾成 1，_gaos 又把它當「重試次數」）→ 直接清掉 voices 的 retry_config。重試 create 可能多建音色而 id 沒印出來。
    c.voices.sdk_configuration.retry_config = None
    return c


def _call(fn, **kw):
    try:
        return fn(**kw)
    except Exception as e:  # noqa: BLE001 — 分流在 _err，所有分支都 exit；不重試（SDK 重試已在 _client 關掉；每次 create 都可能花額度）
        _err(e, 1)


# ---------- 音訊 ----------
def pcm_seconds(pcm: bytes) -> float:
    return len(pcm) / (SR * CH * SW)


def file_to_wav24k(path: str):
    """任何 ffmpeg 讀得懂的檔（m4a/48k/立體聲…）→ (24 kHz mono 16-bit WAV bytes, 秒數)。
    直接給 ffmpeg 檔案路徑而不是 pipe：m4a 的 moov 在檔尾時 pipe 讀不到（手機錄音常如此）。"""
    import subprocess
    if not os.path.isfile(path):
        sys.exit(f"找不到音檔：{path}")
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-ac", str(CH), "-ar", str(SR), "pipe:1"],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        sys.exit(f"ffmpeg 讀 {os.path.basename(path)} 失敗: " + r.stderr.decode("utf-8", "replace")[-300:])
    pcm = r.stdout
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(CH)
        w.setsampwidth(SW)
        w.setframerate(SR)
        w.writeframes(pcm)
    return buf.getvalue(), pcm_seconds(pcm)


def gate_durations(src_sec: float, consent_sec: float):
    """上傳前的 0-API 閘：source 10~30 秒、consent ≥3 秒；不合格印實測秒數退出。"""
    if not (SRC_MIN <= src_sec <= SRC_MAX):
        sys.exit(f"參考音 {src_sec:.1f} 秒，官方要求 {SRC_MIN:.0f}~{SRC_MAX:.0f} 秒；請剪短或重錄")
    if consent_sec < CONSENT_MIN:
        sys.exit(f"授權句只有 {consent_sec:.1f} 秒（<{CONSENT_MIN:.0f} 秒），請完整唸完：{CONSENT_TEXT}")


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


# ---------- 請求 body ----------
def design_body(name: str, gender: str, lang: str, desc: str) -> dict:
    return {"model": MODEL, "type": "prompted", "display_name": name, "gender": gender,
            "language_code": lang, "prompted": {"input": desc}}


def replicate_body(name: str, lang: str, src_wav: bytes, consent_wav: bytes) -> dict:
    return {"model": MODEL, "type": "replicated", "display_name": name, "language_code": lang,
            "replicated": {"source_audio": {"mime_type": "audio/wav", "data": _b64(src_wav)},
                           "consent_audio": {"mime_type": "audio/wav", "data": _b64(consent_wav)}}}


# ---------- 回傳物件 ----------
def fact(v, sample_path=None) -> dict:
    """VoiceOutput → 可抄的事實（欄位名以 SDK 2.27 實測為準：無 create_time）。"""
    exp = getattr(v, "expire_time", None)
    return {"id": getattr(v, "id", None), "type": getattr(v, "type", None),
            "display_name": getattr(v, "display_name", None), "language_code": getattr(v, "language_code", None),
            "expire_time": exp.isoformat() if hasattr(exp, "isoformat") else (str(exp) if exp else None),
            "key": getattr(v, "key", None), "sample": sample_path}


def save_sample(v, out: str):
    """sample_audio（AudioData.data，b64 或 bytes）→ 24k mono WAV 檔；沒有 sample 回 None。"""
    sa = getattr(v, "sample_audio", None)
    data = getattr(sa, "data", None) if sa is not None else None
    if not data:
        return None
    try:
        raw = base64.b64decode(data) if isinstance(data, str) else bytes(data)
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        write_wav(out, to_pcm(raw))
    except Exception as e:  # noqa: BLE001 — 音色已建好、額度已花，sample 轉檔失敗不能吞掉 id
        print(f"警告: sample 轉檔失敗（{str(e)[:120]}；mime={getattr(sa, 'mime_type', None)}），音色已建立，id 見下行 JSON", file=sys.stderr)
        return None
    return out


def _read_desc(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig") as f:
        desc = " ".join(f.read().split())
    if not desc:
        sys.exit("描述檔是空的")
    sents = len(re.findall(r"[.!?。！？]", desc)) or 1
    if sents > 2 or len(desc.split()) > 60:
        print(f"警告: 描述 {sents} 句／{len(desc.split())} 字，官方建議 1~2 句；越長越不穩", file=sys.stderr)
    return desc


def _slug(name: str) -> str:
    if not SLUG.match(name or ""):
        sys.exit("--name 只收 ASCII slug（[A-Za-z0-9_-]{1,40}）；中文名字住角色卡")
    return name


def _print_fact(v, sample=None):
    print(json.dumps(fact(v, sample), ensure_ascii=False))


# ---------- 子命令 ----------
def cmd_list(a):
    c = _client()
    rows, token = [], None
    while True:
        kw = {"page_size": 1000 if a.all else 200}
        if not a.all:
            kw["type_"] = ["prompted", "replicated"]
        if token:
            kw["page_token"] = token
        res = _call(c.voices.list, **kw)
        rows.extend(list(getattr(res, "voices", None) or []))
        token = getattr(res, "next_page_token", None)
        if not token:
            break
    if a.json:
        print(json.dumps([fact(v) for v in rows], ensure_ascii=False, indent=1))
        return
    if not rows:
        print("（沒有自建音色）" if not a.all else "（空）")
        return
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    for v in rows:
        f = fact(v)
        exp = getattr(v, "expire_time", None)
        warn = ""
        if exp is not None and hasattr(exp, "tzinfo"):
            e = exp if exp.tzinfo else exp.replace(tzinfo=_dt.timezone.utc)
            if (e - now).days < 30:
                warn = "⚠️ "
        print(f"{warn}{f['id']} | {f['type']} | {f['display_name']} | {f['language_code']} | {f['expire_time']}")


def cmd_get(a):
    v = _call(_client().voices.get, id=a.id)
    if hasattr(v, "model_dump_json"):
        print(v.model_dump_json(indent=1, exclude_none=True, exclude={"sample_audio"}))  # sample 是數百 KB base64，別灌進終端
    else:
        print(repr(v))


def cmd_delete(a):
    if not a.yes:
        print(f"（預覽）會刪除 {a.id}；確定請加 --yes。刪前先 grep 你的角色卡與私人 ID 檔確認沒人引用")
        return
    _call(_client().voices.delete, id=a.id)
    print(json.dumps({"deleted": a.id}, ensure_ascii=False))


def cmd_design(a):
    name, desc = _slug(a.name), _read_desc(a.desc_file)
    v = _call(_client().voices.create, store=True, voice=design_body(name, a.gender, a.lang, desc))
    _print_fact(v, save_sample(v, a.out))


def cmd_replicate(a):
    name = _slug(a.name)
    if os.path.abspath(a.source) == os.path.abspath(a.consent):
        sys.exit("--source 與 --consent 不能是同一個檔")
    src_wav, src_sec = file_to_wav24k(a.source)
    con_wav, con_sec = file_to_wav24k(a.consent)
    gate_durations(src_sec, con_sec)
    print(f"參考音 {src_sec:.1f}s／授權句 {con_sec:.1f}s。授權句原文（請確認錄的是這句）：{CONSENT_TEXT}", file=sys.stderr)
    v = _call(_client().voices.create, store=not a.no_store, voice=replicate_body(name, a.lang, src_wav, con_wav))
    _print_fact(v, save_sample(v, a.out))


def cmd_probe(a):
    """逐個 language_code 試建一個 prompted 音色→存 sample→（非 --keep）刪掉。exit 0=create 可用；exit 2=免費層不開放／全被拒。"""
    os.environ["GVL_PROBE"] = "1"
    c = _client()
    langs = [s.strip() for s in a.langs.split(",") if s.strip()]
    os.makedirs(a.out, exist_ok=True)
    accepted, rejected = [], []
    for lang in langs:
        try:
            v = c.voices.create(store=True, voice=design_body("gvl-probe", "female", lang, PROBE_DESC))
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "status_code", None) or getattr(e, "code", None)
            msg = str(e)
            if code == 400 and "language" in msg.lower():
                rejected.append((lang, msg[:200]))
                continue
            _err(e, 4)  # 其他錯誤即停（403→exit 2）
        sample = save_sample(v, os.path.join(a.out, f"probe_{lang}.wav"))
        f = fact(v, sample)
        accepted.append(f)
        print(json.dumps(f, ensure_ascii=False), file=sys.stderr)
        if not a.keep:
            _call(c.voices.delete, id=f["id"])
            left = [x.id for x in (getattr(_call(c.voices.list, type_=["prompted"]), "voices", None) or [])]
            if f["id"] in left:
                print(f"警告: {f['id']} 刪除後仍在清單", file=sys.stderr)
        break  # 第一個被接受的語言碼就夠了
    print("事實：")
    if accepted:
        print("  voices.create 免費層可用：是")
    elif rejected:
        print("  voices.create 免費層可用：否（全部語言碼被拒，見上）")
    else:
        print("  voices.create 免費層可用：否")
    for f in accepted:
        print(f"  接受的 language_code：{f['language_code']}　id：{f['id']}　sample：{f['sample']}　expire_time：{f['expire_time']}")
    for lang, m in rejected:
        print(f"  被拒 {lang}：{m}")
    if not accepted:
        sys.exit(2)


# ---------- 自檢 ----------
def selfcheck():
    # 1) to_pcm：1 秒 48k 立體聲正弦 → 24k mono 16-bit，剛好 24000 frames
    import math
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(48000)
        frames = b"".join(int(8000 * math.sin(2 * math.pi * 440 * i / 48000)).to_bytes(2, "little", signed=True) * 2
                          for i in range(48000))
        w.writeframes(frames)
    pcm = to_pcm(buf.getvalue())
    assert abs(len(pcm) - SR * CH * SW) <= SR * CH * SW // 100, f"to_pcm 長度 {len(pcm)} 偏離 {SR * CH * SW} 超過 1%"
    assert abs(pcm_seconds(pcm) - 1.0) <= 0.01, f"pcm_seconds {pcm_seconds(pcm):.4f} 偏離 1.0 秒超過 1%"
    # 2) 時長閘：5 秒參考音要被拒，15 秒要過
    try:
        gate_durations(5.0, 3.0); raise AssertionError("5 秒參考音不該通過")
    except SystemExit as e:
        assert "5.0" in str(e)
    gate_durations(15.0, 3.0)
    # 3) body 鍵路徑與 b64 round-trip
    d = design_body("x", "female", "cmn-TW", "desc")
    assert d["type"] == "prompted" and d["prompted"]["input"] == "desc" and d["model"] == MODEL
    r = replicate_body("x", "cmn-TW", b"\x01\x02", b"\x03")
    assert base64.b64decode(r["replicated"]["source_audio"]["data"]) == b"\x01\x02"
    assert base64.b64decode(r["replicated"]["consent_audio"]["data"]) == b"\x03"
    assert r["replicated"]["source_audio"]["mime_type"] == "audio/wav"
    # 4) probe 候選順序
    assert LANG_CANDIDATES[0] == "cmn-TW" and LANG_CANDIDATES[-1] == "en-US"
    # 5) _err 三條路：429 無 per day → Voices 配額；429 含 billing＋PerDay（Google 真實措辭）→ 每日額度，不是「不開放」；403 → 不開放
    class E(Exception):
        status_code = 429
    try:
        _err(E("RESOURCE_EXHAUSTED: voices quota"), 1); raise AssertionError("429 無 per day 應直接退出")
    except SystemExit as e:
        assert "Voices 配額" in str(e)
    try:
        _err(E("Error code: 429 - please check your plan and billing details. quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"), 1)
        raise AssertionError("429 PerDay 應退出")
    except SystemExit as e:
        assert "每日額度" in str(e) and e.code != 2, f"429 PerDay 被誤判: {e}"
    class F(Exception):
        status_code = 403
    try:
        _err(F("PERMISSION_DENIED"), 1); raise AssertionError("403 應退出")
    except SystemExit as e:
        assert "不開放" in str(e)
    # 6) slug
    assert SLUG.match("my-char") and not SLUG.match("角色") and not SLUG.match("a" * 41)
    # 7) SDK 不重試：假 transport 回 503，voices.create 只能打 1 次（離線，不碰網路）
    try:
        import httpx
        from google import genai  # noqa: F401
    except ImportError:
        print("跳過第 7 項（缺 google-genai／httpx）", file=sys.stderr)
    else:
        hits = []

        def _h(req):
            hits.append(req)
            return httpx.Response(503, json={"error": {"code": 503, "message": "x", "status": "UNAVAILABLE"}})
        c = _client("x", httpx_client=httpx.Client(transport=httpx.MockTransport(_h)))
        try:
            c.voices.create(store=True, voice=design_body("x", "female", "cmn-TW", "d"))
        except Exception:  # noqa: BLE001 — 預期 5xx 例外
            pass
        assert len(hits) == 1, f"voices.create 被打 {len(hits)} 次（SDK 重試沒關；0 代表假 transport 沒生效）"
    print("selfcheck OK")


def main():
    ap = argparse.ArgumentParser(description="gemini-voice-lab: Gemini Voices API 驅動")
    ap.add_argument("--selfcheck", action="store_true", help="離線自檢（不需 key、不打 API）")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("list"); p.add_argument("--all", action="store_true"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("get"); p.add_argument("id")
    p = sub.add_parser("delete"); p.add_argument("id"); p.add_argument("--yes", action="store_true")
    p = sub.add_parser("design")
    p.add_argument("--name", required=True); p.add_argument("--gender", required=True, choices=["female", "male", "neutral"])
    p.add_argument("--lang", required=True); p.add_argument("--desc-file", required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("replicate")
    p.add_argument("--source", required=True); p.add_argument("--consent", required=True)
    p.add_argument("--name", required=True); p.add_argument("--lang", required=True); p.add_argument("--out", required=True)
    p.add_argument("--no-store", action="store_true", help="回 voicekey_（7 天）而不是存進專案")
    p = sub.add_parser("probe")
    p.add_argument("--langs", default=",".join(LANG_CANDIDATES)); p.add_argument("--keep", action="store_true")
    p.add_argument("--out", default=os.path.join(os.environ.get("TEMP", "."), "gvl_probe"))
    a = ap.parse_args()
    if a.selfcheck:
        selfcheck(); return
    if not a.cmd:
        ap.error("要一個子命令（list/get/delete/design/replicate/probe）或 --selfcheck")
    {"list": cmd_list, "get": cmd_get, "delete": cmd_delete, "design": cmd_design,
     "replicate": cmd_replicate, "probe": cmd_probe}[a.cmd](a)


if __name__ == "__main__":
    main()
