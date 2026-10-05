# api-facts — 只放帶日期與驗證方式的事實

| 事實 | 值 | 驗證方式 | 日期 |
|---|---|---|---|
| gemini-3.8-flash-tts 免費層 RPD | 10／天，台灣 15:00 重置（PST 期間 16:00） | 2026-10-02 撞 429 per day（作者的專案；你的專案以實際 429 為準） | 2026-10-02 |
| gemini-3.8-flash-lite-tts RPD | 未知，與 flash 分開算 | flash 鎖死時 lite 仍可用 | 2026-10-02 |
| voices.list 可用、不是 TTS 請求 | 2089 預製／30 語系，無 zh/cmn/yue；回 VoiceOutput（id/type/display_name/language_code/model/gender/expire_time/key/sample_audio；無 create_time） | client.voices.list() | 2026-10-05 |
| AI Pro 訂閱不改 API 額度層；只加 AI Studio 網頁版配額 | 官方 google-ai-plans 頁（2026-08-18）＋員工論壇（2026-08-03） | WebFetch | 2026-10-05 |
| 網頁版造的 voice_ 同專案 API 可用 | 官方 voice-design 頁「saved to your project」 | WebFetch（待實測） | 2026-10-05 |
| prompted 必須 store=true | 是（否則 INVALID_ARGUMENT） | SDK 2.27.0 voices.create docstring | 2026-10-05 |
| stored voice TTL | 不活動 1 年，合成即延 expire_time；超過專案上限（200）回 RESOURCE_EXHAUSTED | 官方 voice-design 頁 | 2026-10-05 |
| voices.create 免費層可用？ | 待 probe（網頁版為主，選用） | | |
| voices.create 接受的 language_code | 待 probe（cmn-TW→zh-TW→zh→cmn-CN→en-US） | | |
| voices.create 吃不吃 flash 10 發 | 待 flash 鎖死日 probe | | |
| 400 被拒吃不吃額度 | 假設不吃，待 probe 當天觀察 | | |
