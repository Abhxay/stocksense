"""
database.py — SQLAlchemy setup with SQLite
"""
from sqlalchemy import create_engine, Column, String, Float, Date, Integer, Text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = "sqlite:///./stock_data.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class StockPrice(Base):
    __tablename__ = "stock_prices"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), index=True, nullable=False)
    date = Column(Date, nullable=False)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    daily_return = Column(Float)
    ma_7 = Column(Float)


class Company(Base):
    __tablename__ = "companies"

    symbol = Column(String(20), primary_key=True)
    name = Column(String(100))
    sector = Column(String(50))
    market_cap_cr = Column(Float)
    description = Column(Text)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
