import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SHOPIFY_STORE_URL: str = os.getenv("SHOPIFY_STORE_URL", "")
    SHOPIFY_CLIENT_ID: str = os.getenv("SHOPIFY_CLIENT_ID", "")
    SHOPIFY_CLIENT_SECRET: str = os.getenv("SHOPIFY_CLIENT_SECRET", "")
    SHOPIFY_API_VERSION: str = os.getenv("SHOPIFY_API_VERSION", "2026-04")
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL: str = "claude-sonnet-4-6"
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://localhost/mega_enrichment")
    INPUT_CSV: str = os.getenv("INPUT_CSV", "products_export.csv")
    OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", "output")
    CONCURRENT_CLAUDE_CALLS: int = int(os.getenv("CONCURRENT_CLAUDE_CALLS", "20"))
    SCRAPER_ENABLED: bool = os.getenv("SCRAPER_ENABLED", "true").lower() == "true"
    SCRAPER_DELAY_SECONDS: float = float(os.getenv("SCRAPER_DELAY_SECONDS", "2"))
    TIER1_MIN_PRICE: float = float(os.getenv("TIER1_MIN_PRICE", "50.0"))
    TIER2_MIN_PRICE: float = float(os.getenv("TIER2_MIN_PRICE", "10.0"))
    TIER1_MAX_TOKENS: int = 2000
    TIER2_MAX_TOKENS: int = 1200
    TIER3_MAX_TOKENS: int = 600
    CLAUDE_MAX_RETRIES: int = 3
    SHOPIFY_MAX_RETRIES: int = 5
    POLL_INTERVAL_SECONDS: int = 10
    SEO_TITLE_MAX_CHARS: int = 70
    META_DESC_MIN_CHARS: int = 50
    META_DESC_MAX_CHARS: int = 160
    PRICE_INPUT_PER_MTK: float = 3.0
    PRICE_OUTPUT_PER_MTK: float = 15.0
    DASHBOARD_PORT: int = int(os.getenv("DASHBOARD_PORT", "8080"))
    DASHBOARD_HOST: str = os.getenv("DASHBOARD_HOST", "0.0.0.0")

    @property
    def shopify_token_url(self) -> str:
        return f"https://{self.SHOPIFY_STORE_URL}/admin/oauth/access_token"

    @property
    def shopify_graphql_url(self) -> str:
        return f"https://{self.SHOPIFY_STORE_URL}/admin/api/{self.SHOPIFY_API_VERSION}/graphql.json"

    def validate(self):
        missing = []
        for attr in ["SHOPIFY_STORE_URL", "SHOPIFY_CLIENT_ID",
                     "SHOPIFY_CLIENT_SECRET", "ANTHROPIC_API_KEY", "DATABASE_URL"]:
            if not getattr(self, attr):
                missing.append(attr)
        if missing:
            raise EnvironmentError(
                f"Missing required environment variables: {', '.join(missing)}"
            )

config = Config()