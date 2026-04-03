"""
main.py — Stock Data Intelligence Dashboard — FastAPI Backend
============================================================
Author : Abhay Thakur
Stack  : FastAPI · SQLAlchemy · SQLite · yfinance · Pandas · NumPy

Endpoints
---------
GET /                      → Redirect to dashboard UI
GET /companies             → All available companies
GET /data/{symbol}         → Last N days of OHLCV + metrics
GET /summary/{symbol}      → 52-week stats + volatility + momentum
GET /compare               → Side-by-side comparison of two symbols
GET /gainers-losers        → Today's top gainers and losers
GET /correlation           → Pearson correlation matrix (custom metric)
POST /refresh              → Re-fetch all data from yfinance
GET /health                → Health check
GET /frontend              → Serve dashboard HTML
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from data_fetcher import (
    COMPANIES,
    fetch_all,
    get_52_week_stats,
    get_volatility_and_momentum,
)
from database import Company, StockPrice, get_db, init_db
from mock_seeder import seed_mock

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)

# ── Startup / Shutdown ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initialise DB and seed data on first launch.

    Seeding strategy (with automatic fallback):
      1. Try yfinance — real NSE data from Yahoo Finance.
      2. If yfinance fails (e.g. network restricted on Render free tier),
         automatically fall back to mock_seeder which generates realistic
         GBM-simulated price data so the app is always fully functional.
    """
    logger.info("Initialising database...")
    init_db()

    db = next(get_db())
    company_count = db.query(Company).count()
    db.close()

    if company_count == 0:
        logger.info("No data found — attempting yfinance seed...")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _seed_data)
        logger.info("Seed complete.")
    else:
        logger.info("Database already populated — skipping seed.")

    yield


def _seed_data():
    """
    Try real yfinance data first.
    If it fails for any reason (network blocked, rate limit, etc.),
    fall back to synthetic mock data so the server always starts healthy.
    """
    db = next(get_db())
    try:
        logger.info("Fetching real NSE data via yfinance...")
        results = fetch_all(db)
        success = [r for r in results if "error" not in r]
        failed  = [r for r in results if "error" in r]

        if success:
            for r in results:
                logger.info(r)
            logger.info(f"yfinance seed complete — {len(success)} symbols OK, {len(failed)} failed.")
        else:
            raise RuntimeError("All yfinance fetches failed — switching to mock data.")

    except Exception as exc:
        logger.warning(f"yfinance unavailable ({exc}). Falling back to mock data...")
        try:
            seed_mock(db)
            logger.info("Mock data seeded successfully. Dashboard fully operational.")
        except Exception as mock_exc:
            logger.error(f"Mock seeder also failed: {mock_exc}")
    finally:
        db.close()


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Stock Data Intelligence Dashboard",
    description=(
        "A mini financial data platform built with FastAPI + yfinance. "
        "Tracks NSE-listed companies with real-time metrics, 52-week stats, "
        "volatility scores, and a custom Momentum Score."
    ),
    version="1.0.0",
    contact={"name": "Abhay Thakur", "url": "https://github.com/Abhxay"},
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_PATH = os.path.join(os.path.dirname(__file__), "frontend", "index.html")


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def _row_to_dict(row: StockPrice) -> dict:
    return {
        "date":          str(row.date),
        "open":          row.open,
        "high":          row.high,
        "low":           row.low,
        "close":         row.close,
        "volume":        int(row.volume) if row.volume else 0,
        "daily_return":  round(row.daily_return * 100, 4) if row.daily_return else 0.0,
        "ma_7":          row.ma_7,
    }


def _symbol_or_404(symbol: str, db: Session) -> Company:
    symbol = symbol.upper()
    if not symbol.endswith(".NS"):
        symbol += ".NS"
    company = db.query(Company).filter(Company.symbol == symbol).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found. Call /companies for valid symbols.")
    return company


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/frontend")


@app.get("/health", tags=["System"])
def health():
    """Quick health check."""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/frontend", include_in_schema=False)
def serve_frontend():
    """Serve the HTML dashboard."""
    if os.path.exists(FRONTEND_PATH):
        return FileResponse(FRONTEND_PATH, media_type="text/html")
    return HTMLResponse("<h2>Frontend not found. Run the server from the project root.</h2>")


# ── /companies ────────────────────────────────────────────────────────────────

@app.get("/companies", tags=["Data"])
def get_companies(db: Session = Depends(get_db)):
    """
    Returns a list of all tracked NSE companies with metadata.
    """
    companies = db.query(Company).all()
    if not companies:
        raise HTTPException(status_code=503, detail="Data not yet seeded. Retry in a moment.")
    return [
        {
            "symbol":        c.symbol,
            "name":          c.name,
            "sector":        c.sector,
            "market_cap_cr": c.market_cap_cr,
            "description":   c.description,
        }
        for c in companies
    ]


# ── /data/{symbol} ────────────────────────────────────────────────────────────

@app.get("/data/{symbol}", tags=["Data"])
def get_stock_data(
    symbol: str,
    days: int = Query(default=30, ge=1, le=365, description="Number of trading days to return"),
    db: Session = Depends(get_db),
):
    """
    Returns last `days` days of OHLCV data + daily_return and 7-day MA for a symbol.

    - **symbol**: e.g. `TCS`, `INFY`, `RELIANCE`
    - **days**: 1–365 (default 30)
    """
    company = _symbol_or_404(symbol, db)
    cutoff  = datetime.today().date() - timedelta(days=days)
    rows    = (
        db.query(StockPrice)
        .filter(StockPrice.symbol == company.symbol, StockPrice.date >= cutoff)
        .order_by(StockPrice.date)
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No price data found for {company.symbol}")

    return {
        "symbol":  company.symbol,
        "name":    company.name,
        "days":    days,
        "count":   len(rows),
        "data":    [_row_to_dict(r) for r in rows],
    }


# ── /summary/{symbol} ────────────────────────────────────────────────────────

@app.get("/summary/{symbol}", tags=["Analytics"])
def get_summary(symbol: str, db: Session = Depends(get_db)):
    """
    Returns 52-week high/low/avg + Volatility Score + Momentum Score for a symbol.

    **Volatility Score** (0–100): Annualised 30-day rolling standard deviation of
    daily returns, scaled to 0–100. Higher = more volatile.

    **Momentum Score** (−100 to +100): Composite of price strength vs 30-day MA
    and recent volume acceleration. Positive = bullish momentum.
    """
    company      = _symbol_or_404(symbol, db)
    stats_52w    = get_52_week_stats(db, company.symbol)
    live_metrics = get_volatility_and_momentum(db, company.symbol)

    if not stats_52w:
        raise HTTPException(status_code=404, detail="Insufficient data for 52-week summary.")

    latest = (
        db.query(StockPrice)
        .filter(StockPrice.symbol == company.symbol)
        .order_by(StockPrice.date.desc())
        .first()
    )

    return {
        "symbol":               company.symbol,
        "name":                 company.name,
        "sector":               company.sector,
        "latest_close":         latest.close if latest else None,
        "latest_date":          str(latest.date) if latest else None,
        **stats_52w,
        **live_metrics,
    }


# ── /compare ─────────────────────────────────────────────────────────────────

@app.get("/compare", tags=["Analytics"])
def compare_stocks(
    symbol1: str = Query(..., description="First symbol, e.g. TCS"),
    symbol2: str = Query(..., description="Second symbol, e.g. INFY"),
    days: int    = Query(default=90, ge=7, le=365),
    db: Session  = Depends(get_db),
):
    """
    Compare two stocks' normalised price performance over the last `days` days.

    Returns daily close prices for both symbols indexed to 100 at the start of
    the window — so you can plot a fair apples-to-apples comparison.
    Also returns Pearson correlation of their daily returns.
    """
    def _get_series(sym: str) -> dict:
        c      = _symbol_or_404(sym, db)
        cutoff = datetime.today().date() - timedelta(days=days)
        rows   = (
            db.query(StockPrice)
            .filter(StockPrice.symbol == c.symbol, StockPrice.date >= cutoff)
            .order_by(StockPrice.date)
            .all()
        )
        return c, rows

    c1, rows1 = _get_series(symbol1)
    c2, rows2 = _get_series(symbol2)

    if not rows1 or not rows2:
        raise HTTPException(status_code=404, detail="Insufficient data for one or both symbols.")

    def normalise(rows):
        closes = [r.close for r in rows]
        base   = closes[0] or 1
        return [round(c / base * 100, 4) for c in closes]

    dates1    = [str(r.date) for r in rows1]
    dates2    = [str(r.date) for r in rows2]
    norm1     = normalise(rows1)
    norm2     = normalise(rows2)

    # Pearson correlation on daily returns (inner-join on dates)
    ret_map1  = {str(r.date): r.daily_return for r in rows1}
    ret_map2  = {str(r.date): r.daily_return for r in rows2}
    common    = sorted(set(ret_map1) & set(ret_map2))
    corr      = None
    if len(common) >= 10:
        arr1 = np.array([ret_map1[d] for d in common if ret_map1[d] is not None])
        arr2 = np.array([ret_map2[d] for d in common if ret_map2[d] is not None])
        if len(arr1) == len(arr2) and len(arr1) > 1:
            corr = round(float(np.corrcoef(arr1, arr2)[0, 1]), 4)

    return {
        "days": days,
        "correlation_daily_returns": corr,
        "symbol1": {
            "symbol": c1.symbol, "name": c1.name,
            "dates": dates1, "normalised_price": norm1,
            "final_return_pct": round(norm1[-1] - 100, 2) if norm1 else None,
        },
        "symbol2": {
            "symbol": c2.symbol, "name": c2.name,
            "dates": dates2, "normalised_price": norm2,
            "final_return_pct": round(norm2[-1] - 100, 2) if norm2 else None,
        },
    }


# ── /gainers-losers ───────────────────────────────────────────────────────────

@app.get("/gainers-losers", tags=["Analytics"])
def gainers_losers(db: Session = Depends(get_db)):
    """
    Returns the top 3 gainers and top 3 losers based on yesterday's daily return.
    """
    # Find latest date in DB
    latest_entry = db.query(StockPrice).order_by(StockPrice.date.desc()).first()
    if not latest_entry:
        raise HTTPException(status_code=503, detail="No data available yet.")

    latest_date = latest_entry.date
    rows = db.query(StockPrice).filter(StockPrice.date == latest_date).all()

    if not rows:
        raise HTTPException(status_code=404, detail="No data for latest date.")

    sorted_rows = sorted(rows, key=lambda r: r.daily_return or 0, reverse=True)

    def _enrich(r: StockPrice):
        company = db.query(Company).filter(Company.symbol == r.symbol).first()
        return {
            "symbol":         r.symbol,
            "name":           company.name if company else r.symbol,
            "close":          r.close,
            "daily_return_pct": round((r.daily_return or 0) * 100, 2),
        }

    return {
        "date":    str(latest_date),
        "gainers": [_enrich(r) for r in sorted_rows[:3]],
        "losers":  [_enrich(r) for r in sorted_rows[-3:][::-1]],
    }


# ── /correlation ─────────────────────────────────────────────────────────────

@app.get("/correlation", tags=["Analytics"])
def correlation_matrix(
    days: int = Query(default=90, ge=30, le=365),
    db: Session = Depends(get_db),
):
    """
    Returns the Pearson correlation matrix of daily returns for all tracked stocks.

    This is a **custom analytical metric** — useful for portfolio diversification:
    stocks with low/negative correlation are better diversifiers.
    """
    cutoff = datetime.today().date() - timedelta(days=days)
    symbols = [c.symbol for c in db.query(Company).all()]

    series = {}
    for sym in symbols:
        rows = (
            db.query(StockPrice)
            .filter(StockPrice.symbol == sym, StockPrice.date >= cutoff)
            .order_by(StockPrice.date)
            .all()
        )
        series[sym] = {str(r.date): r.daily_return for r in rows if r.daily_return is not None}

    # Align on common dates
    all_dates = sorted(set.intersection(*[set(v.keys()) for v in series.values()]))
    if len(all_dates) < 10:
        raise HTTPException(status_code=422, detail="Insufficient overlapping data for correlation.")

    df = pd.DataFrame(
        {sym: [series[sym].get(d) for d in all_dates] for sym in symbols},
        index=all_dates,
    ).dropna()

    corr_df = df.corr().round(4)
    labels  = [sym.replace(".NS", "") for sym in symbols]

    return {
        "days":    days,
        "symbols": labels,
        "matrix":  corr_df.values.tolist(),
    }


# ── /refresh ─────────────────────────────────────────────────────────────────

@app.post("/refresh", tags=["System"])
async def refresh_data():
    """
    Re-fetches all stock data from yfinance and upserts into the database.
    Falls back to mock data automatically if yfinance is unavailable.
    Runs in background to avoid timeout.
    """
    loop = asyncio.get_event_loop()
    asyncio.create_task(loop.run_in_executor(None, _seed_data))
    return {"message": "Refresh started in background (yfinance → mock fallback). Check /companies in ~30s."}
