---
name: gemini-voice-lab
description: >-
  Gemini 3.8 Flash TTS 配音的「聲音實驗室」：用回合制把雲端配音調成自然的台灣口語——每回合只燒 1 次請求，
  使用者聽完回一句評語（像大陸腔／太平／尾音拖／唸錯字），Claude 歸到 voice／style／text／cue 其中一支把手，
  查規則庫套處方或開新規則，立刻回寫 learnings；也負責建與管專屬音色（voices.py design 用描述造角色聲——免費層可建、
  Voice replication 複製使用者本人聲音、voices.py 列／查／刪／到期重建），角色定案回寫角色設定卡。
  本 skill 為免費層設計（flash 每天約 10 次請求），額度用完切 AI Studio 網頁版備援。
  觸發：「訓練 Gemini 配音」「配音訓練／配音回合／下一回合／上次練到哪」「聲音實驗室」「設計角色聲音／幫角色做專屬音色」
  「複製我的聲音到 Gemini」「這樣唸不像台灣人／像大陸腔／像播音員／像在朗讀」「音色到期／續期」「今天還剩幾發」「開微調介面」。
  不適用：一次到位的配音成品（直接跑 scripts/tts_gemini.py 即可）、配樂／音效／混音。
---

# gemini-voice-lab

Gemini TTS 只有三支把手：**voice**（人設／口音，建一次）、**style**（每段一句，越短越不漂）、**text**（逐字照唸——用字、語尾、替換字、`<tag>` 都是把手）。這個 skill 做的事就是把使用者的一句評語落到正確的那一支，並把學到的寫進 `learnings/`。合成用本 skill 自帶的 `scripts\tts_gemini.py`（配音譜、風格表、`--one-request`、`--dry-run`、SRT 都在裡面），自建音色用 `scripts\voices.py`；兩支都在本 skill 資料夾（repo 根）執行，不需要別的 skill。

> 路徑與指令以 Windows 寫法呈現（`\`、PowerShell）；macOS／Linux 把 `\` 換成 `/`、`python` 多半要寫 `python3`、金鑰改用 `export GEMINI_API_KEY=…` 即可。

## 鐵則

1. **免費層設計**。本 skill 為免費層設計：flash 每天約 10 次請求（太平洋時間午夜重置＝台灣 15:00，PST 期間 16:00）、lite 分開算，一切以你的專案實際 429 為準；要不要付費自行決定，skill 不替你升層。用完切網頁版備援，**不**把演技判斷改到 lite。
2. **一輪只改一件事、只燒 1 發 flash（每次 flash 呼叫必帶 `--one-request`，否則一段一發）、基準稿整個訓練期不換字。** 沒收到評語不開下一發。
3. **人耳裁定**：好壞由使用者說，Claude 只分類、查表、記錄。
4. **當場回寫**：收到評語立刻改 `learnings/rules.md` 與回合日誌；不等收工。
5. **隱私**：`voice_…` ID、同意錄音、本人音檔不進 git（`.gitignore` 已擋 `lab/`、`private/` 與常見音檔副檔名）；角色的 prompted `voice_…` 只寫進該角色的設定卡（若那張卡會公開，先確認可公開）；**使用者本人** replicated 的 ID 只存 repo 外（或 `private\` 內）的私人檔，不進任何角色卡、不進 git。複製聲音只做使用者本人，他人一律拒絕。
6. **CJK 走檔案**：`--cues`／`--styles`／`--desc-file` 都是 UTF-8 檔；`voices.py --name` 只收 ASCII slug。
7. **金鑰**：`GEMINI_API_KEY` 環境變數；Windows 下 `voices.py` 會從登錄檔 `HKCU\Environment` 補讀；`tts_gemini` 用 PowerShell 注入（見 r01 指令）；絕不印出。

## 先讀後動（每次開工）

1. `learnings/api-facts.md`（今天還能做什麼）→ 2. `learnings/rules.md`「目前底色」＋狀態=定案的條目＋替換表 → 3. 角色設定卡的聲演層（有的話；沒有就看回合日誌「目前最佳」）→ 4. 若 `lab\<角色>\回合日誌.md` 存在，讀「目前最佳」「下一棒」直接續跑，不重問。

## 第 1 階段：開一個角色的訓練檔

- 工作夾 `lab\<角色>\`（`.gitignore` 已擋，不進 git）。`examples\範例角色\` 是一份可跑的起手範本，整夾複製過去改字即可。
- **基準稿.txt**：60~90 字、角色語體，**固定含六個檢核點**：一個問句、一條逗號長句、一組數字／年份、一個台灣語尾（啊／嘛／喔／欸）、一個台陸異讀字（和／垃圾／法／企／液）、一處【停 1.0】。整個訓練期不改字。
- 套規則：替換表＋角色卡上的唸法校正表 → 手寫 `cues.txt`（配音譜格式，見 `scripts\cues.py` 檔頭）；`styles.txt` 第一行 `底色: <rules.md 目前底色>`。先 `--dry-run`（0 發）驗切段：
  ```powershell
  python scripts\tts_gemini.py --cues lab\<角色>\cues.txt --styles lab\<角色>\styles.txt --dry-run --out lab\<角色>\r00_dry.wav
  ```
- 第一個角色：先挑一個沒鎖別家引擎的角色；角色卡若已鎖 ElevenLabs／edge-tts 等，換引擎要使用者明說。

### 造聲（預設走 API `voices.py design`；AI Studio 網頁版 Voice Design 需付費層 key——免費層實測進不去）

- 入口 `https://aistudio.google.com/generate-speech`。用你手上的瀏覽器自動化工具（或手動）操作 AI Studio；需使用者瀏覽器已登入，遇登入牆／驗證就停下交還使用者。
- **角色 Voice design**：從角色卡的年齡／性別／語氣人設寫 1~2 句英文描述（必含 `Taiwanese Mandarin accent` 與 `conversational` 兩個用語），存 `lab\<角色>\voice\desc-N.txt` → `python scripts\voices.py design --name <slug> --gender female|male|neutral --lang cmn-TW --desc-file <desc> --out <sample.wav>`（免費層實測可建、`cmn-TW` 可收；最後一行 JSON 就是 id，sample 給使用者試聽）→ `python scripts\voices.py list` 確認登錄簿 → 描述原句、id、expire_time 寫進回合日誌「目前最佳」（角色卡等定案才寫）。每角色每天 ≤3 版；落選的 `voices.py delete <id> --yes`。網頁版 Voice Design 只給付費 key，別按 Set up billing。
- **本人 Voice replication**（只做使用者本人）：先過**同意閘**——Claude 貼出授權句原文（官方僅簡中文本，國語照唸）：「我是此声音的拥有者并授权谷歌使用此声音创建语音合成模型」；使用者在聊天室確認 (a) 是本人聲音 (b) 同意上傳 Google。錄音：手機安靜處錄兩段（授權句 ≥3 秒、自然說話 15~20 秒），任何方式傳到電腦即可（例：存成 `private\gemini\{consent,source}.m4a`）；網頁版可直接上傳這兩段建音色，或退路 `voices.py replicate`。本人 replicated 的 ID 只存 repo 外（或 `private\` 內）的私人檔，不進任何角色卡、不進 git。
- **退路鏈**：API `design` 失敗才跑 `voices.py probe`（作者 2026-10-05 實測 create 可用、cmn-TW 可收，正常不必再跑）；probe **exit 2**（403／billing）（exit 2 先看 stderr：403／billing 才是『不開放』；若是全部語言碼被拒則改 `--langs` 再試）→ `api-facts.md` 記「voices.create 免費層不開放（日期）」，本節 API 造聲標不可用：角色改用預製音色（Sulafat／Achernar／Gacrux）＋底色加 `Taiwanese Mandarin accent`，本人聲音走本機克隆方案（如 F5-TTS），規則庫迴圈照跑；probe 只收 `en-US` → 仍可用，描述句帶 `Taiwanese Mandarin accent`，實際語言由 cue 文字決定（記 api-facts）。
- **台灣腔上限**：voice 層連 3 輪腔沒移動 → 停，在本節寫明「prompted 音色唸不出台灣腔」上限，「台灣腔」需求改走本機克隆方案（如 F5-TTS，本人聲天生台灣腔）或其他支援 zh-TW 的 TTS（如 edge-tts zh-TW）。

## 第 2 階段：回合迴圈（一輪＝1 發 flash）

**前置閘（0 發）**：回合日誌 `flash n/10` 還有餘額且不吃到**保留的 2 發**（開場先問「今天有沒有要出成品？」沒有就在重置前 2 小時釋放）；上一輪已有評語；本輪只改一個變數；cue 已 dry-run。

**r01 基線**：
```powershell
$env:GEMINI_API_KEY=[Environment]::GetEnvironmentVariable('GEMINI_API_KEY','User'); $env:PYTHONUTF8=1; python scripts\tts_gemini.py --cues lab\<角色>\cues.txt --styles lab\<角色>\styles.txt --voice <voice_id 或 Sulafat> --one-request --model flash --out lab\<角色>\r01.mp3
```
tts_gemini 走 PowerShell 並注入金鑰（agent 的工具環境常讀不到使用者層變數）；voices.py 自己會從登錄檔補金鑰，直接 `python` 即可。絕不印出金鑰。

把音檔交給使用者試聽，caption 一行「基線：voice X／底色 Y／套用 R-001,R-004」，問「一句話就好」。

**評語→把手對照表**

| 使用者原話像… | 把手 | 處方方向 |
|---|---|---|
| 像大陸腔／捲舌／兒化／不像台灣人 | voice | 改音色描述句重建；退路：底色加 `Taiwanese Mandarin accent` |
| 音調太低／太高（一點點） | 後製 | style 句 `slightly higher pitch` 實測推不動 f0；用 `ffmpeg -i in.mp3 -af "rubberband=pitch=1.0595:formant=preserved" out.mp3`（+1 半音；-1 半音 0.9439），0 請求、節奏不動；差很多才重寫描述重建 |
| 太平／像念經／像播音員／尾音拖／太快太慢／沒感情 | style | 底色或該段 style 換**一個**短語（≤3 個形容詞） |
| 唸錯字／太書面／不像人講話／數字怪 | text | 替換表加一組 `原→替`、數字寫中文數字、句子拆逗號 |
| 停頓怪／接太快／斷在奇怪的地方 | cue | 【停 X】位置或 `<breath>`；**句間要拖長→後製** `python scripts\stretch_pauses.py --in rNN.mp3 --out rNN+1.mp3 --add 0.35 --min 0.5`（0 請求；行內 `<short pause>` 實測對空白長度無效） |
| 跟上次不一樣／怎麼變了 | 漂移 | 0 額度：比 voice id、styles.txt 字數、基準稿有沒有被改 |

聽不懂的評語（「怪怪的」）先問一個二選一（腔？速度？字？）再花額度；一句含兩件事拆兩輪。

**重複檢查（作者 2026-10-05 實測）**：flash 的兩段 one-request 可能把最後一段唸兩遍（同稿連續 2/2；lite 不會）。短稿（≤2 段、≤100 字）一律單段 `--file`＋行內 `<long pause>`；多段譜出來**先用 whisper 類工具逐段查有沒有重複**再給使用者，重複就別算回合、重跑或改單段。

**查規則庫**：`rules.md` 以「症狀」欄 grep；命中→套處方、命中 +1；沒命中→開新條目（狀態=驗證中），只改這一件事。

**兩種回合型**
- **單變數回合**（預設）：同基準稿只動一處→`rNN.mp3`；把**硬碟上的目前最佳檔**和 rNN.mp3 一起交給使用者，caption 寫唯一差異。基線不重渲（prompted voice 有隨機性，重渲的 A 已不是使用者點頭過的那條）。
  送檔 caption **必列本輪實際套用的替換組**（例「和→跟、2026→二零二六、10 分鐘→十分鐘」）；使用者說「那個字不要換」→ `rules.md` 替換表該行前加 `#` 停用，並開一條狀態=推翻的條目記證據。
- **拼盤回合**（掃一條 style 軸，一天 ≤2 次）：同一句重複三段、段首口頭標籤、各掛【A】【B】【C】；勝出者**必再經一次單變數回合**才能定案。範本：
  ```
  【A】
  一。接下來的十分鐘，什麼都不用做，只要聽就好。
  【停 1.0】
  【B】
  二。接下來的十分鐘，什麼都不用做，只要聽就好。
  【停 1.0】
  【C】
  三。接下來的十分鐘，什麼都不用做，只要聽就好。
  ```
  `styles.txt`：`底色: …`／`A: …`／`B: …`／`C: …`。拼盤同樣 `--one-request`（否則三段＝三發）。caption 列「一=…／二=…／三=…」。
  跑 `--cues` 時 stderr 會出現三行「警告: 不認識的 preset「A」，當平穩處理…」——那是 `cues.parse_cues` 的既有雜訊，**style 仍由 styles.txt 正確套上**（tts_gemini 用 `STYLE_PRESETS.get(name)` 撈得到新鍵）；忽略，不必改 `cues.py`。

**當場蒸餾**：使用者回「好／沒差／更糟」→ 立刻改 `rules.md`（有效→驗證中，第二次確認升定案；無效→推翻並保留證據；跟舊定案相反→爭議，兩筆都留、問使用者），回合日誌加一列。

**節奏**：每日 ≤6 輪；使用者說累或連兩輪「沒差」就停；每第 3 輪改渲角色卡上「對白樣本」防過擬合；日末寫「下一棒」。lite 只驗 text／cue 層（替換字、數字、斷句），標 `lite 已驗／flash 待複驗`。

**flash 429 當天**：切網頁版備援——用瀏覽器自動化工具（或手動）在 AI Studio 貼同一份稿＋style 生成、下載音檔（下載前問使用者一次）進同一回合夾，日誌模型欄記 `studio`。

**回合日誌範本**（`lab\<角色>\回合日誌.md`；空表在 `examples\範例角色\回合日誌.md`）
```markdown
# 配音實驗室：<角色>
## 目前最佳
- voice：voice_xxxx（prompted，expire_time YYYY-MM-DD，描述見 voice\desc-N.txt）
- 底色：…
- 套用規則：R-001 R-003 ＋ 角色卡校正表 N 組
- 最佳檔：rNN.mp3
## 基準稿：基準稿.txt（固定不改字）；基準稿B＝角色卡上對白樣本（每第 3 輪用）
## 輪次
| 輪 | 日期 | 模型 | flash | 只改了什麼 | 使用者原話 | 判定 | 規則 |
|---|---|---|---|---|---|---|---|
## 下一棒
- 
```

## 第 3 階段：定案與回存（使用者說「可以了／定案」）

1. 花 1 發 flash 用定案設定渲角色卡上「對白樣本」3 句→`定案_YYYYMMDD.mp3`。
2. 把定案參數回寫角色設定卡的聲演層（欄位：engine／voice／base／情緒位移覆寫／唸法校正表／定案試聽檔／使用限制，各填什麼見文末附錄）：Gemini 的資訊以子項**並列**，不覆寫該卡既有的其他引擎值；版本紀錄加一行；卡若有版控，commit 這次回寫。
3. 全域規則升定案、必要時更新「目前底色」；**不**升進 `tts_gemini.py STYLE_PRESETS`。
4. 之後直接照卡出成品：`python scripts\tts_gemini.py --cues … --voice voice_… --styles <由卡抄出> --one-request --out …`。

## 續跑

「上次練到哪／<角色>配音繼續」→ 讀回合日誌「目前最佳」「下一棒」→ 自建音色先 `voices.py get <id>`（不在／到期→用卡上設計描述重建，再跑 1 輪與定案試聽檔比對）→ 直接提出下一個單一改動。

## 介面（選用）

使用者想自己動手調：`python scripts\ui.py`（只綁 127.0.0.1，自動開瀏覽器；`--port`、`--no-browser`；`--selfcheck` 離線自檢）。生成區＝`tts_gemini.py --one-request`（每按 1 發），後製區＝`stretch_pauses.py`＋ffmpeg rubberband（0 發，試聽即成品）；每輪自動寫進 `lab\<角色>\回合日誌.md`（日期欄含時間，供太平洋日界計數），評語寫進「使用者原話」欄。

- **介面不碰 `rules.md`**：使用者說「整理一下／看日誌」→ 讀回合日誌裡介面留下的列與評語，照第 2 階段「當場蒸餾」回寫規則庫，並補該列的「判定」「規則」兩欄。
- 介面存的 `cues.txt`／`styles.txt` 就是現況；Claude 接手下一輪前先讀這兩個檔與 `.ui_last.json`。
- 後製列（模型＝後製）不花額度；後製能解的評語（音調一點點、句間停頓）引導使用者用滑桿，不必燒 flash。

## voices.py 速查

```
python scripts\voices.py --selfcheck
python scripts\voices.py list [--all] [--json]       # 自建音色登錄簿（含網頁版造的）；不計 TTS 額度
python scripts\voices.py get <voice_id>
python scripts\voices.py delete <voice_id> --yes     # 先問使用者；grep 角色卡與私人 ID 檔為空才刪
python scripts\voices.py design --name slug --gender female|male|neutral --lang cmn-TW --desc-file desc.txt --out sample.wav   # 退路
python scripts\voices.py replicate --source s.m4a --consent c.m4a --name slug --lang cmn-TW --out private\gemini\sample.wav   # 退路，只本人
python scripts\voices.py probe [--langs cmn-TW,zh-TW,zh,cmn-CN,en-US] [--keep]   # 選用：走 API 造聲前跑一次
```
`design`／`replicate` 最後印一行 fact JSON（`id/type/display_name/language_code/expire_time/key/sample`）；`delete` 印 `{"deleted": "<id>"}`；抄事實不 parse 散文。

## 額度表

| 資源 | 每日配置 |
|---|---|
| flash 約 10 發 | 保留 2 發給成品＋1 發基線（首日／換 voice）＋≤6 發訓練（拼盤 ≤2）＋定案日 1 發 |
| lite | text／cue 檢查、新 voice_ 首次可用性測試 |
| AI Studio 網頁版 | flash 鎖死後的**生成**備援（Playground 吃 Pro 配額）；Voice Design 需付費層，不當造聲入口 |
| voices.create（API） | 造聲主線（免費層實測可建）；確認是否共用 flash 前每天 ≤3 次（含 probe）；probe 一次最多試 5 個語言碼，被拒的 400 假設不吃額度（待驗） |
| list／get／delete | 不計額度 |

計數：回合日誌手寫 `flash n/10`；**429 才是真相**（同一把 key 的其他程式用量看不到）。

## 範圍外

配樂、音效、混音不在本 skill 範圍；旁白成品可接任何混音工具。

## 附錄：story-bible 聲演層欄位對照

給角色卡有「聲演層」格式的人：第 3 階段回寫時，卡上 `### 聲線參數` 既有的 `- **欄位**：值` 行照下表填（多行值用兩格縮排子項；**不新增欄位名、不覆寫 edge-tts／ElevenLabs 原值**，Gemini 以子項並列）。不用這套卡的人，把同樣七項寫進你自己的角色設定卡即可。

| 欄位 | 填什麼 |
|---|---|
| `engine` | 加「Gemini 3.8 Flash TTS（…）」 |
| `voice` | 加子項「Gemini：`voice_…`（prompted，display_name、expire_time）」＋「設計描述（重建用，逐字）」 |
| `base` | 加子項「Gemini：`--style "…"`」 |
| `情緒位移覆寫` | `styles.txt` 逐行 |
| `唸法校正表` | 角色專屬的替換字 |
| `定案試聽檔` | 定案 mp3 的路徑 |
| `使用限制` | 註到期（expire_time）與重建法 |
