# api-facts — 只放帶日期與驗證方式的事實

| 事實 | 值 | 驗證方式 | 日期 |
|---|---|---|---|
| gemini-3.8-flash-tts 免費層 RPD | 10／天，台灣 15:00 重置（PST 期間 16:00） | 2026-10-02 撞 429 per day（作者的專案；你的專案以實際 429 為準） | 2026-10-02 |
| gemini-3.8-flash-lite-tts RPD | 未知，與 flash 分開算 | flash 鎖死時 lite 仍可用 | 2026-10-02 |
| voices.list 可用、不是 TTS 請求 | 2089 預製／30 語系，無 zh/cmn/yue；回 VoiceOutput（id/type/display_name/language_code/model/gender/expire_time/key/sample_audio；無 create_time） | client.voices.list() | 2026-10-05 |
| AI Pro 訂閱不改 API 額度層；只加 AI Studio 網頁版配額 | 官方 google-ai-plans 頁（2026-08-18）＋員工論壇（2026-08-03） | WebFetch | 2026-10-05 |
| **AI Studio 網頁版 Voice Design 需付費層** | 彈窗明寫「Voice Design runs on the paid tier. Link a paid API key to continue」；AI Pro 網頁配額不涵蓋 | 作者以瀏覽器實測（未按 Set up billing） | 2026-10-05 |
| prompted 必須 store=true | 是（否則 INVALID_ARGUMENT） | SDK 2.27.0 voices.create docstring | 2026-10-05 |
| stored voice TTL | 不活動 1 年，合成即延 expire_time；超過專案上限（200）回 RESOURCE_EXHAUSTED | 官方 voice-design 頁 | 2026-10-05 |
| **voices.create 免費層可用** | 是：`voices.py probe` 建 prompted 音色成功（exit 0），自動刪除後 list 為空 | voices.py probe | 2026-10-05 |
| voices.create 接受的 language_code | `cmn-TW` 第一個就被接受（其餘候選未試） | voices.py probe | 2026-10-05 |
| **flash 兩段 one-request 可能把最後一段唸兩遍** | 作者同一稿（2 段＋【停 1.0】）連續 2/2 重複；lite 同請求不重複；單段 `--file`＋行內 `<long pause>` 在 flash 乾淨 | whisper 逐段時間軸比對 | 2026-10-05 |
| 行內 `<short pause>` 加在每個句號後，**句間空白長度沒有可量測變化**（lite） | silencedetect 比對：句間空白都 0.5~1.3 s，總長幾乎相同；flash 未驗 | ffmpeg silencedetect | 2026-10-05 |
| 句間停頓要「拖一點點」用後製 `scripts/stretch_pauses.py` 最準（0 請求） | 每個 ≥0.5 s 空白中點插固定秒數，語氣不動；作者實測 +0.6 s 剛好 | 自檢＋實作 | 2026-10-05 |
| style 句 `slightly higher pitch` **推不動音高**（flash、prompted 音色） | 作者實測 f0 中位 190→186 Hz，反而節奏變快；音高用 ffmpeg `rubberband=pitch=…:formant=preserved` 後製（+1 半音 190→200 Hz，長度不變） | numpy 自相關粗估 f0 | 2026-10-05 |
| prompted 音色的 `sample_audio` 是**英文示範句**（即使 language_code=cmn-TW） | 約 15 s 英文；判腔／判語言要用 lite 唸中文稿，別拿 sample 當中文試聽 | whisper 轉錄（lang=en） | 2026-10-05 |
| voices.create 吃不吃 flash 10 發 | 待 flash 鎖死日 probe | | |
| 400 被拒吃不吃額度 | 假設不吃，待 probe 當天觀察 | | |
