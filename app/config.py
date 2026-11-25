"""Runtime configuration, all from environment variables (see README)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("MP_DATA_DIR", ROOT / "data"))
KNOWLEDGE_DIR = Path(os.environ.get("MP_KNOWLEDGE_DIR", ROOT / "knowledge"))
DB_PATH = DATA_DIR / "meeting_prep.sqlite3"
OUTBOX_DIR = DATA_DIR / "outbox"
EXPORT_DIR = DATA_DIR / "exports"

# LLM (Anthropic-compatible endpoint). Falls back to the standard Anthropic env vars.
LLM_BASE_URL = os.environ.get("MP_LLM_BASE_URL") or os.environ.get("LLM_PROXY_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL")
LLM_API_KEY = os.environ.get("MP_LLM_API_KEY") or os.environ.get("LLM_PROXY_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
MODEL = os.environ.get("MP_MODEL", "claude-opus-5-5")
RESEARCH_EFFORT = os.environ.get("MP_RESEARCH_EFFORT", "low")
WRITE_EFFORT = os.environ.get("MP_WRITE_EFFORT", "medium")
WEB_SEARCH_TOOL = os.environ.get("MP_WEB_SEARCH_TOOL", "web_search_20250305")
SEARCHES_PER_AREA = int(os.environ.get("MP_SEARCHES_PER_AREA", "5"))
PARALLEL_CALLS = int(os.environ.get("MP_PARALLEL_CALLS", "4"))

# Prices in USD per million tokens / per 1000 searches, for the cost log.
PRICE_IN = float(os.environ.get("MP_PRICE_IN", "4.0"))
PRICE_OUT = float(os.environ.get("MP_PRICE_OUT", "20.0"))
PRICE_CACHE_WRITE = float(os.environ.get("MP_PRICE_CACHE_WRITE", "5.0"))
PRICE_CACHE_READ = float(os.environ.get("MP_PRICE_CACHE_READ", "0.20"))
PRICE_SEARCH_PER_1000 = float(os.environ.get("MP_PRICE_SEARCH_PER_1000", "10.0"))

# Apollo (optional). Without a key the research still runs, see requirements 4.6.
APOLLO_API_KEY = os.environ.get("APOLLO_API_KEY", "")
APOLLO_BASE_URL = os.environ.get("APOLLO_BASE_URL", "https://api.apollo.io/api/v1")

# Weekly digest email (SMTP optional; without it the digest is saved to the outbox).
DIGEST_TO = os.environ.get("MP_DIGEST_TO", "puneet@jgsaw.ai")
SMTP_HOST = os.environ.get("MP_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("MP_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("MP_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("MP_SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("MP_SMTP_FROM", SMTP_USER)
TIMEZONE = os.environ.get("MP_TIMEZONE", "Europe/Berlin")

# Public base URL used in digest links.
PUBLIC_URL = os.environ.get("MP_PUBLIC_URL", "http://localhost:4510")

# Access: named POC users, "name:password,name2:password2". If unset, data/users.json is used.
USERS_ENV = os.environ.get("MP_USERS", "")
SECRET_KEY_FILE = DATA_DIR / "secret.key"

CHROME = os.environ.get("MP_CHROME", "google-chrome")

for d in (DATA_DIR, OUTBOX_DIR, EXPORT_DIR):
    d.mkdir(parents=True, exist_ok=True)
