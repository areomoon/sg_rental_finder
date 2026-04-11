# 🏠 SG Rental Finder

自動抓取 PropertyGuru / 99.co 租屋資訊，每週三 + 週日早上 8am 發送評分排名摘要到信箱。

- 預算：≤ S$3,800/月
- 地點：靠近 City Hall / Raffles Place（Patsnap @ Funan）
- 類型：Condo 1–2BR
- 搬入：2026-05-04（最遲 2026-05-15）

---

## 快速開始

```bash
git clone https://github.com/areomoon/sg_rental_finder
cd sg_rental_finder
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
# 填寫 .env 後執行：
python run.py --test
```

---

## Step 1：PropertyGuru Saved Search 設定（5 分鐘）

1. 登入 [PropertyGuru](https://www.propertyguru.com.sg)
2. 搜尋條件：
   - Listing Type: **For Rent**
   - Property Type: **Condo / Apartment**
   - Location: **D01, D02, D06, D07, D09, D10, D11**（或輸入 "City Hall"）
   - Price: **S$1,500 – S$3,800/mo**
   - Bedrooms: **1–2**
3. 點 **Save Search**，開啟 **Email Alert: Daily**
4. 確認信箱收到 PropertyGuru 的確認信

> 可選：在 99.co 做同樣設定（from: `@mail.99.co`）

---

## Step 2：Gmail 標籤 + 過濾器（2 分鐘）

1. 開啟 Gmail → 設定（⚙️）→ **查看所有設定** → **標籤**
2. 建立標籤：`Rentals/PropertyGuru`
3. 前往 **篩選器和封鎖的地址** → **建立新篩選器**：
   - 寄件人：`noreply@propertyguru.com.sg`
   - 勾選：**套用標籤** → `Rentals/PropertyGuru`
   - 勾選：**略過收件匣（封存）**
4. （可選）為 99.co 建立同樣的 `Rentals/99co` 標籤，寄件人：`@mail.99.co`

---

## Step 3：Gmail OAuth 設定（10 分鐘）

### 3a. 建立 Google Cloud 專案

1. 前往 [Google Cloud Console](https://console.cloud.google.com)
2. 建立新專案（或使用現有）
3. 左側選單 → **API 和服務** → **啟用 API 和服務**
4. 搜尋 **Gmail API** → 啟用

### 3b. 建立 OAuth 憑證

1. 左側 → **憑證** → **建立憑證** → **OAuth 用戶端 ID**
2. 應用程式類型：**桌面應用程式**
3. 下載 JSON → 儲存為 `config/credentials.json`

### 3c. 設定 OAuth 同意畫面（如果跳出）

- 使用者類型：**外部**
- 加入自己的 Gmail 為「測試使用者」
- Scope：`https://www.googleapis.com/auth/gmail.readonly`

### 3d. 執行 OAuth 流程

```bash
python run.py --auth-gmail
```

瀏覽器會跳出授權頁面 → 登入 → 授權 → token.json 會自動儲存到 `config/token.json`。

---

## Step 4：OneMap.sg 註冊（5 分鐘）

1. 前往 [https://www.onemap.gov.sg](https://www.onemap.gov.sg/apidocs/)
2. 點右上角 **Register** → 填寫資料
3. 確認信箱驗證
4. 把 email + password 填入 `.env`：
   ```
   ONEMAP_EMAIL=your_email@example.com
   ONEMAP_PASSWORD=your_password
   ```

> OneMap 是新加坡政府免費提供的地圖 API，用於計算從租屋到 Funan/Raffles Place 的公共交通時間。

---

## Step 5：.env 設定

```bash
cp .env.example .env
```

填寫以下欄位：

```env
GMAIL_USER=your_gmail@gmail.com
GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx   # 見下方說明
EMAIL_RECIPIENT=your_gmail@gmail.com

ONEMAP_EMAIL=your_email@example.com
ONEMAP_PASSWORD=your_onemap_password
```

### 建立 Gmail App Password

1. 確認 Gmail 已開啟 **兩步驟驗證**
2. 前往 [https://myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. 應用程式：**其他（自訂名稱）** → 輸入 `sg-rental-finder`
4. 複製產生的 16 位密碼 → 填入 `GMAIL_APP_PASSWORD`

---

## Step 6：本機測試

```bash
# 測試模式（不發送郵件，顯示前 10 筆）
python run.py --test

# 預覽摘要
python run.py --preview

# 立即執行（收集 + 發送郵件）
python run.py --now
```

---

## Step 7：GitHub Secrets 設定

GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret 名稱 | 值 |
|---|---|
| `GMAIL_USER` | 你的 Gmail 地址 |
| `GMAIL_APP_PASSWORD` | App Password（16 位） |
| `EMAIL_RECIPIENT` | 收信地址（可同上） |
| `ONEMAP_EMAIL` | OneMap 帳號 email |
| `ONEMAP_PASSWORD` | OneMap 密碼 |
| `GMAIL_OAUTH_TOKEN` | `config/token.json` 的完整內容 |

> 取得 `GMAIL_OAUTH_TOKEN`：執行 `cat config/token.json` 複製全部內容

---

## Step 8：啟動 GitHub Actions

1. Push 到 GitHub（private repo）
2. 前往 repo → **Actions** → **SG Rental Twice-Weekly Digest**
3. 點 **Run workflow** 立即測試一次
4. 確認無誤後，每週三 + 週日 8am SGT 會自動執行

---

## 目錄結構

```
sg_rental_finder/
├── config/
│   ├── settings.yaml           # 預算、MRT 目標、排程設定
│   ├── search_preferences.yaml # 詳細偏好（房型、設施、黑名單）
│   ├── credentials.json        # Gmail OAuth 憑證（gitignored）
│   └── token.json              # Gmail OAuth token（gitignored）
├── src/
│   ├── collectors/
│   │   ├── base.py             # BaseListing 資料模型
│   │   ├── gmail_alerts.py     # 主要：Gmail API 抓取 PG/99co alerts
│   │   ├── propertyguru_scraper.py  # 備援：Playwright
│   │   └── ninetynineco_scraper.py  # 備援：Playwright
│   ├── processor/
│   │   ├── parser.py           # 解析 Email HTML
│   │   ├── dedup.py            # 跨來源去重
│   │   ├── filter.py           # 硬性過濾（預算、房型、黑名單）
│   │   ├── enricher.py         # OneMap.sg 路由計算
│   │   └── ranker.py           # 0-100 評分排名
│   ├── messenger/
│   │   └── email_sender.py     # Gmail SMTP 發送
│   ├── digest.py               # 主流程 orchestrator
│   └── templates_builder.py    # HTML email 生成
├── data/
│   ├── seen_listings.json      # 已發送的 URL（避免重複）
│   └── shortlist.json          # 手動收藏
├── templates/
│   └── daily_rental_digest.html  # HTML email 模板
├── .github/workflows/
│   └── twice-weekly-digest.yml   # Wed + Sun 8am SGT
├── requirements.txt
├── .env.example
└── run.py                      # CLI 入口
```

---

## 評分說明（滿分 100）

| 項目 | 分數 | 說明 |
|---|---|---|
| 通勤到 Funan | 30 | ≤5min = 滿分，每增加 5min 遞減 |
| 價格/坪效 | 25 | 低於 S$5.5/sqft = 滿分 |
| MRT 步行 | 15 | ≤5min 步行 = 滿分 |
| 通勤到 Raffles Place | 15 | 同 Funan 計算 |
| 上架新鮮度 | 10 | ≤3天 = 滿分 |
| 照片數量 | 5 | ≥15張 = 滿分 |

---

## 常見問題

**Q: Gmail OAuth 流程失敗？**
確認 `config/credentials.json` 存在，且 OAuth 同意畫面已加入自己為測試使用者。

**Q: OneMap API 回傳 401？**
Token 已過期（3天），重新執行會自動刷新。

**Q: Playwright 被 Cloudflare 擋？**
正常現象 — 系統會自動降級為只用 Gmail alerts。只要 Gmail alerts 有資料就不影響功能。

**Q: 收不到郵件？**
1. 確認 `GMAIL_APP_PASSWORD` 正確（需有 2FA）
2. 檢查垃圾信件夾
3. 執行 `python run.py --test` 看是否有 listings
