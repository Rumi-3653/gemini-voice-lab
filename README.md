# gemini-voice-lab — 用回合制把 Gemini 配音調成自然的台灣口語

> 給 AI 配音創作者的「聲音實驗室」：每回合只花 1 次免費請求，聽完說一句評語，評語歸到三支把手之一，規則寫回規則庫，下次直接套。
>
> English summary below. ↓

## 這是什麼

Gemini TTS 只有三支把手：**voice**（音色人設／口音，建一次）、**style**（每段一句英文風格描述）、**text**（逐字照唸——用字、語尾、替換字、`<sigh>` 這類行內標籤都算）。「像大陸腔」「太平」「唸錯字」這種評語，其實各自對應其中一支。

這個 skill 把調聲變成回合制：每回合只改一件事、只燒 1 發 flash 請求；使用者用耳朵裁定好壞；Claude 負責分類、查規則庫、記錄，並維護專屬音色（AI Studio 網頁版造聲，或 `voices.py` 列／查／刪／到期重建）。**自帶引擎**（`scripts/tts_gemini.py`），不依賴其他 skill。

## 需要什麼

- Python 3.10+
- `pip install google-genai`
- ffmpeg（在 PATH 上；輸出 mp3／m4a 與重採樣會用到）
- `GEMINI_API_KEY` 環境變數（到 <https://aistudio.google.com/apikey> 建一把；Windows：`setx GEMINI_API_KEY "你的key"` 後開新終端。金鑰不要寫進任何檔案）

## 30 秒上手

當 Claude Code skill 用（也適用其他讀 `SKILL.md` 的 agent）：把整個資料夾放到 `~/.claude/skills/gemini-voice-lab`（資料夾名必須等於 `SKILL.md` 的 `name`），然後說「訓練 Gemini 配音」。

不靠 agent、純手動試跑（在本 skill 資料夾（repo 根）執行，Windows 寫法；macOS／Linux 把 `\` 換成 `/`、`python` 改 `python3`）：

```powershell
# 1. 離線自檢（不需 key、不打 API）
python scripts\voices.py --selfcheck

# 2. 驗證範例角色的配音譜切段與風格表（--dry-run：0 次請求，輸出靜音佔位）
python scripts\tts_gemini.py --cues examples\範例角色\cues.txt --styles examples\範例角色\styles.txt --dry-run --out lab\_smoke\dry.wav

# 3. r01 基線：整支譜塞進 1 次 flash 請求
python scripts\tts_gemini.py --cues examples\範例角色\cues.txt --styles examples\範例角色\styles.txt --voice Sulafat --one-request --model flash --out lab\範例角色\r01.mp3
```

聽完 r01，回一句評語（例如「尾音太拖」），照 `SKILL.md` 的「評語→把手對照表」處理下一輪。中文內容一律放 UTF-8 檔（`--cues`／`--styles`／`--desc-file`），不要塞進命令列參數。

## 兩種造聲

預設走 **AI Studio 網頁版**（<https://aistudio.google.com/generate-speech>）；若你有 Google AI Pro，網頁版配額較高，可當造聲入口與備援。API 版（`voices.py design`／`replicate`）是退路。

- **Voice design（角色聲）**：寫 1~2 句英文描述（含 `Taiwanese Mandarin accent`、`conversational`）→ 網頁版試聽、存成 `voice_…` → `python scripts\voices.py list` 確認 API 看得到。
- **Voice replication（只限你自己的聲音）**：先過同意閘（念出授權句、確認是本人並同意上傳 Google）；手機安靜處錄兩段（授權句 ≥3 秒、自然說話 15~20 秒）→ 網頁版上傳，或 `voices.py replicate`。本人 replicated 的 ID 只存 repo 外或 `private\` 內的私人檔，不進角色卡、不進 git。複製他人聲音一律不做。

## 規則庫怎麼長

`learnings/rules.md` 起始是空的（只有「目前底色」與一張替換表）。每回合收到評語就當場回寫一條：症狀（逐字抄使用者原話）→ 層級（voice／style／text／cue）→ 規則 → 做法 → 證據 → 狀態。新規則先是「驗證中」，**第二次確認、含不同句子**才升「定案」；無效則「推翻」並保留證據；與舊定案相反則標「爭議」、兩筆都留。拼盤回合（同一句三種 style 並排）勝出者，必須再經一次單變數回合才能定案。schema 見 `learnings/README.md`。

## 限制與未驗事項

- 免費層額度：flash 每天約 10 次請求、lite 分開算（以你的專案實際 429 為準）；本 skill 為免費層設計，要不要付費由你決定。
- `voices.create`（API 造聲）在免費層是否開放、接受哪些 `language_code`、是否與 flash 共用額度，都還沒實測——目前網頁版為主。詳見 `learnings/api-facts.md`，每列都帶驗證方式與日期。
- 台灣腔有上限：若 voice 層連 3 輪沒移動，可能是 prompted 音色唸不出台灣腔，屆時需改走其他方案（`SKILL.md` 有退路說明）。
- 配樂、音效、混音不在範圍內；旁白成品可接任何混音工具。

## 資料夾

```
gemini-voice-lab/
├── SKILL.md              agent 讀的工作流與鐵則
├── scripts/
│   ├── tts_gemini.py     合成引擎（配音譜、風格表、--one-request、--dry-run、SRT）
│   ├── cues.py           配音譜解析（純函式）
│   └── voices.py         自建音色的列／查／刪／建／探針；--selfcheck 離線自檢
├── learnings/            規則庫（rules.md）、API 事實（api-facts.md）、schema（README.md）
├── examples/範例角色/     可直接跑的起手範本：基準稿、cues、styles、空白回合日誌
├── lab/                  你的工作夾（.gitignore 已擋）
└── private/              你的私人檔：本人聲音檔、ID（.gitignore 已擋）
```

## 授權

[MIT](LICENSE)

---

## English summary

**gemini-voice-lab** is a Claude Code skill (plain `SKILL.md`, works with any agent that reads it) for tuning Gemini 3.8 TTS into natural Taiwanese-Mandarin speech through a turn-based loop: each turn changes one thing and spends exactly one request.

- **Design for the free tier**: roughly 10 flash requests/day, lite counted separately (your own 429s are the source of truth); `--one-request` packs a whole script into one call. Whether to pay is your decision.
- **Three handles**: voice (persona/accent, built once), style (one short sentence per segment), text (verbatim wording, replacements, inline tags). A listener's one-line comment is mapped to exactly one of them.
- **Voices**: design a character voice or replicate your own (consent-gated, yourself only) in AI Studio's web UI, or via `scripts/voices.py` (list/get/delete/design/replicate/probe).
- **Rules library**: every comment is written back to `learnings/rules.md` (trial -> confirmed after a second check on a different sentence; overturned entries are kept).
- **Requirements**: Python 3.10+, `pip install google-genai`, ffmpeg, `GEMINI_API_KEY`. Run `python scripts/voices.py --selfcheck` (offline) and a `--dry-run` first.
- **License**: MIT.
