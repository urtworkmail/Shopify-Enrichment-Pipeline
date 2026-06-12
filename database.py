"""
database.py -- PostgreSQL layer using SQLAlchemy ORM.

Tables:
    runs         -- one row per pipeline execution
    products     -- all products loaded from CSV/Shopify export
    enrichments  -- Claude output per product
    logs         -- every pipeline event, error, retry
"""

from datetime import datetime
from contextlib import contextmanager

from sqlalchemy import (
    create_engine, Column, String, Integer, Float, Boolean,
    DateTime, Text, JSON, ForeignKey, Index
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session

from config import config

Base = declarative_base()
engine = create_engine(
    config.DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_timeout=10,          # <-- NEW: raise if no connection in 10s
    connect_args={
        "connect_timeout": 10 # <-- NEW: TCP connect timeout
    },
    echo=False,
)

# --- new event listener ---
from sqlalchemy import event

@event.listens_for(engine, "connect")
def _set_statement_timeout(dbapi_conn, connection_record):
    cur = dbapi_conn.cursor()
    cur.execute("SET statement_timeout = '10000'")
    cur.execute("SET idle_in_transaction_session_timeout = '15000'")
    cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)   # <-- MUST BE PRESENT


# ── Models ────────────────────────────────────────────────────────────────────

class Run(Base):
    __tablename__ = "runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String(32), default="running")  # running, completed, failed, paused
    total_products = Column(Integer, default=0)
    enriched_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    skipped_count = Column(Integer, default=0)
    total_input_tokens = Column(Integer, default=0)
    total_output_tokens = Column(Integer, default=0)
    estimated_cost_usd = Column(Float, default=0.0)
    writeback_status = Column(String(32), default="pending")  # pending, running, completed, failed
    notes = Column(Text, nullable=True)

    enrichments = relationship("Enrichment", back_populates="run", lazy="dynamic")
    logs = relationship("Log", back_populates="run", lazy="dynamic")


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sku = Column(String(128), unique=True, nullable=False, index=True)
    shopify_product_id = Column(String(64), nullable=True, index=True)
    title = Column(Text, nullable=True)
    price = Column(Float, nullable=True)
    tier = Column(Integer, nullable=True)
    vendor = Column(String(256), nullable=True)
    product_type = Column(String(256), nullable=True)
    tags = Column(JSON, nullable=True)
    handle = Column(String(256), nullable=True)
    supplier_name = Column(String(256), nullable=True)
    supplier_url = Column(Text, nullable=True)
    description_html = Column(Text, nullable=True)
    images = Column(JSON, nullable=True)        # [{id, url, altText}]
    image_count = Column(Integer, default=0)
    barcode = Column(String(128), nullable=True)
    rrp = Column(Float, nullable=True)
    cost = Column(Float, nullable=True)
    mpn = Column(String(128), nullable=True)
    supplier_code = Column(String(128), nullable=True)
    upc = Column(String(128), nullable=True)
    metafields = Column(JSON, nullable=True)    # [{namespace, key, value, type}]
    raw_shopify_data = Column(JSON, nullable=True)
    raw_csv_data = Column(JSON, nullable=True)
    existing_content = Column(JSON, nullable=True)  # Extracted metafield values from Shopify -- augment base
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    enrichments = relationship("Enrichment", back_populates="product", lazy="dynamic")

    __table_args__ = (
        Index("ix_products_sku_shopify_id", "sku", "shopify_product_id"),
    )


class Enrichment(Base):
    __tablename__ = "enrichments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("runs.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    sku = Column(String(128), nullable=False, index=True)
    tier = Column(String(5), nullable=True)
    status = Column(String(32), default="pending")  # pending, success, failed, skipped
    scrape_status = Column(String(64), nullable=True)
    scraped_content = Column(JSON, nullable=True)
    claude_input_tokens = Column(Integer, default=0)
    claude_output_tokens = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)
    enriched_data = Column(JSON, nullable=True)     # Full Claude JSON output
    retry_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    needs_manual_review = Column(Boolean, default=False)
    writeback_status = Column(String(32), default="pending")  # pending, success, failed
    writeback_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    run = relationship("Run", back_populates="enrichments")
    product = relationship("Product", back_populates="enrichments")


class Log(Base):
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("runs.id"), nullable=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    level = Column(String(16), default="INFO")   # INFO, WARNING, ERROR, DEBUG
    module = Column(String(64), nullable=True)
    sku = Column(String(128), nullable=True, index=True)
    message = Column(Text, nullable=False)
    details = Column(JSON, nullable=True)

    run = relationship("Run", back_populates="logs")

    __table_args__ = (
        Index("ix_logs_run_level", "run_id", "level"),
    )


class ShopifyToken(Base):
    __tablename__ = "shopify_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    access_token = Column(Text, nullable=False)
    minted_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def init_db():
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=engine)
    print("[db] Tables initialised.")


@contextmanager
def get_db() -> Session:
    """Context manager for database sessions."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _model_columns() -> set:
    """Return the set of column names defined on the Product model."""
    return {c.key for c in Product.__table__.columns}


def upsert_product(db: Session, data: dict) -> "Product":
    """Insert or update a product by SKU. Ignores keys not on the model."""
    valid_keys = _model_columns()
    clean = {k: v for k, v in data.items() if k in valid_keys}

    product = db.query(Product).filter_by(sku=clean["sku"]).first()
    if product:
        for k, v in clean.items():
            setattr(product, k, v)
        product.updated_at = datetime.utcnow()
    else:
        product = Product(**clean)
        db.add(product)
    db.flush()
    return product


def get_run_stats(db: Session, run_id: int) -> dict:
    """Return current stats for a run -- used by dashboard."""
    run = db.query(Run).filter_by(id=run_id).first()
    if not run:
        return {}

    return {
        "id": run.id,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "total_products": run.total_products,
        "enriched_count": run.enriched_count,
        "failed_count": run.failed_count,
        "skipped_count": run.skipped_count,
        "progress_pct": round(
            (run.enriched_count + run.failed_count + run.skipped_count)
            / max(run.total_products, 1) * 100, 1
        ),
        "total_input_tokens": run.total_input_tokens,
        "total_output_tokens": run.total_output_tokens,
        "estimated_cost_usd": round(run.estimated_cost_usd, 4),
        "writeback_status": run.writeback_status,
    }
