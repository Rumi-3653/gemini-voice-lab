# gemini-voice-lab — 用回合制把 Gemini 配音調成自然的台灣口語

> 給 AI 配音創作者的「聲音實驗室」：每回合只花 1 次免費請求，聽完說一句評語，評語歸到三支把手之一，規則寫回規則庫，下次直接套。
>
> English summary below. ↓

## 這是什麼

Gemini TTS 只有三支把手：**voice**（音色人設／口音，建一次）、**style**（每段一句英文風格描述）、**text**（逐字照唸——用字、語尾、替換字、`<sigh>` 這類行內標籤都算）。「像大陸腔」「太平」「唸錯字」這種評語，其實各自對應其中一支。

這個 skill 把調聲變成回合制：每回合只改一件事、只燒 1 發 flash 請求；使用者用耳朵裁定好壞；Claude 負責分類、查規則庫、記錄，並維護專屬音色（`voices.py design` 用描述造聲——免費層實測可建；`list`／`get`／`delete`／到期重建）。**自帶引擎**（`scripts/tts_gemini.py`），不依賴其他 skill。

## 需要什麼

- Python 3.10+
- `pip install google-genai`
- ffmpeg（在 PATH 上；輸出 mp3／m4a 與重採樣會用到；微調介面的音高／語速需含 rubberband 的版本）
- 微調介面另需：`pip install numpy`（後製拉停頓）；Windows 再加 `pip install tzdata`（額度日界要時區資料）
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

## 微調介面（選用）

不想打指令的話，開本機網頁：

```powershell
python scripts\ui.py               # 開 http://127.0.0.1:8765/（--port 改埠、--no-browser 不自動開）
python scripts\ui.py --selfcheck   # 離線自檢（不連網、0 次請求）
python scripts\ui.py --install-shortcut   # Windows：桌面建「聲音實驗室」捷徑（Edge 獨立視窗、不跳黑窗）
python scripts\ui.py --lab D:\某處\角色們   # 角色資料夾放在 repo 外；配 --install-shortcut 會寫進捷徑
```

桌面捷徑：已經開著時再點只會叫出視窗、不會開第二份；關掉視窗約 3 分鐘後伺服器自己結束。沒有主控台時的訊息寫在 `lab\.ui.log`。

左邊改音色／底色／配音譜後按「生成」（每按一次用 1 次請求，固定 `--one-request`）；右邊用滑桿調音高、語速、句間停頓、音量（本機後製，不花請求，需 `numpy`）。每輪自動記進 `lab\<角色>\回合日誌.md`，評語直接在頁面上打。介面只綁本機、不改 `learnings/rules.md`——規則歸納仍照 `SKILL.md` 做。

- **配色**：右上角「配色」選單，跟隨系統／米白／夜間錄音室（深色）／莫蘭迪灰綠／暖橘日落，選了會記住；各套文字對比度都 ≥4.5:1。
- **新增角色**：角色選單旁「＋ 新增角色」，選一個範本建資料夾（名字自動換進配音譜與回合日誌）：範例角色、紀錄片旁白、Podcast主持、客服語音、童書說故事、廣告配音。每個範本附一句建議的造聲描述（`voice\desc-0.txt`），造聲區會自動帶入。範本台詞與描述都是本 repo 原創，跟著 MIT 授權。
- **放入範例音檔**：「輸入聲音」區的按鈕，把 `examples\_範例音檔\` 的可商用錄音複製進目前角色，不花額度就能試後製、波形、A/B。只供試聽與後製，**不拿來複製聲音**；授權見下方「範例音檔」。
- **金鑰**：右上角「未設定金鑰」點開貼上；勾「記住」會寫進 Windows 使用者環境變數（等同 `setx`）。金鑰只交給本機腳本，不回傳頁面、不寫日誌；驗證用 `voices.py list`，不花生成額度。
- **波形**：原始／調整後兩條波形同一個時間尺，停頓拉長多少一眼看得出；點波形跳到該位置。
- **A/B 對聽**：日誌區選兩個檔，「A⇄B 切換」停在同一個時間點換邊。
- **輸入聲音**：頁面最上方，點一下選檔或把錄音檔拖進視窗（可一次多個），存進角色資料夾，後製、A/B、複製聲音都能選；旁邊「複製我的聲音 ↓」直接跳到複製區。
- **造聲**：用描述造（`voices.py design`）或複製聲音（`voices.py replicate`，本人或已取得聲音主人同意；參考音 10~30 秒＋授權句 ≥3 秒，授權句由聲音主人親自錄，送出前要勾同意；區塊下方列相關法規）；兩種合計每天最多 3 個，建好自動選進音色選單，日誌記一列「造聲N」。

## 兩種造聲

預設走 **API**（`voices.py design`／`replicate`，免費層實測可建、`language_code: cmn-TW` 可收）。AI Studio 網頁版（<https://aistudio.google.com/generate-speech>）的 Voice Design **需付費層 key**，免費 key 進不去（2026-10-05 實測）；網頁版 Playground 只拿來當 flash 額度用完後的生成備援（有 Google AI Pro 者配額較高）。

- **Voice design（角色聲）**：寫 1~2 句英文描述（含 `Taiwanese Mandarin accent`、`conversational`）→ `python scripts\voices.py design --name my-char --gender female --lang cmn-TW --desc-file lab\<角色>\voice\desc-1.txt --out lab\<角色>\voice\design-1.wav`（最後一行 JSON 就是 `voice_…` id，sample 先聽）→ `python scripts\voices.py list` 確認登錄簿。
- **Voice replication（本人，或已取得聲音主人同意）**：先過同意閘（聲音主人親自念授權句；確認是本人，或已取得對方同意——建議留書面——並同意上傳 Google）；手機安靜處錄兩段（授權句 ≥3 秒、自然說話 15~20 秒）→ `voices.py replicate --source … --consent …`（網頁版 Voice Replication 同樣在付費層）。replicated 的 ID 只存 repo 外或 `private\` 內的私人檔，不進角色卡、不進 git。拿不出同意就不做，也不拿配音員／歌手已發表的錄音當參考音。

  相關規範（台灣法規 2026-10 依全國法規資料庫查證；僅供參考，不是法律意見）：
  - **Google**：Voice replication 要聲音主人親自唸授權句；[生成式 AI 使用政策](https://policies.google.com/terms/generative-ai/use-policy)禁止未揭露地冒充他人來欺騙。
  - **民法 §18、§195**：聲音能辨識一個人，屬人格權保護範圍；未經同意使用，對方可請求除去侵害、賠償損害。
  - **個人資料保護法 §2、§19**：可直接或間接識別個人的資料都算個資，聲音也是；蒐集、利用原則上要當事人同意。
  - **刑法 §339-4 第 1 項第 4 款**：用電腦合成他人不實的影像、聲音來詐騙＝加重詐欺，處 1～7 年有期徒刑，得併科 100 萬元以下罰金。
  - **著作權法 §7-1**：表演受著作權保護；別拿配音員、歌手已發表的錄音當參考音。

## 規則庫怎麼長

`learnings/rules.md` 起始是空的（只有「目前底色」與一張替換表）。每回合收到評語就當場回寫一條：症狀（逐字抄使用者原話）→ 層級（voice／style／text／cue）→ 規則 → 做法 → 證據 → 狀態。新規則先是「驗證中」，**第二次確認、含不同句子**才升「定案」；無效則「推翻」並保留證據；與舊定案相反則標「爭議」、兩筆都留。拼盤回合（同一句三種 style 並排）勝出者，必須再經一次單變數回合才能定案。schema 見 `learnings/README.md`。

## 限制與未驗事項

- 免費層額度：flash 每天約 10 次請求、lite 分開算（以你的專案實際 429 為準）；本 skill 為免費層設計，要不要付費由你決定。
- `voices.create`（API 造聲）免費層**可用**、`cmn-TW` 可收（2026-10-05 實測）；是否與 flash 共用每日額度仍待驗。詳見 `learnings/api-facts.md`，每列都帶驗證方式與日期。
- 台灣腔有上限：若 voice 層連 3 輪沒移動，可能是 prompted 音色唸不出台灣腔，屆時需改走其他方案（`SKILL.md` 有退路說明）。
- 配樂、音效、混音不在範圍內；旁白成品可接任何混音工具。

## 資料夾

```
gemini-voice-lab/
├── SKILL.md              agent 讀的工作流與鐵則
├── scripts/
│   ├── tts_gemini.py     合成引擎（配音譜、風格表、--one-request、--dry-run、SRT）
│   ├── cues.py           配音譜解析（純函式）
│   ├── stretch_pauses.py 後製：拉長句間空白（0 次請求）
│   ├── ui.py / ui.html   本機微調介面（生成＋後製＋波形＋A/B＋造聲／複製＋回合日誌）；--selfcheck 離線自檢
│   └── voices.py         自建音色的列／查／刪／建／探針；--selfcheck 離線自檢
├── learnings/            規則庫（rules.md）、API 事實（api-facts.md）、schema（README.md）
├── examples/範例角色/     可直接跑的起手範本：基準稿、cues、styles、空白回合日誌、建議造聲描述
├── examples/紀錄片旁白…  另外 5 個用途範本（紀錄片旁白／Podcast主持／客服語音／童書說故事／廣告配音）
├── examples/_範例音檔/   可商用的中文說話錄音（CC0／CC BY-SA 4.0，不屬 MIT，見其中的來源與授權.md）
├── lab/                  你的工作夾（.gitignore 已擋）
└── private/              你的私人檔：複製用的錄音、ID（.gitignore 已擋）
```

## 授權

[MIT](LICENSE)

---

## English summary

**gemini-voice-lab** is a Claude Code skill (plain `SKILL.md`, works with any agent that reads it) for tuning Gemini 3.8 TTS into natural Taiwanese-Mandarin speech through a turn-based loop: each turn changes one thing and spends exactly one request.

- **Design for the free tier**: roughly 10 flash requests/day, lite counted separately (your own 429s are the source of truth); `--one-request` packs a whole script into one call. Whether to pay is your decision.
- **Three handles**: voice (persona/accent, built once), style (one short sentence per segment), text (verbatim wording, replacements, inline tags). A listener's one-line comment is mapped to exactly one of them.
- **Voices**: design a character voice or replicate a voice with its owner's consent (yourself, or someone who agreed and recorded the consent line themselves) in AI Studio's web UI, or via `scripts/voices.py` (list/get/delete/design/replicate/probe).
- **Rules library**: every comment is written back to `learnings/rules.md` (trial -> confirmed after a second check on a different sentence; overturned entries are kept).
- **Local UI (optional)**: `python scripts/ui.py` opens a localhost-only page to regenerate (one request per click) and to post-tune pitch, tempo, pauses and volume for free (with waveforms and A/B switching); it can also take your API key, upload your own recordings, and create voices (design from a description, or replicate a voice with its owner's consent behind a checkbox, with the relevant Taiwan laws listed below it; 3 per day); every round is logged to the character's round log.
- **Requirements**: Python 3.10+, `pip install google-genai`, ffmpeg, `GEMINI_API_KEY` (the local UI also needs `numpy`, an ffmpeg build with rubberband, and `tzdata` on Windows). Run `python scripts/voices.py --selfcheck` (offline) and a `--dry-run` first.
- **License**: MIT.
