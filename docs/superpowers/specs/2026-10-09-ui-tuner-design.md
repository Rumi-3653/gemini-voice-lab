# 設計：聲音實驗室微調介面（ui.py）

日期：2026-10-09　狀態：待使用者審閱

## 目的

讓使用者不必開 Claude，自己在本機網頁上微調一個角色的配音：

- **生成微調（花額度）**：改音色、模型、底色、配音譜、風格表 → 送 Gemini 生成一輪。
- **後製微調（0 額度）**：對已生成的音檔調音高、語速、句間停頓、音量，即時試聽，滿意再存檔。

介面**只負責記錄**：每輪自動寫進 `lab\<角色>\回合日誌.md`、使用者可在介面上打評語；把評語歸納成 `learnings/rules.md` 規則仍由 Claude 依 SKILL.md 處理（介面不碰 `rules.md`）。

成功標準：

1. `python scripts\ui.py` 開出瀏覽器頁面，選角色後能 dry-run、生成（帶 `--one-request`）、後製試聽與存檔。
2. 每次成功生成／後製存檔，回合日誌多一列；評語寫回該列「使用者原話」欄。
3. 頂列顯示今日 flash 已用次數（太平洋時間日界），429 每日額度用完時頁面明確顯示。
4. `python scripts\ui.py --selfcheck` 不連網、不花額度、全部通過。
5. 既有 `tts_gemini.py`／`stretch_pauses.py`／`cues.py`／`voices.py` 不改。

## 畫面

單頁，四區：

| 區 | 內容 |
|---|---|
| 頂列 | 角色選單（`lab\` 下的資料夾）、今日 `flash n/10 · 保留 2`、重置時間（台灣 15:00；PST 期間 16:00，依太平洋時間自動算） |
| 生成（花額度） | 音色（30 款預製 ＋ `voices.py list --json` 的自建音色）、模型 flash/lite、底色、配音譜編輯框（`cues.txt`）、風格表編輯框（`styles.txt`，底色行由「底色」欄位同步）；「跟上一輪比改了：…」提示（>1 處亮黃，不擋）；按鈕「試切段（免費）」＝`--dry-run`、「生成 rNN」 |
| 後製（免費） | 來源檔選單（該角色資料夾內 mp3/wav）、滑桿：音高 −3～+3 半音（0.5 步）、語速 0.80～1.20×（0.05 步）、句間停頓 +0～+1.0 秒（0.05 步）、音量 −6～+6 dB（1 步）；「原始／調整後」兩個播放鈕、「存檔」 |
| 回合日誌 | 讀 `回合日誌.md`「輪次」表：輪、播放鈕、只改了什麼、評語輸入框（離開焦點即存） |

生成前跳確認「會用掉 1 次，今天第 n/10」；會吃到保留的 2 次時用更醒目的警告文字。配音譜 ≥2 段時，生成結果旁提示「flash 多段可能重複最後一段，聽一下」（SKILL.md 實測事實）。

## 架構

新增兩個檔，既有腳本不改：

- `scripts\ui.py`：標準庫 `http.server.ThreadingHTTPServer`，只綁 `127.0.0.1`（預設埠 8765，`--port` 可改），啟動後開瀏覽器。無第三方相依。
- `scripts\ui.html`：單頁 vanilla JS，不載 CDN（離線可用）。

### API（JSON）

| 方法 路徑 | 作用 |
|---|---|
| `GET /` | 回 `ui.html`，注入本次啟動的隨機 token |
| `GET /api/roles` | `lab\` 下的角色資料夾 |
| `GET /api/state?role=` | `cues.txt`、`styles.txt`、上一輪參數、日誌列、今日 flash 次數、音檔清單 |
| `GET /api/voices` | 預製音色（`tts_gemini.VOICES`）＋ 自建音色（`voices.py list --json` 子程序；不計額度） |
| `POST /api/dryrun` | 寫回 cues/styles → `tts_gemini.py --dry-run` → 回切段表 |
| `POST /api/generate` | 寫回 cues/styles → `tts_gemini.py --cues … --styles … --voice … --model … --one-request --out lab\<角色>\rNN.mp3` → 成功才寫日誌列 |
| `POST /api/preview` | 依滑桿參數產生 `lab\<角色>\.preview.mp3`，回音檔 URL |
| `POST /api/save` | 同 preview 參數輸出 `rNN.mp3` ＋ 日誌列（「後製：音高 +0.5、停頓 +0.30」） |
| `POST /api/comment` | 把評語寫入指定輪的「使用者原話」欄 |
| `GET /audio/<角色>/<檔>` | 只供應 `lab\<角色>\` 內的音檔 |

所有 POST 需帶啟動 token（header），防止其他網頁對 localhost 發請求偷燒額度。

### 生成

- 子程序呼叫既有 `tts_gemini.py`，固定 `--one-request`。
- 金鑰：環境變數沒有就照 `voices.py._key()` 的做法從 `HKCU\Environment` 補進子程序 env；不回傳前端、不寫檔、不印。
- 全域鎖：同時只允許一個生成；生成中按鈕顯示進行中，重複請求回 409。
- 失敗（子程序非 0）：把 stderr 末段（`tts_gemini` 本身已是中文訊息）回前端，不寫日誌。訊息含「額度用完」→ 前端頂列標「今天用完了」。

### 後製

順序固定：

1. 句間停頓 > 0 → 子程序 `stretch_pauses.py --in 來源 --out 暫存.wav --add X --min 0.5`。
2. 音高／語速／音量任一非預設 → `ffmpeg -af "rubberband=pitch=2^(半音/12):tempo=語速:formant=preserved,volume=XdB"`；全為預設則直接轉檔。

preview 與 save 走同一個函式，試聽即成品。

### 日誌

- 檔案：`lab\<角色>\回合日誌.md`（SKILL.md 範本格式）。不存在時用 `examples\範例角色\回合日誌.md` 的空表建立。
- 新增列寫在「## 輪次」表格最後一列之後；欄位：輪、日期、模型、flash（`n/10`，lite／後製寫 `-`）、只改了什麼、使用者原話、判定、規則（後兩欄留空給 Claude）。
- 輪次編號：日誌與資料夾內 `rNN.*` 取最大值 +1，兩位數補零。
- 今日 flash 次數：日誌中模型＝flash 且日期落在今天太平洋時間日界內的列數（`zoneinfo` America/Los_Angeles）。日誌日期欄只有日期，故生成列的日期欄寫台灣日期＋時間（`2026-10-09 14:30`），舊列只有日期者以台灣日期視同。
- 「只改了什麼」：與上一輪生成參數（voice／model／底色／cues 文字／styles）逐項比對自動產生，例如「底色」「cues 文字」；後製列寫參數摘要。上一輪參數存在 `lab\<角色>\.ui_last.json`（lab 已被 .gitignore 擋）。

## 錯誤處理

| 情況 | 行為 |
|---|---|
| 沒有金鑰 | 生成回錯「沒有 GEMINI_API_KEY」，dry-run 與後製照常 |
| 429 每日額度 | 回錯並標記今日用完；不寫日誌 |
| ffmpeg／stretch_pauses 失敗 | 回 stderr 末段；不產檔、不寫日誌 |
| 角色名或檔名含 `..`、路徑分隔符、不在 `lab\` 下 | 400 |
| 日誌檔被手動改壞（找不到「## 輪次」表） | 不覆寫，回錯請使用者檢查 |

## 測試

`python scripts\ui.py --selfcheck`（不連網、0 額度），斷言：

- 日誌：空範本建表 → 追加兩列 → 寫評語 → 解析回來欄位一致；其他段落原樣保留。
- 輪次編號：混合 `r01.mp3`、`r03.wav`、日誌 r02 → 下一輪 r04。
- 今日次數：跨太平洋日界的列不計入。
- 後製濾鏡字串：+1 半音 → `pitch=1.0595`；全預設 → 不加濾鏡。
- 路徑防護：`../x`、`a\b`、絕對路徑 → 拒絕。
- 參數比對：只改底色 → 「底色」。

實機驗證：啟動後用瀏覽器操作「試切段」與「後製試聽／存檔」（0 額度）；真正生成一次由使用者決定是否花額度。

## 文件

- `SKILL.md`：加「介面」小節（啟動指令、介面只記錄不改 rules.md、評語歸納仍走回合迴圈）。
- `README.md`：加一行啟動方式。

## 不做（YAGNI）

- 介面內編輯 `rules.md`、自動分類評語（歸 Claude）。
- 造聲／刪音色（`voices.py` 指令列已足夠）。
- 波形圖、多檔混音、SRT 編輯。
- 瀏覽器端即時 DSP 預覽（伺服器端 ffmpeg 一次產出，試聽即成品）。
