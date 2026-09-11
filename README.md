# 一個爛bot(把以前寫過的一些bot東拼西凑讓他能勉强上線)
## 縫合了 大概是2021-22期間寫的3只機器人的 **奇美拉機器人**

## AI Calendar

已加入 `ccpt/cmd/calendar.py`，依規格在指定 Discord 頻道維護單一行事曆訊息，資料使用 SQLite 保存於 `data/calendar.db`。

啟用前請將 `.env.example` 複製為 `.env`，設定 `DISCORD_TOKEN`、`CALENDAR_CHANNEL_ID` 與 `CALENDAR_OWNER_ID`。Bot 需要 View Channel、Send Messages、Embed Links、Read Message History、Manage Messages 權限。指令前綴為 `ai-calendar`，完整格式可在頻道輸入 `ai-calendar help` 查看。

每日備份會保存於 `data/backups/`，預設時區為 `Asia/Taipei`、顯示未來 14 天。未設定 `CALENDAR_CHANNEL_ID` 時，Calendar 模組會保持停用，不影響既有功能。
