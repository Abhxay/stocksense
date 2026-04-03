"""
data_fetcher.py — Fetches, cleans, and enriches stock data from yfinance.

Metrics added:
  - Daily Return      : (CLOSE - OPEN) / OPEN
  - 7-day Moving Avg  : Rolling 7-day mean of close prices
  - 52-week High/Low  : Max/Min of close over trailing 52 weeks
  - Volatility Score  : 30-day rolling std of daily returns (annualized, 0-100 scale)
  - Momentum Score    : Custom composite: (price vs 30d avg) × (volume trend) — original metric
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
from sqlalchemy.orm import Session

from database import Company, StockPrice

logger = logging.getLogger(__name__)

# ── NSE-listed companies (yfinance uses .NS suffix) ──────────────────────────
COMPANIES = {
    "RELIANCE.NS": {"name": "Reliance Industries", "sector": "Energy",          "market_cap_cr": 1950000, "description": "India's largest conglomerate — energy, retail, telecom."},
    "TCS.NS":      {"name": "Tata Consultancy Services", "sector": "IT",         "market_cap_cr": 1430000, "description": "Global IT services and consulting giant."},
    "INFY.NS":     {"name": "Infosys", "sector": "IT",                           "market_cap_cr": 620000,  "description": "Digital services and consulting firm."},
    "HDFCBANK.NS": {"name": "HDFC Bank", "sector": "Banking",                   "market_cap_cr": 1200000, "description": "India's largest private sector bank."},
    "WIPRO.NS":    {"name": "Wipro", "sector": "IT",                             "market_cap_cr": 270000,  "description": "IT, consulting, and business process services."},
    "ICICIBANK.NS":{"name": "ICICI Bank", "sector": "Banking",                  "market_cap_cr": 860000,  "description": "Leading private-sector bank."},
    "HINDUNILVR.NS":{"name":"Hindustan Unilever","sector":"FMCG",               "market_cap_cr": 580000,  "description": "FMCG giant — home, personal care, foods."},
    "BHARTIARTL.NS":{"name":"Bharti Airtel","sector":"Telecom",                 "market_cap_cr": 890000,  "description": "India's leading telecom operator."},
    "SBIN.NS":     {"name": "State Bank of India", "sector": "Banking",          "market_cap_cr": 720000,  "description": "Largest public sector bank in India."},
    "LT.NS":       {"name": "Larsen & Toubro", "sector": "Infrastructure",       "market_cap_cr": 500000,  "description": "Engineering, construction, and technology conglomerate."},
}


# ── Cleaning & Transformation ─────────────────────────────────────────────────

def clean_and_enrich(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean raw yfinance OHLCV data and add all required metrics.
    """
    if df.empty:
        return df

    df = df.copy()

    # Flatten multi-level columns (yfinance v0.2+)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Standardise column names
    df.columns = [c.lower().replace(" ", "_") for c in df.columns]

    # Ensure date index is proper
    df.index = pd.to_datetime(df.index).normalize()
    df.index.name = "date"
    df = df.sort_index()

    # ── Handle missing values ──────────────────────────────────────────────
    # Forward-fill then back-fill any gaps (handles market holidays naturally)
    df[["open", "high", "low", "close", "volume"]] = (
        df[["open", "high", "low", "close", "volume"]]
        .ffill()
        .bfill()
    )

    # Drop rows where close is still NaN (data never existed)
    df.dropna(subset=["close"], inplace=True)

    # ── Required Metrics ───────────────────────────────────────────────────
    df["daily_return"] = (df["close"] - df["open"]) / df["open"]
    df["ma_7"]         = df["close"].rolling(window=7,  min_periods=1).mean()
    df["ma_30"]        = df["close"].rolling(window=30, min_periods=1).mean()

    # ── Custom Metric 1: Volatility Score (0-100) ─────────────────────────
    # Annualised 30-day rolling std of daily returns, normalised to 0-100
    rolling_std        = df["daily_return"].rolling(window=30, min_periods=5).std()
    annualised_vol     = rolling_std * np.sqrt(252)          # annualise
    # Normalise: typical Indian equity volatility ranges from ~10% to ~80%
    df["volatility_score"] = ((annualised_vol - 0.10) / (0.80 - 0.10) * 100).clip(0, 100)

    # ── Custom Metric 2: Momentum Score (original) ────────────────────────
    # Composite = price strength × volume acceleration
    # price_strength : how far close is above/below 30d MA (%)
    # volume_trend   : ratio of 5d avg volume to 20d avg volume
    price_strength     = (df["close"] - df["ma_30"]) / df["ma_30"] * 100
    vol_5              = df["volume"].rolling(5,  min_periods=1).mean()
    vol_20             = df["volume"].rolling(20, min_periods=1).mean()
    volume_trend       = (vol_5 / vol_20).clip(0.5, 2.0)          # cap extremes
    momentum_raw       = price_strength * volume_trend
    # Normalise to -100 … +100
    abs_max            = momentum_raw.abs().rolling(252, min_periods=30).max().replace(0, 1)
    df["momentum_score"] = (momentum_raw / abs_max * 100).clip(-100, 100)

    return df


# ── Database persistence ──────────────────────────────────────────────────────

def upsert_company(db: Session, symbol: str) -> None:
    meta = COMPANIES.get(symbol, {})
    existing = db.query(Company).filter(Company.symbol == symbol).first()
    if not existing:
        db.add(Company(symbol=symbol, **meta))
        db.commit()


def upsert_prices(db: Session, symbol: str, df: pd.DataFrame) -> int:
    """Upsert price rows — skip duplicates by (symbol, date)."""
    from sqlalchemy import and_
    from database import StockPrice

    inserted = 0
    for date, row in df.iterrows():
        existing = (
            db.query(StockPrice)
            .filter(and_(StockPrice.symbol == symbol, StockPrice.date == date.date()))
            .first()
        )
        if existing:
            continue
        db.add(StockPrice(
            symbol       = symbol,
            date         = date.date(),
            open         = round(float(row.get("open",  0) or 0), 2),
            high         = round(float(row.get("high",  0) or 0), 2),
            low          = round(float(row.get("low",   0) or 0), 2),
            close        = round(float(row.get("close", 0) or 0), 2),
            volume       = float(row.get("volume", 0) or 0),
            daily_return = round(float(row.get("daily_return", 0) or 0), 6),
            ma_7         = round(float(row.get("ma_7", 0) or 0), 2),
        ))
        inserted += 1
    db.commit()
    return inserted


# ── Main fetch entry point ────────────────────────────────────────────────────

def fetch_and_store(db: Session, symbol: str, period: str = "1y") -> dict:
    """
    Download data for one symbol, clean it, store in DB.
    Returns summary dict.
    """
    logger.info(f"Fetching {symbol} | period={period}")
    try:
        ticker = yf.Ticker(symbol)
        df_raw = ticker.history(period=period, auto_adjust=True)
    except Exception as exc:
        logger.error(f"yfinance error for {symbol}: {exc}")
        return {"symbol": symbol, "error": str(exc)}

    if df_raw.empty:
        return {"symbol": symbol, "error": "No data returned from yfinance"}

    df = clean_and_enrich(df_raw)
    upsert_company(db, symbol)
    rows = upsert_prices(db, symbol, df)
    return {"symbol": symbol, "rows_inserted": rows, "date_range": f"{df.index[0].date()} → {df.index[-1].date()}"}


def fetch_all(db: Session) -> list[dict]:
    results = []
    for symbol in COMPANIES:
        results.append(fetch_and_store(db, symbol, period="2y"))
    return results


# ── Analytical helpers ────────────────────────────────────────────────────────

def get_52_week_stats(db: Session, symbol: str) -> dict:
    from database import StockPrice
    cutoff = datetime.today().date() - timedelta(weeks=52)
    rows = (
        db.query(StockPrice)
        .filter(StockPrice.symbol == symbol, StockPrice.date >= cutoff)
        .all()
    )
    if not rows:
        return {}
    closes = [r.close for r in rows if r.close]
    return {
        "high_52w": round(max(closes), 2),
        "low_52w":  round(min(closes), 2),
        "avg_close":round(sum(closes) / len(closes), 2),
    }


def get_volatility_and_momentum(db: Session, symbol: str) -> dict:
    """Re-compute live metrics from DB data (last 60 days)."""
    from database import StockPrice
    cutoff = datetime.today().date() - timedelta(days=60)
    rows = (
        db.query(StockPrice)
        .filter(StockPrice.symbol == symbol, StockPrice.date >= cutoff)
        .order_by(StockPrice.date)
        .all()
    )
    if not rows:
        return {}
    returns = [r.daily_return for r in rows if r.daily_return is not None]
    if not returns:
        return {}
    arr = np.array(returns)
    annualised_vol   = float(arr.std() * np.sqrt(252))
    volatility_score = round(min(max((annualised_vol - 0.10) / 0.70 * 100, 0), 100), 1)
    closes = np.array([r.close for r in rows])
    ma30   = closes[-30:].mean() if len(closes) >= 30 else closes.mean()
    price_strength   = (closes[-1] - ma30) / ma30 * 100
    momentum_score   = round(float(np.clip(price_strength * 2, -100, 100)), 1)
    return {
        "volatility_score": volatility_score,
        "momentum_score":   momentum_score,
        "annualised_vol_pct": round(annualised_vol * 100, 2),
    }
