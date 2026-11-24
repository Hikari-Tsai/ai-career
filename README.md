# 台灣 AI 職缺雷達

每天自動到各公司**官方招募頁**抓取台灣的 AI / ML / LLM 職缺，計算關聯度後發布成靜態網頁（GitHub Pages）。

- 不使用 104、LinkedIn、CakeResume 等求職平台，只使用公司官網背後的招募系統（Workday、Greenhouse、Eightfold、Google / Amazon 自有 API 等）。
- 爬蟲會標明身分（`ai-career-bot` User-Agent），同一主機每秒最多 1 個請求，並遵守 `429 Retry-After`，不繞過任何反爬蟲機制。

## 架構

```
companies.yaml            公司清單：每家公司對應一個 adapter + 參數
scraper/
  sources/                各招募系統 adapter（workday, greenhouse, lever, ashby,
                          smartrecruiters, eightfold, google, amazon）
  run.py                  每日流程：抓取 → 台灣篩選 → 補職缺內文 → 關聯度 → data/jobs.json
  relevance.py            第一段：關鍵字規則評分（免費、可解釋）
  llm.py                  第二段：Claude 結構化分類（選用，有快取）
  probe.py                驗證單一公司設定
web/                      前端（純 HTML/CSS/JS，讀取 data/jobs.json）
data/
  jobs.json               AI 相關職缺（前端讀這個）
  llm_cache.json          LLM 分類結果，以職缺內容 hash 為 key（commit 進 repo）
  seen.json               每個職缺第一次出現的日期（用於「NEW」標記）
  history.json            每日統計
.cache/details.json.gz    職缺內文快取（放在 Actions cache，不進 git）
```

## 關聯度怎麼算

每個台灣職缺都會得到一個 0–100 的「關聯度」，網頁預設依這個分數排序（同分時新收錄的在前）。分數分兩段計算：

```
所有台灣職缺 ──► 第 1 段：關鍵字規則（每個都算）
                    │ 規則分數 ≥ 15
                    ▼
                 第 2 段：LLM 判斷（有設定 ANTHROPIC_API_KEY 時）
                    │
                    ▼
               最終分數與是否顯示
```

### 第 1 段：關鍵字規則（`scraper/relevance.py`）

免費、可解釋、每次結果都一樣。詞庫是一組「類別、權重、正規表示式」，例如：

| 類別 | 權重 | 例子 |
|---|---|---|
| LLM / 生成式 AI | 5 | LLM、large language model、大型語言模型 |
| LLM / 生成式 AI | 4 | generative AI、GenAI、生成式 AI |
| LLM / 生成式 AI | 2–3 | RAG、agentic、AI agent、foundation model、transformer、fine-tune、LoRA、vLLM |
| 機器學習 / AI 工程 | 5 | machine learning、deep learning、機器學習、深度學習 |
| 機器學習 / AI 工程 | 3–4 | ML、neural network、AI engineer、人工智慧、AI 演算法、AI 軟體／模型 |
| 機器學習 / AI 工程 | 2 | PyTorch、TensorFlow、JAX、模型訓練 |
| 電腦視覺 / NLP / 語音 | 2–4 | computer vision、電腦視覺、NLP、語音辨識、多模態、object detection |
| ML 系統 / 推論優化 | 3–4 | MLOps、model serving、distributed training、CUDA、TensorRT、Triton、量化 |
| AI 晶片 / 硬體 | 1.5–3 | NPU、AI accelerator、AI 晶片、TPU；AI server、AI 伺服器（1.5） |
| 資料科學 | 1.5–3 | data scientist、資料科學；單獨出現的「AI」只有 1.5 |

計算方式：

1. 每個詞條只算一次。出現在**職稱或部門**算「標題分」，否則在**職缺內文**出現才算「內文分」。
2. `分數 = 9 × 標題分 + 3 × min(內文分, 14)`，所以職稱的影響是內文的 3 倍，內文最多貢獻 42 分。
3. 扣分（只看職稱）：
   - 業務、行銷、BD、採購、人資、招募、會計、法務、客服、助理等 → × 0.35
   - 機構、散熱、layout、封裝、PCB、製造、品保等硬體職，且標題分 < 4 → × 0.5
4. 結果限制在 0–100。職缺類別取加權得分最高的類別。

例子（實際用程式算出）：

| 職缺 | 命中 | 規則分數 |
|---|---|---|
| Senior Machine Learning Engineer（內文提到 computer vision、PyTorch、TensorRT） | 標題 machine learning 5；內文 4 + 2 + 3 = 9 | 9×5 + 3×9 = **72** |
| AI Solutions Sales Manager（內文提到 generative AI、LLM） | 標題 AI 1.5；內文 4 + 5 = 9；業務扣分 | (13.5 + 27) × 0.35 = **14.2** |
| AI Server 機構工程師 | 標題 AI server 1.5 + AI 1.5；機構扣分 | 27 × 0.5 = **13.5** |
| Java Backend Engineer（內文提到 AI APIs） | 內文 AI 1.5 | **4.5** |

職缺內文只會替「看起來像技術職」的職稱抓取（每家公司每次有上限，Workday 每次 40 筆），其餘只用職稱評分；內文會快取，之後幾天陸續補齊。

### 第 2 段：LLM 判斷（`scraper/llm.py`）

規則分數 ≥ 15 的職缺會交給 Claude（預設 `claude-opus-5-5`，可用 `LLM_MODEL` 改成 Haiku 降低成本），依「這份工作每天實際在做什麼」打分，而不是看有沒有出現 AI 字眼：

| LLM 分數 | 意義 |
|---|---|
| 85–100 | 核心 AI 工作：訓練或微調模型、LLM / RAG / agent 系統、ML 研究、CV / NLP / 語音模型、推論／編譯器／kernel 優化 |
| 60–84 | 有實質 AI 成分：ML 平台 / MLOps、有建模的資料科學、AI 加速器架構、在模型上開發 AI 應用 |
| 30–59 | AI 相關：AI 伺服器／GPU 硬體設計、AI 產品或專案管理、串接 AI API 的軟體 |
| 0–29 | 不是 AI 工作，即使公司或產品跟 AI 有關（業務、採購、機構、製造、一般 IT） |

LLM 同時輸出類別（多一個「AI 相關應用 / 管理」與「not_ai」）、年資、最多 6 個技能，以及一句中文摘要，網頁上的摘要、技能標籤和年資篩選都來自這裡。

結果以「職稱 + 內文前 6000 字」的 hash 快取在 `data/llm_cache.json`：職缺內容沒變就不會再呼叫 API，每天只需要分析新增或修改過的職缺（每次最多 400 筆）。

### 最終分數與顯示條件

| 情況 | 最終分數 | 顯示條件 |
|---|---|---|
| 有 LLM 結果 | `0.75 × LLM 分數 + 0.25 × 規則分數` | LLM 分數 ≥ 40 且類別不是 not_ai |
| 沒有 LLM 結果（未設定金鑰，或還在排隊） | 規則分數 | 規則分數 ≥ 32 |

規則分數保留 25% 的權重，讓兩個 LLM 分數相近的職缺之間，職稱更明確的會排在前面。

**為什麼不用嵌入向量：** 嵌入向量衡量的是主題相似度。「AI Server 機構工程師」或「GenAI 伺服器 TPM」在向量空間裡跟 ML 工程師很接近，但其實是不同職涯。LLM 能分辨這種差異，也能抽出結構化欄位。每天新增的候選職缺不多，加上 hash 快取，成本很低。嵌入向量比較適合下一階段的「用履歷比對職缺」個人化排序。

## 本機執行

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m scraper.run                      # 全部公司
.venv/bin/python -m scraper.run --only nvidia,google # 指定公司
ANTHROPIC_API_KEY=... .venv/bin/python -m scraper.run  # 啟用 LLM 階段
python3 -m http.server -d . 8000   # 開 http://localhost:8000/web/
```

## 新增公司

1. 找到公司官網招募頁背後的系統（看頁面是否連到 `myworkdayjobs.com`、`boards.greenhouse.io`、`eightfold.ai`…）。
2. 驗證設定：
   ```bash
   .venv/bin/python -m scraper.probe workday '{"host":"xxx.wd1.myworkdayjobs.com","tenant":"xxx","site":"External"}'
   ```
3. 加到 `companies.yaml`。如果是新的招募系統，在 `scraper/sources/` 寫一個 `fetch(company)` generator 並註冊到 `sources/__init__.py`。

## 收錄範圍

`companies.yaml` 目前自動爬取 75 個招募來源，另有 34 家以官網直達卡片顯示（2026-11-23 驗證）：

| 類型 | 公司 |
|---|---|
| 台灣半導體 | 聯發科、聯電、瑞昱、新唐、華邦電、創意、世界先進 |
| 台灣電子／系統／電信 | 鴻海、華碩（含機器人與 AI 中心 ARAIC 官網；同一職缺以 ARAIC 為主）、緯創、宏碁、研華、群暉、台灣大哥大、中華電信、遠傳 |
| 台灣軟體／網路／金融 | 趨勢、Appier、KKCompany、SHOPLINE、Pinkoi、Gogolook、Dcard、玉山銀行 |
| 國際科技大廠 | Google、Microsoft、Amazon、Apple、NVIDIA、Intel、AMD、Qualcomm、Micron、Broadcom、Marvell、Synopsys、Cadence、Arm、AMAT、ASML、Lam、KLA、IBM、Cisco、Dell、TI、HP、HPE、Supermicro、Logitech、NXP、Infineon、ADI、Keysight、Teradyne、Netflix |
| 日美星外商在台 | LINE、Shopee、Renesas、Bosch、Zebra、Binance、Ubiquiti、Canonical、OKX、Agoda、Stripe、Crypto.com、Ericsson、Sony、TEL、foodpanda |
| 台灣新創（Yourator） | 以 AI、機器學習、LLM 等關鍵字搜尋 Yourator（robots.txt 允許、公開 API），已由官網爬取的公司會跳過，職缺標示「來源：Yourator」 |

**無法收錄的公司**（不使用求職平台、不繞過反爬蟲機制）：其中的大廠以 `link_only: true` 登記在 `companies.yaml`，網頁上會以「請至官網查看」卡片顯示，直接連到官方招募頁。

- **求職平台**：Yourator 允許爬取，已作為新創來源；104、1111、CakeResume 會擋爬蟲（403 或 CAPTCHA），所以不使用。
- **官網只導向 104 等求職平台**：聯詠、奇景、群聯、世芯、晶心、南亞科、旺宏、台達電、和碩、光寶、Gogoro、工研院、91APP、奧義智慧、iKala、遊戲橘子、昕力、精誠、叡揚、東森購物、將來銀行、訊連、台灣人工智慧實驗室
- **官網有 Cloudflare 或其他機制阻擋自動化存取**：台積電、日月光、力旺、英業達、技嘉、微星、義隆、Uber、Nokia、MaiCoin
- **只有私有 API，或找不到可用的端點**：Meta（只有私有 GraphQL）、廣達（職缺頁 iframe 目前 404）、國泰（iMatch API 格式不明）、Western Digital、緯穎（只有招募信箱）
- **之後可以補的**（端點已找到，還沒寫 adapter）：仁寶、西門子（Avature）、力積電、慧榮
- **暫時抓不到**：Workday 系統的公司（NVIDIA、Intel、Micron 等）在 GitHub Actions 上常被限流，聯電擋雲端 IP。抓取失敗且沒有舊資料可沿用時，網頁會自動改顯示成官網直達卡片。

## GitHub Actions 設定

`.github/workflows/update.yml` 每天台灣時間 06:00 執行：抓取 → commit `data/` → 部署 Pages。

- Settings → Pages → Source 選 **GitHub Actions**
- （選用）Settings → Secrets → `ANTHROPIC_API_KEY`：啟用 LLM 關聯度
- （選用）Settings → Variables → `LLM_MODEL`：預設 `claude-opus-5-5`，可改成 `claude-haiku-5-5` 降低成本
- 某家公司官網抓取失敗時，會沿用前一天的資料，不會讓整個流程失敗；狀態會顯示在網頁底部的「資料來源與抓取狀態」。
