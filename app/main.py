"""
Mockett AI Dashboard — FastAPI Backend
Run: uvicorn main:app --reload --port 8000
"""

import logging
import logging.handlers
import os, json, re, sys, sqlite3, threading
from pathlib import Path
from datetime import date, timedelta, datetime
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange, Dimension, Metric, RunReportRequest, OrderBy
)
from googleapiclient.discovery import build
import google.auth.exceptions
import google.api_core.exceptions
import pymysql
from openai import OpenAI

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent

load_dotenv(PROJECT_ROOT / ".env")

app = FastAPI(title="Mockett AI Dashboard API")
logger = logging.getLogger("mockett.analytics")
cors_origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["GET"], allow_headers=["Content-Type", "Authorization"])
STATIC_DIR = PROJECT_ROOT / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def serve_dashboard():
    return FileResponse(PROJECT_ROOT / "index.html")

# ── Logging setup ──────────────────────────────────────────────────
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

def _build_logger():
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("mockett")
    logger.setLevel(logging.DEBUG)

    # Rotating file — keep 7 days of 10 MB files
    fh = logging.handlers.RotatingFileHandler(
        LOG_DIR / "dashboard.log", maxBytes=10 * 1024 * 1024, backupCount=7, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    # Stdout for systemd / journalctl
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger

logger = _build_logger()


# ═══════════════════════════════════════════════════════════════════
# USAGE TRACKING — lightweight SQLite access log
# ═══════════════════════════════════════════════════════════════════

USAGE_DB = PROJECT_ROOT / "data" / "usage.db"
USAGE_DB.parent.mkdir(parents=True, exist_ok=True)
_usage_lock = threading.Lock()

def _init_usage_db():
    conn = sqlite3.connect(str(USAGE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS page_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            path TEXT NOT NULL,
            ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pv_ts ON page_views(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pv_email ON page_views(email)")
    conn.commit()
    conn.close()

_init_usage_db()

def _log_page_view(email: str, path: str):
    with _usage_lock:
        conn = sqlite3.connect(str(USAGE_DB))
        try:
            conn.execute(
                "INSERT INTO page_views (email, path, ts) VALUES (?, ?, ?)",
                (email, path, datetime.utcnow().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

# Paths we don't want to log (static assets, API calls, favicon, etc.)
_SKIP_PREFIXES = ("/static/", "/api/", "/favicon")

@app.middleware("http")
async def usage_tracking_middleware(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    # Only log actual page views, not API calls or static assets
    if not any(path.startswith(p) for p in _SKIP_PREFIXES):
        email = request.headers.get("Cf-Access-Authenticated-User-Email", "").strip()
        if email:
            try:
                _log_page_view(email, path)
            except Exception:
                logger.debug("Usage log write failed", exc_info=True)
    return response


@app.get("/api/usage-stats")
def usage_stats(group: str = "day", days: int = 30):
    """
    Dashboard usage stats. Only for your eyes.
    ?group=day|week|month  — how to bucket the counts
    ?days=30               — how far back to look
    """
    days = max(1, min(days, 365))
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

    if group == "week":
        date_expr = "strftime('%Y-W%W', ts)"
    elif group == "month":
        date_expr = "strftime('%Y-%m', ts)"
    else:
        date_expr = "date(ts)"

    conn = sqlite3.connect(str(USAGE_DB))
    conn.row_factory = sqlite3.Row
    try:
        # Per-user totals
        rows = conn.execute(
            "SELECT email, COUNT(*) AS visits FROM page_views WHERE ts >= ? GROUP BY email ORDER BY visits DESC",
            (cutoff,),
        ).fetchall()
        by_user = [{"email": r["email"], "visits": r["visits"]} for r in rows]

        # Time series
        rows = conn.execute(
            f"SELECT {date_expr} AS period, COUNT(*) AS visits FROM page_views WHERE ts >= ? GROUP BY period ORDER BY period",
            (cutoff,),
        ).fetchall()
        by_period = [{"period": r["period"], "visits": r["visits"]} for r in rows]

        # Total
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM page_views WHERE ts >= ?", (cutoff,)
        ).fetchone()["n"]
    finally:
        conn.close()

    return {
        "range_days": days,
        "group_by": group,
        "total_views": total,
        "by_user": by_user,
        "by_period": by_period,
    }


# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════

GA4_PROPERTY_ID = os.getenv("GA4_PROPERTY_ID", "312242279")
GSC_SITE_URL    = os.getenv("GSC_SITE_URL", "https://www.mockett.com/")
OAUTH_TOKEN     = PROJECT_ROOT / "credentials" / "oauth-token.json"
GA4_SERVICE_ACCOUNT = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
if GA4_SERVICE_ACCOUNT:
    ga4_service_account_path = Path(GA4_SERVICE_ACCOUNT)
    if not ga4_service_account_path.is_absolute():
        ga4_service_account_path = PROJECT_ROOT / ga4_service_account_path
else:
    ga4_service_account_path = None


# ═══════════════════════════════════════════════════════════════════
# AUTH & CLIENTS
# ═══════════════════════════════════════════════════════════════════

# ── Custom exception so callers can distinguish auth problems ───────
class GoogleAuthError(Exception):
    """Raised when Google OAuth or service account auth is not usable."""
    def __init__(self, source: str, detail: str):
        self.source = source          # "ga4" or "gsc"
        self.detail = detail          # safe string, no secrets
        super().__init__(f"[{source}] {detail}")


# ── OAuth credential loader ─────────────────────────────────────────
def get_credentials() -> "google.oauth2.credentials.Credentials":
    if not OAUTH_TOKEN.exists():
        msg = "OAuth token file not found. Run scripts/get_token.py locally and copy to server."
        logger.error("Google auth: %s", msg)
        raise GoogleAuthError("google", msg)

    with open(OAUTH_TOKEN) as f:
        td = json.load(f)

    creds = Credentials(
        token=td["token"],
        refresh_token=td["refresh_token"],
        token_uri=td["token_uri"],
        client_id=td["client_id"],
        client_secret=td["client_secret"],
        scopes=td["scopes"],
    )

    try:
        creds.refresh(Request())
        # Write updated token back safely
        td["token"] = creds.token
        tmp = OAUTH_TOKEN.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(td, f, indent=2)
        tmp.replace(OAUTH_TOKEN)
        logger.debug("Google OAuth token refreshed successfully.")
    except google.auth.exceptions.RefreshError as e:
        err_str = str(e)
        if "invalid_grant" in err_str:
            msg = (
                "OAuth refresh token expired or was revoked. "
                "Run scripts/get_token.py locally and redeploy credentials/oauth-token.json. "
                "See docs/google-auth-recovery.md."
            )
            logger.error("Google auth REAUTHORIZATION REQUIRED: %s", msg)
            raise GoogleAuthError("google", msg) from e
        msg = f"OAuth token refresh failed with unexpected error. Details: {type(e).__name__}"
        logger.error("Google auth refresh error: %s", msg)
        raise GoogleAuthError("google", msg) from e
    except Exception as e:
        msg = f"Unexpected error during OAuth refresh: {type(e).__name__}"
        logger.error("Google auth: %s", msg)
        raise GoogleAuthError("google", msg) from e

    return creds


# ── GA4 credentials: prefer service account, fall back to OAuth ─────
def get_ga4_credentials():
    """
    Returns credentials for GA4.
    Prefers service account if configured and accessible.
    Falls back to OAuth. Raises GoogleAuthError if neither works.
    """
    if ga4_service_account_path and ga4_service_account_path.exists():
        try:
            creds = service_account.Credentials.from_service_account_file(
                ga4_service_account_path,
                scopes=["https://www.googleapis.com/auth/analytics.readonly"],
            )
            logger.debug("GA4: using service account credentials.")
            return creds
        except Exception as e:
            logger.warning(
                "GA4 service account load failed (%s); falling back to OAuth.", type(e).__name__
            )
    else:
        logger.debug("GA4: no service account configured; using OAuth.")

    return get_credentials()


def get_ga4_client():
    return BetaAnalyticsDataClient(credentials=get_ga4_credentials())


def run_ga4_report(request: RunReportRequest):
    """
    Run a GA4 report.
    On service account permission denial, retries with OAuth.
    Raises GoogleAuthError on auth failure.
    Raises HTTPException(500) on other errors.
    """
    try:
        creds = get_ga4_credentials()
        return BetaAnalyticsDataClient(credentials=creds).run_report(request)
    except GoogleAuthError:
        raise
    except google.api_core.exceptions.PermissionDenied as e:
        if ga4_service_account_path and ga4_service_account_path.exists():
            logger.warning(
                "GA4 service account permission denied for property %s; falling back to OAuth.",
                GA4_PROPERTY_ID,
            )
            try:
                oauth_creds = get_credentials()
                return BetaAnalyticsDataClient(credentials=oauth_creds).run_report(request)
            except GoogleAuthError:
                raise
            except Exception as fallback_e:
                logger.error("GA4 OAuth fallback also failed: %s", type(fallback_e).__name__)
                raise HTTPException(500, detail="Dashboard data is unavailable for this request.") from fallback_e
        logger.error("GA4 PermissionDenied (no service account to fall back to): %s", str(e)[:120])
        raise HTTPException(500, detail="Dashboard data is unavailable for this request.") from e
    except Exception as e:
        logger.error("GA4 report failed: %s: %s", type(e).__name__, str(e)[:120])
        raise HTTPException(500, detail="Dashboard data is unavailable for this request.") from e


def get_gsc_service():
    """Build a Search Console service using OAuth credentials."""
    try:
        creds = get_credentials()
        svc = build("searchconsole", "v1", credentials=creds)
        logger.debug("GSC: service built with OAuth credentials.")
        return svc
    except GoogleAuthError:
        raise
    except Exception as e:
        logger.error("GSC service build failed: %s", type(e).__name__)
        raise


# ── api_error helper — now surfaces GoogleAuthError distinctly ───────
def api_error(e):
    if isinstance(e, HTTPException):
        raise e
    if isinstance(e, GoogleAuthError):
        logger.error("API endpoint hit GoogleAuthError: %s", e.detail)
        raise HTTPException(503, detail="Google authorization needs to be renewed. Contact the dashboard administrator.")
    logger.exception("API request failed")
    raise HTTPException(500, detail="Dashboard data is unavailable for this request.")

def get_magento_conn():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT", 3306)),
        user=os.getenv("MYSQL_USER"), password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DB"), cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
    )

def get_openai():
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ═══════════════════════════════════════════════════════════════════
# SHARED HELPERS
# ═══════════════════════════════════════════════════════════════════

def parse_iso_date(value: Optional[str], field_name: str):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, detail=f"{field_name} must be YYYY-MM-DD")

def date_range(days: int = 30, start_date: Optional[str] = None, end_date: Optional[str] = None):
    """
    Resolve the dashboard period. Preset ranges are rolling windows ending yesterday;
    custom ranges use the exact UI-selected start/end dates.
    """
    start = parse_iso_date(start_date, "start_date")
    end = parse_iso_date(end_date, "end_date")

    if start or end:
        if not start or not end:
            raise HTTPException(400, detail="Both start_date and end_date are required for a custom range")
        if start > end:
            raise HTTPException(400, detail="start_date must be before or equal to end_date")
        if end >= date.today():
            raise HTTPException(400, detail="Custom range must end before today")
        if (end - start).days + 1 > 500:
            raise HTTPException(400, detail="Custom range cannot exceed 500 days")
        return start, end

    days = max(1, min(int(days), 500))
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    return start, end

def period_context(days: int = 30, start_date: Optional[str] = None, end_date: Optional[str] = None, period_label: Optional[str] = None):
    start, end = date_range(days, start_date, end_date)
    period_days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=period_days - 1)
    if start_date or end_date:
        label = "Custom Range"
        period_type = "custom"
    elif period_days == 90:
        label = "Last 90 Days"
        period_type = "rolling"
    elif period_days == 270:
        label = "Last 270 Days"
        period_type = "rolling"
    else:
        label = f"Last {period_days} Days"
        period_type = "rolling"
    if period_days <= 14:
        mode = "short-term pulse"
    elif period_days <= 45:
        mode = "selected-period executive review"
    elif period_days <= 120:
        mode = "quarterly trend review"
    else:
        mode = "long-term trend review"
    return {
        "label": label,
        "type": period_type,
        "mode": mode,
        "days": period_days,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "comparison": {
            "label": f"prior {period_days}-day period",
            "start_date": prev_start.isoformat(),
            "end_date": prev_end.isoformat(),
        },
        "start": start,
        "end": end,
        "prev_start": prev_start,
        "prev_end": prev_end,
    }

def mysql_end_exclusive(end_date_value: date):
    return (end_date_value + timedelta(days=1)).isoformat()

def pct_delta(c, p):
    if not p: return None
    return round(((c - p) / p) * 100, 1)

def fmt_rev(v):
    if v >= 1_000_000: return f"${v/1_000_000:.2f}M"
    if v >= 1_000:     return f"${v/1_000:.1f}K"
    return f"${v:.0f}"

def normalize_search_term(term: str):
    return re.sub(r"\s+", " ", (term or "").strip()).lower()

def display_search_term(term: str):
    cleaned = re.sub(r"\s+", " ", (term or "").strip())
    if not cleaned:
        return ""
    sku_like = any(ch.isdigit() for ch in cleaned)
    if sku_like and any(ch.isalpha() for ch in cleaned):
        return cleaned.upper()
    return cleaned

ALLOWED_AI_ACTIONS = {
    "review_search_snippet_alignment",
    "review_low_ctr_page_copy",
    "review_featured_products",
    "review_related_products",
    "review_category_navigation",
}

PRODUCT_DROPOFF_MIN_REVENUE_DROP = 250
PRODUCT_DROPOFF_MIN_DELTA = -35
PRODUCT_DROPOFF_CANDIDATE_LIMIT = 50
PRODUCT_DROPOFF_RETURN_LIMIT = 10

ACTION_LABELS = {
    "review_search_snippet_alignment": "Review search snippet",
    "review_low_ctr_page_copy": "Review page copy",
    "review_featured_products": "Check featured products",
    "review_related_products": "Check related products",
    "review_category_navigation": "Review category links",
}

BROAD_WORK_PATTERNS = [
    r"\ball\b",
    r"\bevery\b",
    r"\bbulk\b",
    r"\bsitewide\b",
    r"\bcatalog rewrite\b",
    r"\btitle-template\b",
    r"\bredesign\b",
]

def has_numeric_citation(text: str):
    value = text or ""
    return bool(re.search(r"(\$\s?\d|\d[\d,.]*\s?(?:%|sessions?|impressions?|clicks?|ctr|revenue|pages?|products?|categories?|orders?|K|M)\b)", value, re.IGNORECASE))

def has_broad_work_language(text: str):
    lower = (text or "").lower()
    return any(re.search(pattern, lower) for pattern in BROAD_WORK_PATTERNS)

def ai_summary_limit(ctx):
    days = ctx.get("days", 30)
    if days <= 45:
        return 4
    if days <= 120:
        return 4
    return 3

def ai_opportunity_limit(ctx):
    days = ctx.get("days", 30)
    if days <= 45:
        return 5
    if days <= 120:
        return 5
    return 4

def ai_count_guidance(ctx, item_type, max_count):
    days = ctx.get("days", 30)
    if days <= 45:
        return f"For this short operational range, return up to {max_count} {item_type}; 2-3 strong items are better than filling the list. Return fewer when the data does not show distinct signals."
    if days <= 120:
        return f"For this quarterly-style range, return up to {max_count} {item_type}; 2-4 strong items are better than filler."
    return f"For this long-term range, return up to {max_count} durable {item_type}; fewer is acceptable when signals are stable or repetitive."

def normalize_ai_target(value: str):
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()[:120]

def ai_item_dedupe_key(item):
    action = item.get("action") or ""
    target = (
        item.get("target")
        or item.get("review_target")
        or item.get("url")
        or item.get("sku")
        or item.get("category")
        or item.get("title")
        or ""
    )
    return action, normalize_ai_target(target)

def clean_ai_items_with_metadata(items, max_count, allowed_actions=ALLOWED_AI_ACTIONS):
    cleaned = []
    seen_items = set()
    removed = 0
    reasons = {"unsupported_action": 0, "duplicate_target": 0, "missing_metric": 0, "broad_language": 0, "invalid_shape": 0}
    for item in items or []:
        if not isinstance(item, dict):
            removed += 1
            reasons["invalid_shape"] += 1
            continue
        action = item.get("action")
        description = item.get("desc") or item.get("description") or ""
        title = item.get("title") or ""
        if action not in allowed_actions:
            removed += 1
            reasons["unsupported_action"] += 1
            continue
        dedupe_key = ai_item_dedupe_key(item)
        if dedupe_key in seen_items:
            removed += 1
            reasons["duplicate_target"] += 1
            continue
        if not has_numeric_citation(f"{title} {description}"):
            removed += 1
            reasons["missing_metric"] += 1
            continue
        if has_broad_work_language(f"{title} {description}"):
            removed += 1
            reasons["broad_language"] += 1
            continue
        seen_items.add(dedupe_key)
        item["action_label"] = ACTION_LABELS.get(action, "Focused review")
        cleaned.append(item)
        if len(cleaned) >= max_count:
            break
    return cleaned, {"input_count": len(items or []), "returned": len(cleaned), "removed": removed, "reasons": reasons}

def clean_ai_items(items, max_count, allowed_actions=ALLOWED_AI_ACTIONS):
    return clean_ai_items_with_metadata(items, max_count, allowed_actions)[0]

def period_payload(ctx):
    return {
        "label": ctx["label"],
        "start_date": ctx["start_date"],
        "end_date": ctx["end_date"],
        "days": ctx["days"],
        "type": ctx["type"],
        "mode": ctx["mode"],
        "comparison": ctx["comparison"],
    }

def deterministic_summary(metrics, sources, ctx, reason="openai_unavailable"):
    max_count = ai_summary_limit(ctx)
    insights = []
    ga4 = metrics.get("ga4")
    gsc = metrics.get("gsc")
    if ga4:
        current = ga4.get("current", {})
        insights.append({
            "title": "GA4 ecommerce revenue snapshot",
            "type": "info",
            "description": f"GA4 shows ${current.get('revenue', 0):,.0f} ecommerce revenue and {current.get('sessions', 0):,} sessions for the selected period. Use this as a source-grounded pulse while AI text generation is unavailable.",
        })
    if gsc:
        insights.append({
            "title": "Search Console visibility snapshot",
            "type": "info",
            "description": f"Search Console shows {gsc.get('impressions', 0):,} impressions, {gsc.get('ctr', 0)}% CTR, and average position {gsc.get('position', 0)} for the selected period. Review Search Console directly for page-level detail if needed.",
        })
    categories = metrics.get("categories") or []
    if categories:
        top = categories[0]
        insights.append({
            "title": "Magento category order-line snapshot",
            "type": "info",
            "description": f"{top.get('name', 'The top category')} has ${top.get('revenue', 0):,.0f} in Magento parent order-line revenue for the selected period. Category totals can overlap when products belong to multiple categories.",
        })
    if not insights:
        insights.append({
            "title": "Data sources unavailable",
            "type": "info",
            "description": "GA4, Search Console, and Magento data could not be retrieved for this selected period. Check the source connections and retry the dashboard load.",
        })
    return {
        "insights": insights[:max_count],
        "generated_at": datetime.now().isoformat(),
        "period": period_payload(ctx),
        "data_snapshot": {
            "sessions": ga4["current"]["sessions"] if ga4 else None,
            "revenue": ga4["current"]["revenue"] if ga4 else None,
            "impressions": gsc["impressions"] if gsc else None,
            "ctr": gsc["ctr"] if gsc else None,
        },
        "sources": sources,
        "partial_data": any(s["status"] != "available" for s in sources.values()),
        "fallback": True,
        "fallback_reason": reason,
        "filtered_items": {"input_count": 0, "returned": len(insights[:max_count]), "removed": 0, "reasons": {}},
    }

def deterministic_opportunities(signals, sources, ctx, reason="openai_unavailable"):
    max_count = ai_opportunity_limit(ctx)
    opportunities = []
    for page in signals.get("gsc_pages", []):
        if page.get("impressions", 0) > 5000 and page.get("ctr", 100) < 2.0:
            opportunities.append({
                "icon": "search",
                "priority": "med",
                "title": "Review a high-impression low-CTR page",
                "desc": f"{page.get('url', 'A Search Console page')} has {page.get('impressions', 0):,} impressions and {page.get('ctr', 0)}% CTR in the selected period. Review that page's search snippet and visible page copy for alignment.",
                "action": "review_search_snippet_alignment",
                "action_label": ACTION_LABELS["review_search_snippet_alignment"],
            })
            break
    for product in signals.get("product_dropoffs", []):
        if product.get("revenue_drop", 0) >= 250:
            opportunities.append({
                "icon": "box",
                "priority": "med",
                "title": "Review a product revenue drop-off",
                "desc": f"{product.get('sku', 'A product')} fell from ${product.get('previous_revenue', 0):,.0f} to ${product.get('current_revenue', 0):,.0f} in Magento order-line revenue. Check the product page, placement, and related-product context for this item only.",
                "action": "review_related_products",
                "action_label": ACTION_LABELS["review_related_products"],
            })
            break
    for category in signals.get("categories", []):
        if category.get("delta") is not None and category.get("delta") <= -25:
            opportunities.append({
                "icon": "category",
                "priority": "low",
                "title": "Review a declining category signal",
                "desc": f"{category.get('name', 'A category')} is at ${category.get('revenue', 0):,.0f} Magento order-line revenue with a {category.get('delta')}% change versus the prior period. Review featured products or category navigation for this category only.",
                "action": "review_featured_products",
                "action_label": ACTION_LABELS["review_featured_products"],
            })
            break
    return {
        "opportunities": opportunities[:max_count],
        "generated_at": datetime.now().isoformat(),
        "period": period_payload(ctx),
        "sources": sources,
        "partial_data": any(s["status"] != "available" for s in sources.values()),
        "total": len(opportunities[:max_count]),
        "fallback": True,
        "fallback_reason": reason,
        "filtered_items": {"input_count": 0, "returned": len(opportunities[:max_count]), "removed": 0, "reasons": {}},
    }

def lm_dates():
    today    = date.today()
    lm_end   = today.replace(day=1) - timedelta(days=1)
    lm_start = lm_end.replace(day=1)
    return lm_start, lm_end


# ═══════════════════════════════════════════════════════════════════
# GA4 ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/ga4/sessions-revenue")
def ga4_sessions_revenue(days: int = 30, start_date: Optional[str] = None, end_date: Optional[str] = None):
    """Daily sessions + revenue. Powers Revenue & Sessions chart."""
    try:
        start, end = date_range(days, start_date, end_date)
        resp = run_ga4_report(RunReportRequest(
            property=f"properties/{GA4_PROPERTY_ID}",
            date_ranges=[DateRange(start_date=start.isoformat(), end_date=end.isoformat())],
            dimensions=[Dimension(name="date")],
            metrics=[Metric(name="sessions"), Metric(name="purchaseRevenue")],
            order_bys=[OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name="date"))],
        ))
        labels, sessions, revenue = [], [], []
        for row in resp.rows:
            raw = row.dimension_values[0].value
            d   = date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
            labels.append(f"{d.month}/{d.day}")
            sessions.append(int(row.metric_values[0].value))
            revenue.append(round(float(row.metric_values[1].value), 2))
        return {"period": {"start_date": start.isoformat(), "end_date": end.isoformat()}, "labels": labels, "sessions": sessions, "revenue": revenue}
    except Exception as e:
        api_error(e)


@app.get("/api/ga4/kpis")
def ga4_kpis(days: int = 30, start_date: Optional[str] = None, end_date: Optional[str] = None):
    """Rolling N-day KPIs vs prior N days. Powers the 4 KPI cards."""
    try:
        ctx        = period_context(days, start_date, end_date)
        start      = ctx["start"]
        end        = ctx["end"]
        prev_start = ctx["prev_start"]
        prev_end   = ctx["prev_end"]

        def fetch(s, e):
            req = RunReportRequest(
                property=f"properties/{GA4_PROPERTY_ID}",
                date_ranges=[DateRange(start_date=s.isoformat(), end_date=e.isoformat())],
                metrics=[
                    Metric(name="sessions"), Metric(name="purchaseRevenue"),
                    Metric(name="sessionConversionRate"), Metric(name="engagementRate"),
                ],
            )
            rows = run_ga4_report(req).rows
            if not rows:
                return {
                    "sessions": 0,
                    "revenue": 0,
                    "conversion_rate": 0,
                    "engagement_rate": 0,
                }
            row = rows[0]
            return {
                "sessions":        int(row.metric_values[0].value),
                "revenue":         round(float(row.metric_values[1].value), 2),
                "conversion_rate": round(float(row.metric_values[2].value) * 100, 2),
                "engagement_rate": round(float(row.metric_values[3].value) * 100, 2),
            }

        curr = fetch(start, end)
        prev = fetch(prev_start, prev_end)
        return {
            "current":  curr,
            "previous": prev,
            "period_days": ctx["days"],
            "metric_notes": {
                "revenue": "GA4 purchaseRevenue",
                "conversion_rate": "GA4 sessionConversionRate, shown as a percentage",
                "engagement_rate": "GA4 engagementRate, shown as a percentage",
            },
            "period": {
                "label": ctx["label"],
                "start_date": ctx["start_date"],
                "end_date": ctx["end_date"],
                "comparison": ctx["comparison"],
            },
            "deltas": {
                "sessions":        pct_delta(curr["sessions"],        prev["sessions"]),
                "revenue":         pct_delta(curr["revenue"],         prev["revenue"]),
                "conversion_rate": pct_delta(curr["conversion_rate"], prev["conversion_rate"]),
                "engagement_rate": pct_delta(curr["engagement_rate"], prev["engagement_rate"]),
            }
        }
    except Exception as e:
        api_error(e)


@app.get("/api/ga4/traffic-sources")
def ga4_traffic_sources(days: int = 30, start_date: Optional[str] = None, end_date: Optional[str] = None):
    """Session breakdown by channel group. Powers Traffic Sources donut."""
    try:
        start, end = date_range(days, start_date, end_date)
        resp = run_ga4_report(RunReportRequest(
            property=f"properties/{GA4_PROPERTY_ID}",
            date_ranges=[DateRange(start_date=start.isoformat(), end_date=end.isoformat())],
            dimensions=[Dimension(name="sessionDefaultChannelGroup")],
            metrics=[Metric(name="sessions")],
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
        ))
        total   = sum(int(r.metric_values[0].value) for r in resp.rows)
        sources = []
        for row in resp.rows:
            name = row.dimension_values[0].value
            sess = int(row.metric_values[0].value)
            sources.append({"name": name, "sessions": sess, "pct": round(sess/total*100, 1) if total else 0})
        return {"period": {"start_date": start.isoformat(), "end_date": end.isoformat()}, "sources": sources, "total": total}
    except Exception as e:
        api_error(e)


@app.get("/api/ga4/search-terms")
def ga4_search_terms(days: int = 30, start_date: Optional[str] = None, end_date: Optional[str] = None, limit: int = 40):
    """
    On-site search terms from GA4. Blank non-search rows are filtered,
    identical terms are grouped case-insensitively, and SKU-like searches display
    in uppercase without merging meaningful suffixes such as DP128/9.
    """
    try:
        ctx = period_context(days, start_date, end_date)
        limit = max(10, min(int(limit), 100))

        resp = run_ga4_report(RunReportRequest(
            property=f"properties/{GA4_PROPERTY_ID}",
            date_ranges=[DateRange(start_date=ctx["start_date"], end_date=ctx["end_date"])],
            dimensions=[Dimension(name="searchTerm")],
            metrics=[Metric(name="sessions")],
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
            limit=250,
        ))

        grouped = {}
        blank_sessions = 0
        for row in resp.rows:
            raw = row.dimension_values[0].value or ""
            sessions = int(float(row.metric_values[0].value or 0))
            normalized = normalize_search_term(raw)
            if not normalized or normalized in {"(not set)", "not set", "(not provided)", "not provided"}:
                blank_sessions += sessions
                continue

            bucket = grouped.setdefault(normalized, {
                "search_term": display_search_term(raw),
                "normalized": normalized,
                "sessions": 0,
                "variants": {},
            })
            bucket["sessions"] += sessions
            display = display_search_term(raw)
            bucket["variants"][display] = bucket["variants"].get(display, 0) + sessions

        terms = []
        for item in grouped.values():
            variants = item.pop("variants")
            if not (item["search_term"].isupper() and any(ch.isalpha() for ch in item["search_term"])):
                item["search_term"] = max(variants.items(), key=lambda kv: kv[1])[0]
            item["is_sku_like"] = any(ch.isdigit() for ch in item["search_term"])
            terms.append(item)

        terms.sort(key=lambda t: t["sessions"], reverse=True)
        top_terms = terms[:limit]
        return {
            "period": {
                "label": ctx["label"],
                "start_date": ctx["start_date"],
                "end_date": ctx["end_date"],
            },
            "terms": top_terms,
            "total_search_sessions": sum(t["sessions"] for t in terms),
            "unique_terms": len(terms),
            "blank_sessions_filtered": blank_sessions,
            "top_term": top_terms[0] if top_terms else None,
        }

    except Exception as e:
        api_error(e)


# ═══════════════════════════════════════════════════════════════════
# SEARCH CONSOLE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/gsc/summary")
def gsc_summary(days: int = 30, start_date: Optional[str] = None, end_date: Optional[str] = None):
    """Total impressions, clicks, CTR, avg position. Powers SEO KPI cards."""
    try:
        service    = get_gsc_service()
        start, end = date_range(days, start_date, end_date)
        result = service.searchanalytics().query(
            siteUrl=GSC_SITE_URL,
            body={"startDate": start.isoformat(), "endDate": end.isoformat(), "dimensions": []}
        ).execute()
        row = result.get("rows", [{}])[0]
        return {
            "impressions": int(row.get("impressions", 0)),
            "clicks":      int(row.get("clicks", 0)),
            "ctr":         round(row.get("ctr", 0) * 100, 2),
            "position":    round(row.get("position", 0), 1),
            "period": {"start_date": start.isoformat(), "end_date": end.isoformat()},
        }
    except Exception as e:
        api_error(e)


@app.get("/api/gsc/pages")
def gsc_pages(days: int = 30, limit: int = 20, start_date: Optional[str] = None, end_date: Optional[str] = None):
    """Top pages by impressions. Powers SEO opportunity list + scatter chart."""
    try:
        service    = get_gsc_service()
        start, end = date_range(days, start_date, end_date)
        result = service.searchanalytics().query(
            siteUrl=GSC_SITE_URL,
            body={
                "startDate": start.isoformat(), "endDate": end.isoformat(),
                "dimensions": ["page"], "rowLimit": limit,
                "orderBy": [{"fieldName": "impressions", "sortOrder": "DESCENDING"}],
            }
        ).execute()
        pages = []
        for row in result.get("rows", []):
            pages.append({
                "url":         row["keys"][0],
                "impressions": int(row.get("impressions", 0)),
                "clicks":      int(row.get("clicks", 0)),
                "ctr":         round(row.get("ctr", 0) * 100, 2),
                "position":    round(row.get("position", 0), 1),
            })
        low_ctr_count = sum(1 for p in pages if p["impressions"] > 5000 and p["ctr"] < 2.0)
        return {
            "pages": pages,
            "low_ctr_count": low_ctr_count,
            "period": {"start_date": start.isoformat(), "end_date": end.isoformat()},
            "metric_notes": {
                "low_ctr_count": f"Count of fetched top {limit} Search Console pages with more than 5,000 impressions and less than 2% CTR.",
                "sample": f"Fetched top {limit} pages by impressions from Search Console, not a sitewide count.",
            },
        }
    except Exception as e:
        api_error(e)


@app.get("/api/gsc/queries")
def gsc_queries(days: int = 30, limit: int = 20, start_date: Optional[str] = None, end_date: Optional[str] = None):
    """Top GSC search queries. For reference only."""
    try:
        service    = get_gsc_service()
        start, end = date_range(days, start_date, end_date)
        result = service.searchanalytics().query(
            siteUrl=GSC_SITE_URL,
            body={
                "startDate": start.isoformat(), "endDate": end.isoformat(),
                "dimensions": ["query"], "rowLimit": limit,
                "orderBy": [{"fieldName": "impressions", "sortOrder": "DESCENDING"}],
            }
        ).execute()
        queries = []
        for row in result.get("rows", []):
            queries.append({
                "query":       row["keys"][0],
                "impressions": int(row.get("impressions", 0)),
                "clicks":      int(row.get("clicks", 0)),
                "ctr":         round(row.get("ctr", 0) * 100, 2),
                "position":    round(row.get("position", 0), 1),
            })
        return {"queries": queries}
    except Exception as e:
        api_error(e)


# ═══════════════════════════════════════════════════════════════════
# MAGENTO ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/magento/category-revenue")
def magento_category_revenue(days: int = 30, start_date: Optional[str] = None, end_date: Optional[str] = None):
    """Top categories by revenue for the last N days vs prior N days."""
    try:
        ctx        = period_context(days, start_date, end_date)
        start      = ctx["start"]
        end        = ctx["end"]
        prev_start = ctx["prev_start"]
        prev_end   = ctx["prev_end"]
        end_excl   = mysql_end_exclusive(end)
        prev_end_excl = mysql_end_exclusive(prev_end)

        conn = get_magento_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        ccevt.value AS name,
                        SUM(CASE WHEN so.created_at >= %s AND so.created_at < %s THEN soi.row_total ELSE 0 END) AS revenue_current,
                        SUM(CASE WHEN so.created_at >= %s AND so.created_at < %s THEN soi.row_total ELSE 0 END) AS revenue_previous
                    FROM catalog_category_entity cce
                    JOIN eav_attribute ea ON ea.entity_type_id = 3 AND ea.attribute_code = 'name'
                    JOIN catalog_category_entity_varchar ccevt
                        ON ccevt.entity_id = cce.entity_id AND ccevt.attribute_id = ea.attribute_id AND ccevt.store_id = 0
                    JOIN catalog_category_product ccp ON ccp.category_id = cce.entity_id
                    JOIN sales_order_item soi ON soi.product_id = ccp.product_id
                    JOIN sales_order so ON so.entity_id = soi.order_id AND so.state NOT IN ('canceled','closed')
                    WHERE cce.level = 2
                        AND soi.parent_item_id IS NULL
                        AND so.created_at >= %s AND so.created_at < %s
                    GROUP BY cce.entity_id, ccevt.value
                    HAVING revenue_current > 0 OR revenue_previous > 0
                    ORDER BY revenue_current DESC
                    LIMIT 25
                """, (
                    start.isoformat(), end_excl,
                    prev_start.isoformat(), prev_end_excl,
                    prev_start.isoformat(), end_excl,
                ))
                rows = cur.fetchall()
        finally:
            conn.close()

        categories = []
        for row in rows:
            curr = float(row["revenue_current"])
            prev = float(row["revenue_previous"])
            categories.append({
                "name":    row["name"],
                "revenue": round(curr, 2),
                "delta":   pct_delta(curr, prev),
            })
        return {"period": {
            "label": ctx["label"],
            "start_date": ctx["start_date"],
            "end_date": ctx["end_date"],
            "comparison": ctx["comparison"],
        }, "metric_notes": {
            "revenue": "Magento parent order-line revenue attributed to each level-2 category. Products assigned to multiple level-2 categories may appear in each category, so category revenue totals should not be added together.",
        }, "categories": categories}
    except Exception as e:
        api_error(e)


@app.get("/api/magento/top-products")
def magento_top_products(days: int = 30, start_date: Optional[str] = None, end_date: Optional[str] = None):
    """Top products by revenue for the selected dashboard period."""
    try:
        ctx = period_context(days, start_date, end_date)
        start = ctx["start"]
        end_excl = mysql_end_exclusive(ctx["end"])
        conn = get_magento_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT soi.name AS name, soi.sku AS sku, SUM(soi.row_total) AS revenue
                    FROM sales_order_item soi
                    JOIN sales_order so ON so.entity_id = soi.order_id
                        AND so.state NOT IN ('canceled','closed')
                        AND so.created_at >= %s AND so.created_at < %s
                    WHERE soi.parent_item_id IS NULL
                    GROUP BY soi.product_id, soi.name, soi.sku
                    ORDER BY revenue DESC
                    LIMIT 8
                """, (start.isoformat(), end_excl))
                rows = cur.fetchall()
        finally:
            conn.close()
        return {"period": {
            "label": ctx["label"],
            "start_date": ctx["start_date"],
            "end_date": ctx["end_date"],
        }, "products": [
            {"name": r["name"], "sku": r["sku"], "revenue": round(float(r["revenue"]), 2)}
            for r in rows
        ]}
    except Exception as e:
        api_error(e)


@app.get("/api/magento/dormant-top-sellers")
def magento_dormant_top_sellers():
    """
    Legacy calendar-month endpoint. Kept for compatibility with older dashboard
    panels; the primary merchandising workflow now uses selected-period drop-offs.

    Products that were top sellers last month but have zero orders so far this month.
    Signals potential availability, visibility, or featured placement issues.
    """
    try:
        lm_start, lm_end = lm_dates()
        lm_end_excl = mysql_end_exclusive(lm_end)
        cm_start = date.today().replace(day=1)

        conn = get_magento_conn()
        try:
            with conn.cursor() as cur:
                # Top sellers last month
                cur.execute("""
                    SELECT soi.product_id, soi.name, soi.sku, SUM(soi.row_total) AS revenue_lm
                    FROM sales_order_item soi
                    JOIN sales_order so ON so.entity_id = soi.order_id
                        AND so.state NOT IN ('canceled','closed')
                        AND so.created_at >= %s AND so.created_at < %s
                    WHERE soi.parent_item_id IS NULL
                    GROUP BY soi.product_id, soi.name, soi.sku
                    ORDER BY revenue_lm DESC
                    LIMIT 30
                """, (lm_start.isoformat(), lm_end_excl))
                top_lm = cur.fetchall()

                if not top_lm:
                    return {
                        "mode": "legacy_calendar_month",
                        "deprecated": True,
                        "period": {
                            "previous_month_start": lm_start.isoformat(),
                            "previous_month_end": lm_end.isoformat(),
                            "current_month_start": cm_start.isoformat(),
                        },
                        "products": [],
                    }

                top_ids = [r["product_id"] for r in top_lm]

                # Which of those have sold this month?
                fmt_ids = ",".join(["%s"] * len(top_ids))
                cur.execute(f"""
                    SELECT DISTINCT soi.product_id
                    FROM sales_order_item soi
                    JOIN sales_order so ON so.entity_id = soi.order_id
                        AND so.state NOT IN ('canceled','closed')
                        AND so.created_at >= %s
                    WHERE soi.product_id IN ({fmt_ids})
                """, [cm_start.isoformat()] + top_ids)
                sold_this_month = {r["product_id"] for r in cur.fetchall()}
        finally:
            conn.close()

        dormant = [
            {"name": r["name"], "sku": r["sku"], "revenue_last_month": round(float(r["revenue_lm"]), 2)}
            for r in top_lm if r["product_id"] not in sold_this_month
        ]
        return {
            "mode": "legacy_calendar_month",
            "deprecated": True,
            "replacement": "/api/magento/product-dropoffs",
            "metric_notes": {"status": "Legacy calendar-month endpoint kept for compatibility. Selected-period product drop-offs are the primary merchandising watchlist."},
            "period": {
                "previous_month_start": lm_start.isoformat(),
                "previous_month_end": lm_end.isoformat(),
                "current_month_start": cm_start.isoformat(),
            },
            "products": dormant[:10],
        }

    except Exception as e:
        api_error(e)


@app.get("/api/magento/product-dropoffs")
def magento_product_dropoffs(days: int = 30, start_date: Optional[str] = None, end_date: Optional[str] = None):
    """
    Range-aware merchandising watchlist: products that were strong in the
    prior equivalent period and declined sharply during the selected period.
    """
    try:
        ctx = period_context(days, start_date, end_date)
        start = ctx["start"]
        end_excl = mysql_end_exclusive(ctx["end"])
        prev_start = ctx["prev_start"]
        prev_end_excl = mysql_end_exclusive(ctx["prev_end"])

        conn = get_magento_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                SELECT
                    prev.product_id,
                    prev.name,
                    prev.sku,
                    prev.revenue_previous,
                    COALESCE(curr.revenue_current, 0) AS revenue_current
                FROM (
                    SELECT soi.product_id, soi.name, soi.sku, SUM(soi.row_total) AS revenue_previous
                    FROM sales_order_item soi
                    JOIN sales_order so ON so.entity_id = soi.order_id
                        AND so.state NOT IN ('canceled','closed')
                        AND so.created_at >= %s AND so.created_at < %s
                    WHERE soi.parent_item_id IS NULL
                    GROUP BY soi.product_id, soi.name, soi.sku
                    HAVING revenue_previous > 0
                    ORDER BY revenue_previous DESC
                    LIMIT %s
                ) prev
                LEFT JOIN (
                    SELECT soi.product_id, SUM(soi.row_total) AS revenue_current
                    FROM sales_order_item soi
                    JOIN sales_order so ON so.entity_id = soi.order_id
                        AND so.state NOT IN ('canceled','closed')
                        AND so.created_at >= %s AND so.created_at < %s
                    WHERE soi.parent_item_id IS NULL
                    GROUP BY soi.product_id
                ) curr ON curr.product_id = prev.product_id
                """, (prev_start.isoformat(), prev_end_excl, PRODUCT_DROPOFF_CANDIDATE_LIMIT, start.isoformat(), end_excl))
                rows = cur.fetchall()
        finally:
            conn.close()

        products = []
        for row in rows:
            prev = float(row["revenue_previous"] or 0)
            curr = float(row["revenue_current"] or 0)
            if prev <= 0:
                continue
            delta = pct_delta(curr, prev)
            drop = prev - curr
            if drop >= PRODUCT_DROPOFF_MIN_REVENUE_DROP and (curr == 0 or (delta is not None and delta <= PRODUCT_DROPOFF_MIN_DELTA)):
                products.append({
                    "name": row["name"],
                    "sku": row["sku"],
                    "previous_revenue": round(prev, 2),
                    "current_revenue": round(curr, 2),
                    "revenue_drop": round(drop, 2),
                    "delta": delta,
                })

        products.sort(key=lambda p: (p["current_revenue"] == 0, p["revenue_drop"]), reverse=True)
        return {
            "period": {
                "label": ctx["label"],
                "start_date": ctx["start_date"],
                "end_date": ctx["end_date"],
                "comparison": ctx["comparison"],
            },
            "criteria": {
                "candidate_pool": f"Top {PRODUCT_DROPOFF_CANDIDATE_LIMIT} products by prior-period Magento order-line revenue",
                "candidate_limit": PRODUCT_DROPOFF_CANDIDATE_LIMIT,
                "minimum_revenue_drop": PRODUCT_DROPOFF_MIN_REVENUE_DROP,
                "minimum_delta_percent": PRODUCT_DROPOFF_MIN_DELTA,
                "return_limit": PRODUCT_DROPOFF_RETURN_LIMIT,
                "rule": "Current-period revenue is zero, or revenue fell at least 35%, and the dollar drop is at least $250.",
            },
            "counts": {
                "candidates_evaluated": len(rows),
                "qualified": len(products),
                "returned": min(len(products), PRODUCT_DROPOFF_RETURN_LIMIT),
            },
            "products": products[:PRODUCT_DROPOFF_RETURN_LIMIT],
        }

    except Exception as e:
        api_error(e)


@app.get("/api/opportunities")
def opportunities(days: int = 30, start_date: Optional[str] = None, end_date: Optional[str] = None, period_label: Optional[str] = None):
    """
    Gathers raw signals from GSC + Magento, then asks GPT-4.1-mini
    to identify the most genuinely impactful opportunities.
    Returns varied, specific, actionable items — not a repetitive list.
    """
    try:
        ctx = period_context(days, start_date, end_date, period_label)
        max_opportunities = ai_opportunity_limit(ctx)
        count_guidance = ai_count_guidance(ctx, "opportunities", max_opportunities)
        signals = {}
        sources = {
            "gsc": {"status": "unavailable"},
            "magento_categories": {"status": "unavailable"},
            "magento_product_dropoffs": {"status": "unavailable"},
            "magento_top_products": {"status": "unavailable"},
        }

        # GSC: top pages by impressions
        try:
            pages_data = gsc_pages(days=days, limit=30, start_date=start_date, end_date=end_date)
            signals["gsc_pages"] = pages_data["pages"][:20]
            sources["gsc"] = {"status": "available", "count": len(signals["gsc_pages"])}
        except Exception:
            logger.exception("GSC pages unavailable for opportunities")
            signals["gsc_pages"] = []

        # Magento: category revenue with deltas
        try:
            signals["categories"] = magento_category_revenue(days=days, start_date=start_date, end_date=end_date)["categories"]
            sources["magento_categories"] = {"status": "available", "count": len(signals["categories"])}
        except Exception:
            logger.exception("Magento categories unavailable for opportunities")
            signals["categories"] = []

        # Magento: selected-period drop-offs vs prior equivalent period
        try:
            signals["product_dropoffs"] = magento_product_dropoffs(days=days, start_date=start_date, end_date=end_date)["products"][:6]
            sources["magento_product_dropoffs"] = {"status": "available", "count": len(signals["product_dropoffs"])}
        except Exception:
            logger.exception("Magento drop-offs unavailable for opportunities")
            signals["product_dropoffs"] = []

        # Magento: top products for the selected period
        try:
            signals["top_products"] = magento_top_products(days=days, start_date=start_date, end_date=end_date)["products"][:5]
            sources["magento_top_products"] = {"status": "available", "count": len(signals["top_products"])}
        except Exception:
            logger.exception("Magento top products unavailable for opportunities")
            signals["top_products"] = []

        prompt = f"""You are a business intelligence assistant for Mockett.com, which sells office hardware (grommets, power solutions, drawer pulls, cable management, signage hardware).

The web operations manager is a solo person — they cannot action dozens of items. Identify only the genuinely impactful opportunities from the data below. Focus on items where the gap between current performance and potential is large, specific, and actionable in a focused session.

PERIOD CONTEXT:
Opportunity count rule: Return up to {max_opportunities} opportunities. {count_guidance} The goal is to flag where a human should look, not to create a work queue.
Selected period label: {ctx['label']}
Selected period type: {ctx['type']}
Review mode: {ctx['mode']}
Selected period dates: {ctx['start_date']} to {ctx['end_date']} ({ctx['days']} days)
Comparison period: {ctx['comparison']['start_date']} to {ctx['comparison']['end_date']} ({ctx['comparison']['label']})

RAW DATA:

FETCHED TOP SEARCH CONSOLE PAGES FOR SELECTED PERIOD (impressions, clicks, CTR, position):
{json.dumps(signals['gsc_pages'], indent=2)}

MAGENTO CATEGORY REVENUE (selected period revenue, delta vs prior equivalent period):
{json.dumps(signals['categories'], indent=2)}

PRODUCT DROP-OFFS (strong prior-period sellers that declined during the selected period):
{json.dumps(signals['product_dropoffs'], indent=2)}

TOP PRODUCTS IN SELECTED PERIOD:
{json.dumps(signals['top_products'], indent=2)}

RULES:
- Respect the period context. Use "{ctx['label']}", "selected period", or the exact dates.
- Never say "last month", "this month", "month over month", or "MoM" unless the period type is calendar_month.
- For long-term trend review, avoid urgent language unless a metric is an extreme outlier.
- You are a signal detector, not an implementation planner. Flag the page, category, product group, or search behavior worth human review.
- Every opportunity must be completable as a focused review in under 2 hours.
- Use language like "review", "compare", "check", or "inspect"; avoid "rewrite all", "update every", "fix across", "roll out", or "bulk edit".
- Select only items where the data shows a clear, specific gap or anomaly
- Do NOT list every page with a low CTR — only the ones where the gap is exceptional
- Vary the opportunity types — mix SEO, merchandising, and product signals
- Write titles and descriptions in plain business English — no technical jargon
- Each description must cite the specific number that makes it an opportunity
- Each description must explain why the number matters and name the kind of review suggested
- For low-CTR page signals, recommend a focused review of the named page's search snippet and visible page copy: title tag, meta description, H1, intro copy, product/category summary, and whether the snippet matches what the page actually offers.
- Do not infer causes such as stock, visibility, appeal, or seasonality unless the provided data shows that cause
- Do not use generic language like "improve appeal", "optimize the page", or "boost performance"
- Do not recommend broad SKU cleanup, catalog rewrites, title-template work, redesigns, or tasks that imply hundreds of edits
- A low-CTR opportunity should name the specific page, category, or 1-3 item sample to review. It should not imply editing every SKU in that category.
- action must come from this list ONLY: review_search_snippet_alignment, review_low_ctr_page_copy, review_featured_products, review_related_products, review_category_navigation
- target must name the specific page, category, SKU, product, or signal being reviewed
- priority must be: high, med, or low
- icon must be one of: 📈 📄 📦 ⚠️ ⭐ 🔍
- Multiple opportunities may use the same action when they have clearly different targets. Do not repeat the same action for the same target.

Return ONLY valid JSON, no markdown:
{{"opportunities":[{{"icon":"emoji","priority":"high|med|low","title":"specific plain-English title","target":"specific page/category/SKU/product/signal","desc":"1-2 sentences with the specific number, why it matters, and the narrow review target","action":"approved_action"}}]}}"""

        oai = get_openai()
        resp = oai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "Business intelligence assistant. Return only valid JSON. Be selective — quality over quantity."},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=1800,
            temperature=0.2,
        )

        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        result = json.loads(raw)
        result["opportunities"], filter_meta = clean_ai_items_with_metadata(result.get("opportunities"), max_opportunities)
        result["generated_at"] = datetime.now().isoformat()
        result["period"] = {
            "label": ctx["label"],
            "start_date": ctx["start_date"],
            "end_date": ctx["end_date"],
            "days": ctx["days"],
            "type": ctx["type"],
            "mode": ctx["mode"],
            "comparison": ctx["comparison"],
        }
        result["sources"] = sources
        result["partial_data"] = any(s["status"] != "available" for s in sources.values())
        result["total"] = len(result.get("opportunities", []))
        result["filtered_items"] = filter_meta
        result["fallback"] = False
        return result

    except json.JSONDecodeError:
        logger.exception("OpenAI opportunities response was not valid JSON")
        return deterministic_opportunities(signals, sources, ctx, "invalid_ai_json")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("OpenAI opportunities generation failed")
        if "ctx" in locals() and "signals" in locals() and "sources" in locals():
            return deterministic_opportunities(signals, sources, ctx, "openai_unavailable")
        api_error(e)

@app.get("/api/magento/revenue-aov")
def magento_revenue_aov(days: int = 30, start_date: Optional[str] = None, end_date: Optional[str] = None):
    """
    Magento order revenue and AOV for the selected period vs prior equivalent period.
    Replaces GA4 purchaseRevenue as the headline revenue signal and powers the AOV KPI card.
    """
    try:
        ctx        = period_context(days, start_date, end_date)
        start      = ctx["start"]
        end_excl   = mysql_end_exclusive(ctx["end"])
        prev_start = ctx["prev_start"]
        prev_end_excl = mysql_end_exclusive(ctx["prev_end"])

        conn = get_magento_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        SUM(CASE WHEN created_at >= %s AND created_at < %s THEN base_grand_total ELSE 0 END) AS revenue_current,
                        SUM(CASE WHEN created_at >= %s AND created_at < %s THEN base_grand_total ELSE 0 END) AS revenue_previous,
                        COUNT(CASE WHEN created_at >= %s AND created_at < %s THEN entity_id END) AS orders_current,
                        COUNT(CASE WHEN created_at >= %s AND created_at < %s THEN entity_id END) AS orders_previous
                    FROM sales_order
                    WHERE state NOT IN ('canceled', 'closed')
                        AND created_at >= %s AND created_at < %s
                """, (
                    start.isoformat(), end_excl,
                    prev_start.isoformat(), prev_end_excl,
                    start.isoformat(), end_excl,
                    prev_start.isoformat(), prev_end_excl,
                    prev_start.isoformat(), end_excl,
                ))
                row = cur.fetchone()
        finally:
            conn.close()

        rev_curr  = float(row["revenue_current"]  or 0)
        rev_prev  = float(row["revenue_previous"] or 0)
        ord_curr  = int(row["orders_current"]     or 0)
        ord_prev  = int(row["orders_previous"]    or 0)

        aov_curr = round(rev_curr / ord_curr, 2) if ord_curr else 0
        aov_prev = round(rev_prev / ord_prev, 2) if ord_prev else 0

        return {
            "period": {
                "label":      ctx["label"],
                "start_date": ctx["start_date"],
                "end_date":   ctx["end_date"],
                "comparison": ctx["comparison"],
            },
            "current": {
                "revenue": round(rev_curr, 2),
                "orders":  ord_curr,
                "aov":     aov_curr,
            },
            "previous": {
                "revenue": rev_prev,
                "orders":  ord_prev,
                "aov":     aov_prev,
            },
            "deltas": {
                "revenue": pct_delta(rev_curr, rev_prev),
                "aov":     pct_delta(aov_curr, aov_prev),
                "orders":  pct_delta(ord_curr, ord_prev),
            },
            "metric_notes": {
                "revenue": "Magento base_grand_total for non-cancelled/closed orders. Includes tax and shipping.",
                "aov":     "Magento average order value: revenue / order count for non-cancelled/closed orders.",
            }
        }

    except Exception as e:
        api_error(e)

# ═══════════════════════════════════════════════════════════════════
# AI SUMMARY ENDPOINT
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/ai/summary")
def ai_summary(days: int = 30, start_date: Optional[str] = None, end_date: Optional[str] = None, period_label: Optional[str] = None):
    """
    Gathers live data from GA4, GSC, and Magento, then asks GPT-4.1-mini
    for a constrained executive summary using only approved action types.
    """
    try:
        ctx = period_context(days, start_date, end_date, period_label)
        max_insights = ai_summary_limit(ctx)
        count_guidance = ai_count_guidance(ctx, "executive insights", max_insights)
        metrics = {}
        sources = {
            "ga4": {"status": "unavailable"},
            "gsc": {"status": "unavailable"},
            "gsc_pages": {"status": "unavailable"},
            "magento_categories": {"status": "unavailable"},
            "magento_top_products": {"status": "unavailable"},
            "magento_product_dropoffs": {"status": "unavailable"},
        }

        try:
            metrics["ga4"] = ga4_kpis(days=days, start_date=start_date, end_date=end_date)
            sources["ga4"] = {"status": "available"}
        except Exception:
            logger.exception("GA4 unavailable for AI summary")
            metrics["ga4"] = None

        try:
            metrics["gsc"] = gsc_summary(days=days, start_date=start_date, end_date=end_date)
            sources["gsc"] = {"status": "available"}
        except Exception:
            logger.exception("GSC summary unavailable for AI summary")
            metrics["gsc"] = None

        try:
            gsc_opps = gsc_pages(days=days, limit=20, start_date=start_date, end_date=end_date)
            metrics["low_ctr_pages"] = [
                p for p in gsc_opps["pages"] if p["impressions"] > 5000 and p["ctr"] < 2.0
            ][:3]
            sources["gsc_pages"] = {"status": "available", "count": len(gsc_opps["pages"])}
        except Exception:
            logger.exception("GSC pages unavailable for AI summary")
            metrics["low_ctr_pages"] = None

        try:
            metrics["categories"] = magento_category_revenue(days=days, start_date=start_date, end_date=end_date)["categories"][:6]
            sources["magento_categories"] = {"status": "available", "count": len(metrics["categories"])}
        except Exception:
            logger.exception("Magento categories unavailable for AI summary")
            metrics["categories"] = None

        try:
            metrics["top_products"] = magento_top_products(days=days, start_date=start_date, end_date=end_date)["products"][:5]
            sources["magento_top_products"] = {"status": "available", "count": len(metrics["top_products"])}
        except Exception:
            logger.exception("Magento top products unavailable for AI summary")
            metrics["top_products"] = None

        try:
            metrics["product_dropoffs"] = magento_product_dropoffs(days=days, start_date=start_date, end_date=end_date)["products"][:5]
            sources["magento_product_dropoffs"] = {"status": "available", "count": len(metrics["product_dropoffs"])}
        except Exception:
            logger.exception("Magento product drop-offs unavailable for AI summary")
            metrics["product_dropoffs"] = None

        # Build prompt from only available data
        sections = []
        if metrics["ga4"]:
            sections.append(f"GA4 SELECTED PERIOD VS PRIOR EQUIVALENT PERIOD:\n{json.dumps(metrics['ga4'], indent=2)}")
        if metrics["gsc"]:
            sections.append(f"GOOGLE SEARCH CONSOLE SELECTED PERIOD:\n{json.dumps(metrics['gsc'], indent=2)}")
        if metrics["categories"]:
            sections.append(f"TOP CATEGORIES BY SELECTED-PERIOD REVENUE:\n{json.dumps(metrics['categories'], indent=2)}")
        if metrics["top_products"]:
            sections.append(f"TOP PRODUCTS IN SELECTED PERIOD:\n{json.dumps(metrics['top_products'], indent=2)}")
        if metrics["product_dropoffs"]:
            sections.append(f"PRODUCT DROP-OFFS VS PRIOR EQUIVALENT PERIOD:\n{json.dumps(metrics['product_dropoffs'], indent=2)}")
        if metrics["low_ctr_pages"]:
            sections.append(f"FETCHED TOP SEARCH CONSOLE PAGES WITH HIGH IMPRESSIONS BUT LOW CTR:\n{json.dumps(metrics['low_ctr_pages'], indent=2)}")

        # If no data at all, return a friendly message without calling OpenAI
        if not sections:
            return {
                "insights": [{
                    "title": "Data sources unavailable",
                    "type": "info",
                    "description": "GA4, Search Console, and Magento data could not be retrieved for this period. Please check your data source connections and try again."
                }],
                "generated_at": datetime.now().isoformat(),
                "period": {
                    "label": ctx["label"],
                    "start_date": ctx["start_date"],
                    "end_date": ctx["end_date"],
                    "days": ctx["days"],
                    "type": ctx["type"],
                    "mode": ctx["mode"],
                    "comparison": ctx["comparison"],
                },
                "data_snapshot": {"sessions": None, "revenue": None, "impressions": None, "ctr": None},
                "sources": sources,
                "partial_data": True,
                "fallback": True,
                "fallback_reason": "all_sources_unavailable",
                "filtered_items": {"input_count": 0, "returned": 1, "removed": 0, "reasons": {}},
            }

        prompt = f"""You are a business intelligence assistant for Mockett.com, which sells office hardware (grommets, power solutions, drawer pulls, cable management).

PERIOD CONTEXT:
Selected period label: {ctx['label']}
Selected period type: {ctx['type']}
Review mode: {ctx['mode']}
Selected period dates: {ctx['start_date']} to {ctx['end_date']} ({ctx['days']} days)
Comparison period: {ctx['comparison']['start_date']} to {ctx['comparison']['end_date']} ({ctx['comparison']['label']})

BUSINESS DATA:
{chr(10).join(sections)}

Generate up to {max_insights} executive insights based ONLY on the numbers above. {count_guidance}

STRICT RULES:
- Respect the period context. Use "{ctx['label']}", "selected period", or the exact dates.
- Never say "last month", "this month", "month over month", or "MoM" unless the period type is calendar_month.
- For 90-day ranges, frame insights as a quarterly trend review. For 270-day ranges, frame insights as long-term trend review and avoid urgent action language unless a metric is an extreme outlier.
- Every insight MUST cite a specific number from the data
- Plain English only — no technical terms, no jargon
- If only partial source data is available, stay within the available source numbers and do not imply a full-site read.
- Each insight must say why the metric matters and name the kind of review suggested
- Do not infer causes such as stock, visibility, appeal, or seasonality unless the provided data shows that cause
- Do not use generic language like "improve appeal", "optimize the page", or "boost performance"
- Do not recommend broad SKU cleanup, catalog rewrites, title-template work, redesigns, or tasks that imply hundreds of edits
- For low-CTR page signals, frame the action as a focused review of 1-3 named pages' search snippet and visible page copy. Do not imply SKU-wide metadata edits.
- Each suggested review must be focused enough to complete in under 2 hours
- Suggest exactly ONE action per insight from this approved list:
  review_search_snippet_alignment, review_low_ctr_page_copy, review_related_products,
  review_featured_products, review_category_navigation
- Include a target field naming the specific metric, page, category, SKU, product, or signal being reviewed.
- Multiple insights may use the same action when they have clearly different targets. Do not repeat the same action for the same target.
- type must be one of: positive, warning, info, alert

Return ONLY valid JSON, no markdown:
{{"insights":[{{"title":"string","type":"positive|warning|info|alert","target":"specific metric/page/category/SKU/product/signal","description":"2-3 sentences with specific numbers","action":"approved_action"}}]}}"""

        oai = get_openai()
        resp = oai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "Business intelligence assistant. Return only valid JSON. No markdown fences."},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=1600,
            temperature=0.25,
        )

        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        result = json.loads(raw)
        result["insights"], filter_meta = clean_ai_items_with_metadata(result.get("insights"), max_insights)
        result["generated_at"] = datetime.now().isoformat()
        result["period"] = {
            "label": ctx["label"],
            "start_date": ctx["start_date"],
            "end_date": ctx["end_date"],
            "days": ctx["days"],
            "type": ctx["type"],
            "mode": ctx["mode"],
            "comparison": ctx["comparison"],
        }
        result["data_snapshot"] = {
            "sessions":    metrics["ga4"]["current"]["sessions"] if metrics["ga4"] else None,
            "revenue":     metrics["ga4"]["current"]["revenue"]  if metrics["ga4"] else None,
            "impressions": metrics["gsc"]["impressions"]          if metrics["gsc"] else None,
            "ctr":         metrics["gsc"]["ctr"]                  if metrics["gsc"] else None,
        }
        result["sources"] = sources
        result["partial_data"] = any(s["status"] != "available" for s in sources.values())
        result["filtered_items"] = filter_meta
        result["fallback"] = False
        return result

    except json.JSONDecodeError:
        logger.exception("OpenAI summary response was not valid JSON")
        return deterministic_summary(metrics, sources, ctx, "invalid_ai_json")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("OpenAI summary generation failed")
        if "ctx" in locals() and "metrics" in locals() and "sources" in locals():
            return deterministic_summary(metrics, sources, ctx, "openai_unavailable")
        api_error(e)


# ═══════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/magento/health")
def magento_health():
    try:
        conn = get_magento_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS order_count FROM sales_order")
                row = cur.fetchone()
        finally:
            conn.close()
        return {"status": "connected", "total_orders": row["order_count"]}
    except Exception as e:
        api_error(e)

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "sources_configured": {
            "ga4": bool(GA4_PROPERTY_ID),
            "gsc": bool(GSC_SITE_URL),
            "magento": bool(os.getenv("MYSQL_HOST")),
            "openai": bool(os.getenv("OPENAI_API_KEY")),
        },
        "credential_files": {
            "ga4_service_account": bool(ga4_service_account_path and ga4_service_account_path.exists()),
            "oauth_token": OAUTH_TOKEN.exists(),
        },
    }

@app.get("/api/source-health")
def source_health():
    """
    Safe diagnostic endpoint. Returns per-source status without
    exposing secrets, tokens, or credential file contents.
    Statuses: ok | configured | unconfigured | auth_required |
              permission_denied | unavailable | error
    """
    result = {}

    # App
    result["app"] = {"status": "ok"}

    # GA4
    try:
        creds = get_ga4_credentials()
        client = BetaAnalyticsDataClient(credentials=creds)
        from google.analytics.data_v1beta.types import RunReportRequest as RRR, DateRange, Metric
        client.run_report(RRR(
            property=f"properties/{GA4_PROPERTY_ID}",
            date_ranges=[DateRange(start_date="yesterday", end_date="yesterday")],
            metrics=[Metric(name="sessions")],
        ))
        result["ga4"] = {"status": "ok"}
    except GoogleAuthError as e:
        result["ga4"] = {
            "status": "auth_required",
            "message": "Google Analytics authorization needs to be renewed.",
            "safe_detail": "OAuth refresh token expired or was revoked. Run scripts/get_token.py and redeploy credentials.",
        }
    except google.api_core.exceptions.PermissionDenied:
        result["ga4"] = {
            "status": "permission_denied",
            "message": "Service account does not have access to this GA4 property.",
            "safe_detail": f"Property ID {GA4_PROPERTY_ID} is not accessible with configured credentials.",
        }
    except Exception as e:
        result["ga4"] = {"status": "error", "safe_detail": type(e).__name__}

    # GSC
    try:
        svc = get_gsc_service()
        safe_date = (date.today() - timedelta(days=5)).isoformat()
        svc.searchanalytics().query(
            siteUrl=GSC_SITE_URL,
            body={"startDate": safe_date, "endDate": safe_date, "dimensions": []}
        ).execute()
        result["gsc"] = {"status": "ok"}
    except GoogleAuthError as e:
        result["gsc"] = {
            "status": "auth_required",
            "message": "Search Console authorization needs to be renewed.",
            "safe_detail": "OAuth refresh token expired or was revoked. Run scripts/get_token.py and redeploy credentials.",
        }
    except Exception as e:
        result["gsc"] = {"status": "error", "safe_detail": type(e).__name__}

    # Magento
    try:
        conn = get_magento_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        finally:
            conn.close()
        result["magento"] = {"status": "ok"}
    except Exception as e:
        result["magento"] = {"status": "unavailable", "safe_detail": type(e).__name__}

    # OpenAI
    if os.getenv("OPENAI_API_KEY"):
        result["openai"] = {"status": "configured"}
    else:
        result["openai"] = {"status": "unconfigured"}

    return result