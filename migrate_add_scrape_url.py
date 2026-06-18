"""
Migration: Add scrape_url column to enrichments table.
Run this once to add the new column to existing database.
"""
from database import engine, Base
from sqlalchemy import text

def migrate():
    print("Adding scrape_url column to enrichments table...")
    with engine.connect() as conn:
        # Check if column exists
        result = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'enrichments' AND column_name = 'scrape_url'
        """))
        if result.fetchone():
            print("  Column scrape_url already exists - skipping")
        else:
            conn.execute(text("""
                ALTER TABLE enrichments ADD COLUMN scrape_url TEXT
            """))
            conn.commit()
            print("  Added scrape_url column successfully")

if __name__ == "__main__":
    migrate()