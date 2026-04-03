# 📊 StockSense — NSE Stock Data Intelligence Dashboard

A full-stack financial data platform built for the **Jarnox Internship Assignment**.  
Tracks 10 NSE-listed companies with real-time data, custom analytics, REST APIs, and a live dashboard.

---

## 🎯 What This Does

| Capability | Details |
|---|---|
| **Data Source** | yfinance (real NSE data via Yahoo Finance) with automatic mock fallback |
| **Storage** | SQLite via SQLAlchemy ORM |
| **Backend** | FastAPI with auto-generated Swagger UI |
| **Frontend** | Custom dark SaaS dashboard (Chart.js) |
| **Deployment** | Docker + docker-compose + Render-ready |

---

## 🚀 Quick Start

### Option 1 — Run Locally (Python)

```bash
# 1. Clone
git clone https://github.com/Abhxay/stocksense
cd stocksense

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the server
uvicorn main:app --reload --port 8000
```

Visit **http://localhost:8000** → auto-redirects to the dashboard.  
Visit **http://localhost:8000/docs** → Swagger UI with all endpoints.

> ⏳ On first boot the app tries to fetch ~2 years of real NSE data via yfinance (~30s).  
> If yfinance is unavailable (restricted network etc.), it **automatically falls back to realistic mock data** — the app is always fully functional.

---

### Option 2 — Docker

```bash
docker-compose up --build
```

App available at **http://localhost:8000**.

---

### Option 3 — Deploy on Render (free)

1. Push this repo to GitHub
2. Go to [render.com](https://render.com) → **New Web Service** → connect your repo
3. Set:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Deploy — Render's free tier blocks outbound internet, so yfinance will fail gracefully and the app **automatically seeds mock data instead**. The dashboard loads perfectly.

---

## 📡 API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/companies` | GET | All 10 tracked NSE companies |
| `/data/{symbol}` | GET | Last N days OHLCV + metrics (`?days=30`) |
| `/summary/{symbol}` | GET | 52-week stats + Volatility + Momentum Score |
| `/compare` | GET | Normalised performance comparison of 2 stocks |
| `/gainers-losers` | GET | Top 3 gainers and losers (latest trading day) |
| `/correlation` | GET | Pearson correlation matrix of all 10 stocks |
| `/refresh` | POST | Re-fetch all data (yfinance → mock fallback) |
| `/health` | GET | Health check |
| `/docs` | GET | Swagger interactive docs |

**Example calls:**
```bash
curl http://localhost:8000/companies
curl http://localhost:8000/data/TCS?days=90
curl http://localhost:8000/summary/INFY
curl "http://localhost:8000/compare?symbol1=TCS&symbol2=INFY&days=90"
curl http://localhost:8000/gainers-losers
curl http://localhost:8000/correlation?days=90
```

---

## 📐 Data Metrics

### Required Metrics
| Metric | Formula |
|---|---|
| Daily Return | `(CLOSE - OPEN) / OPEN` |
| 7-day Moving Average | `rolling(7).mean()` on close |
| 52-week High/Low | `max/min(close)` over trailing 52 weeks |

### ✦ Custom Metrics (Original)

#### Volatility Score (0–100)
```
annualised_vol   = rolling_30d_std(daily_return) × √252
volatility_score = clip((annualised_vol - 0.10) / 0.70 × 100, 0, 100)
```
Maps typical Indian equity volatility (10%–80% annualised) onto a 0–100 scale.  
Higher = more volatile stock.

#### Momentum Score (−100 to +100)
```
price_strength = (close - MA_30) / MA_30 × 100
volume_trend   = MA_5_volume / MA_20_volume   [clipped 0.5–2.0]
momentum_raw   = price_strength × volume_trend
momentum_score = clip(momentum_raw / rolling_max_abs, −100, +100)
```
A **composite bullish/bearish indicator** combining price deviation from trend with volume acceleration.  
Positive = bullish momentum; Negative = bearish pressure.

---

## 🏗️ Architecture

```
stock_dashboard/
├── main.py             # FastAPI app — all route handlers + startup logic
├── data_fetcher.py     # yfinance fetch, Pandas cleaning, metric computation
├── database.py         # SQLAlchemy models + session factory
├── mock_seeder.py      # GBM-based synthetic data generator (Render fallback)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── README.md
└── frontend/
    └── index.html      # Single-page dashboard (Chart.js, no build step)
```

### Startup Seeding Strategy
```
App boots
  └─► DB empty?
        ├─► YES → try yfinance (real NSE data)
        │           ├─► SUCCESS → store in SQLite ✅
        │           └─► FAIL   → seed_mock() → GBM synthetic data ✅
        └─► NO  → skip (already populated)
```

---

## 🏢 Tracked Companies

| Symbol | Company | Sector |
|---|---|---|
| RELIANCE.NS | Reliance Industries | Energy |
| TCS.NS | Tata Consultancy Services | IT |
| INFY.NS | Infosys | IT |
| HDFCBANK.NS | HDFC Bank | Banking |
| WIPRO.NS | Wipro | IT |
| ICICIBANK.NS | ICICI Bank | Banking |
| HINDUNILVR.NS | Hindustan Unilever | FMCG |
| BHARTIARTL.NS | Bharti Airtel | Telecom |
| SBIN.NS | State Bank of India | Banking |
| LT.NS | Larsen & Toubro | Infrastructure |

---

## 🎨 Dashboard Features

- **Live price chart** with 7-day MA overlay
- **Volume bars** (green = positive day, red = negative)
- **Period switcher** — 30D / 90D / 6M / 1Y
- **Metric cards** — 52W High/Low, Volatility Score, Momentum Score
- **Top Gainers & Losers** panel
- **Comparison tool** — normalise two stocks to base-100 and overlay
- **Pearson correlation** shown live in comparison panel

---

## 🧰 Tech Stack

- **Python 3.12**
- **FastAPI 0.111** — async REST, auto Swagger docs
- **SQLAlchemy 2.0** — ORM with SQLite (swap `DATABASE_URL` for Postgres in prod)
- **yfinance 0.2** — real NSE data via Yahoo Finance
- **Pandas + NumPy + SciPy** — data cleaning, transformations, metrics
- **Chart.js 4.4** — frontend charting (no build step)
- **Docker + docker-compose** — containerisation

---

## 💡 Design Decisions

1. **yfinance → mock fallback** — server always boots healthy regardless of network restrictions.
2. **SQLite over PostgreSQL** — zero-config for local dev; swap `DATABASE_URL` for Postgres in production.
3. **Upsert pattern** — re-running `/refresh` never creates duplicates.
4. **Async seed** — data fetching runs in a thread pool on startup so the server stays responsive.
5. **Base-100 normalisation** in `/compare` — lets you compare stocks at very different price levels fairly.
6. **GBM mock data** — Geometric Brownian Motion with sector-calibrated volatility and per-company seed prices, so the synthetic data looks realistic.

---

*Built by Abhay Thakur · [github.com/Abhxay](https://github.com/Abhxay)*
