import os

SECRET_KEY             = os.environ["SUPERSET_SECRET_KEY"]
SQLALCHEMY_DATABASE_URI = (
    f"postgresql+psycopg2://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@{os.environ.get('POSTGRES_HOST', 'postgres')}:5432/superset"
)

WTF_CSRF_ENABLED       = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE   = False  # set True behind HTTPS in production
TALISMAN_ENABLED        = False

# Allow embedding in iframes for dashboard sharing
HTTP_HEADERS             = {}

# Custom CSS injected into every Superset page.
# Adds a dark pitch-green accent to the navbar for easy recognition.
CUSTOM_CSS = """
.navbar { background-color: #0a3d0a !important; }
.navbar-brand { color: #a8d8a8 !important; font-weight: bold; }
"""

# Feature flags
FEATURE_FLAGS = {
    "ENABLE_TEMPLATE_PROCESSING": True,
    "DASHBOARD_NATIVE_FILTERS":   True,
    "GLOBAL_ASYNC_QUERIES":       False,
}

# Row limit for SQL Lab results
SQL_MAX_ROW = 50000

# Cache: simple in-process cache (replace with Redis in production)
CACHE_CONFIG = {
    "CACHE_TYPE":    "SimpleCache",
    "CACHE_DEFAULT_TIMEOUT": 300,
}
