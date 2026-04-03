"""
mock_seeder.py — Generates realistic mock NSE stock data for demo / offline mode.

Run this if yfinance cannot reach Yahoo Finance (restricted network).
Generates 400+ trading days of synthetic OHLCV data per company,
with realistic random-walk prices, sector-appropriate volatility,
and all required + custom metrics pre-computed.
"""

import random
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from database import Company, StockPrice, get_db, init_db
from data_fetcher import COMPANIES, clean_and_enrich

# ── Seed prices (realistic INR) ──────────────────────────────────────────────
SEED_PRICES = {
    "RELIANCE.NS":  2950.0,
    "TCS.NS":       3800.0,
    "INFY.NS":      1480.0,
    "HDFCBANK.NS":  1620.0,
    "WIPRO.NS":      490.0,
    "ICICIBANK.NS": 1150.0,
    "HINDUNILVR.NS":2400.0,
    "BHARTIARTL.NS":1750.0,
    "SBIN.NS":       840.0,
    "LT.NS":        3600.0,
}

# ── Sector volatility calibration ────────────────────────────────────────────
SECTOR_VOL = {
    "RELIANCE.NS":  0.015,
    "TCS.NS":       0.013,
    "INFY.NS":      0.016,
    "HDFCBANK.NS":  0.012,
    "WIPRO.NS":     0.018,
    "ICICIBANK.NS": 0.014,
    "HINDUNILVR.NS":0.011,
    "BHARTIARTL.NS":0.017,
    "SBIN.NS":      0.019,
    "LT.NS":        0.016,
}


def _trading_days(n_days: int = 500) -> list[date]:
    """Generate last n_days of trading days (Mon–Fri)."""
    days = []
    d = date.today()
    while len(days) < n_days:
        if d.weekday() < 5:   # Mon=0 … Fri=4
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def generate_ohlcv(symbol: str, trading_days: list[date]) -> pd.DataFrame:
    """
    Geometric Brownian Motion–style price simulation.
    Drift: slight upward bias (Indian bull market).
    """
    rng   = np.random.default_rng(seed=abs(hash(symbol)) % (2**32))
    price = SEED_PRICES.get(symbol, 1000.0)
    sigma = SECTOR_VOL.get(symbol, 0.015)
    mu    = 0.0003   # small positive drift per day

    rows = []
    for d in trading_days:
        # GBM step
        ret   = mu + sigma * rng.standard_normal()
        open_ = round(price, 2)
        close = round(price * math.exp(ret), 2)

        # intraday high/low around open-close range
        lo = min(open_, close) * (1 - abs(rng.standard_normal()) * 0.003)
        hi = max(open_, close) * (1 + abs(rng.standard_normal()) * 0.003)

        vol = int(rng.integers(500_000, 8_000_000))
        rows.append({"date": d, "open": open_, "high": round(hi, 2), "low": round(lo, 2), "close": close, "volume": vol})
        price = close   # next day opens at today's close

    df = pd.DataFrame(rows).set_index("date")
    df.index = pd.to_datetime(df.index)
    return df


def seed_mock(db: Session) -> list[dict]:
    """Seed all companies with synthetic data into the DB."""
    init_db()
    trading_days = _trading_days(500)
    results = []

    for symbol, meta in COMPANIES.items():
        # Upsert company
        existing = db.query(Company).filter(Company.symbol == symbol).first()
        if not existing:
            db.add(Company(symbol=symbol, **meta))
            db.commit()

        # Check existing rows
        existing_count = db.query(StockPrice).filter(StockPrice.symbol == symbol).count()
        if existing_count >= 400:
            results.append({"symbol": symbol, "status": "skipped (already seeded)"})
            continue

        # Generate + enrich
        df_raw = generate_ohlcv(symbol, trading_days)
        df     = clean_and_enrich(df_raw)

        inserted = 0
        for date_idx, row in df.iterrows():
            db.add(StockPrice(
                symbol       = symbol,
                date         = date_idx.date(),
                open         = round(float(row["open"]),  2),
                high         = round(float(row["high"]),  2),
                low          = round(float(row["low"]),   2),
                close        = round(float(row["close"]), 2),
                volume       = float(row["volume"]),
                daily_return = round(float(row["daily_return"]), 6),
                ma_7         = round(float(row["ma_7"]),  2),
            ))
            inserted += 1

        db.commit()
        results.append({"symbol": symbol, "rows_inserted": inserted})
        print(f"  ✓ {symbol:<20} {inserted} rows")

    return results


if __name__ == "__main__":
    print("Seeding mock data into stock_data.db …")
    init_db()
    db = next(get_db())
    try:
        results = seed_mock(db)
        print(f"\nDone. {len(results)} companies seeded.")
    finally:
        db.close()
