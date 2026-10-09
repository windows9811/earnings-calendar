# 美股財報行事曆（自動更新版）

每週一頁的美股重點財報行事曆：週一到週五 × 盤前／盤後，每家公司一張 logo 卡片，點了直接開公司官網。
資料**每天自動更新**，不需要人工維護。

- `/earnings` → 本週（週六、週日會自動顯示下一週）
- `/earnings/2026-10-19` → 指定週（網址就是該週週一的日期）
- 左右箭頭（或鍵盤 ← →）切換上一週／下一週，右上角可分享連結、下載整週圖片

## 運作方式

```
GitHub Actions（每天 2 次）
  └─ scripts/update.py   抓 Nasdaq 財報行事曆（今天往前 1 週～往後 6 週）
       ├─ 篩選：市值 ≥ 100 億美元、每週最多 135 家（config.json 可調）＋ 去除重複股別（GOOG/GOOGL）
       ├─ 公司官網：Nasdaq 公司資料 → Wikidata
       └─ 公司 logo：Wikidata 文字標誌 SVG → Parqet → FMP，下載到 public/logos/
  └─ scripts/build.py    產生靜態網頁到 dist/
  └─ 把新資料 commit 回 repo、發布到 GitHub Pages
```

不需要任何 API key、不需要安裝套件（只用 Python 內建功能）。

### 「時間未定」是什麼？
離財報日還很遠的公司，常常還沒公布是盤前還是盤後。程式會依序嘗試：
1. `data/overrides.json` 手動指定的時段
2. Nasdaq 已公布的時段
3. Finnhub（選用，需要免費 API key，見下方）
4. 這家公司上一季的時段（系統會自動記住）

都沒有的話就放在當天下方的「時間未定」列；等公司公布後，下一次自動更新就會移到正確欄位。

## 本機預覽

```bash
python3 scripts/update.py && python3 scripts/build.py && python3 -m http.server 8000 -d dist
```

然後打開 http://localhost:8000/earnings

## 部署到 GitHub Pages（免費）

1. 在 GitHub 建一個新 repo，把整個資料夾推上去（分支名稱 `main`）
2. repo → **Settings → Pages → Source** 選 **GitHub Actions**
3. 改 `config.json`：
   - `siteName`：網站名稱
   - `siteUrl`：網站網址，例如 `https://earnings.你的網域.com`（用於 Google 收錄的網址標記，沒填也能正常運作）
   - `homeLink`：左上角名稱點下去要連到哪（例如你的部落格首頁）
   - 網站內的連結都是相對路徑，放在任何網址底下都能正常顯示
4. 推上去之後 Actions 會自動跑；之後每天自動更新。也可以到 **Actions → Update earnings calendar → Run workflow** 手動更新。

想用自己的網域（例如 `earnings.你的網域.com`）：Settings → Pages → Custom domain，DNS 加一筆 CNAME 指向 `你的帳號.github.io`。

### 選用：Finnhub 補時段
到 https://finnhub.io 註冊免費帳號拿 API key，在 repo **Settings → Secrets and variables → Actions** 新增 `FINNHUB_API_KEY`。

## 手動修正（data/overrides.json）

自動抓的 logo 偶爾會不理想（舊版 logo、白色字看不到等），或想加減公司，改這個檔就好：

```json
{
  "NFLX": { "logo": "https://example.com/netflix.svg" },
  "SMMT": { "include": true },
  "BBDO": { "exclude": true },
  "AAPL": { "time": "amc", "website": "https://www.apple.com", "name": "Apple" }
}
```

| 欄位 | 用途 |
|---|---|
| `logo` | 換成指定圖片網址（會自動下載保存） |
| `website` | 點卡片要開的網址 |
| `name` | 顯示名稱 |
| `time` | `bmo` 盤前 / `amc` 盤後 |
| `include` | `true` = 市值不夠也收錄 |
| `exclude` | `true` = 不收錄 |

## 調整（config.json）

| 欄位 | 預設 | 說明 |
|---|---|---|
| `minMarketCap` | 10000000000 | 收錄門檻（美元），調低收錄更多公司 |
| `maxPerWeek` | 135 | 每週最多收錄幾家（財報旺季時依市值取前幾名，0 = 不限） |
| `weeksAhead` | 6 | 往後抓幾週 |
| `visiblePerColumn` | 10 | 每欄先顯示幾家，其餘收在「顯示更多」 |
| `showNextWeekOnWeekend` | true | 週末時 `/earnings` 直接顯示下一週 |

過去的週次資料會保留在 `data/weeks/`，所以往回翻的歷史頁面會一直存在。
