"""
Mockett AI Dashboard — FastAPI Backend
Run: uvicorn main:app --reload --port 8000
"""

import os, json, re
from datetime import date, timedelta, datetime
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange, Dimension, Metric, RunReportRequest, OrderBy
)
from googleapiclient.discovery import build
import pymysql
from openai import OpenAI

load_dotenv()

app = FastAPI(title="Mockett AI Dashboard API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="."), name="static")

@app.get("/")
def serve_dashboard():
    return FileResponse("index.html")


# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════

GA4_PROPERTY_ID = os.getenv("GA4_PROPERTY_ID", "312242279")
GSC_SITE_URL    = os.getenv("GSC_SITE_URL", "https://www.mockett.com/")
OAUTH_TOKEN     = "credentials/oauth-token.json"


# ═══════════════════════════════════════════════════════════════════
# AUTH & CLIENTS
# ═══════════════════════════════════════════════════════════════════

def get_credentials():
    with open(OAUTH_TOKEN) as f:
        td = json.load(f)
    creds = Credentials(
        token=td["token"], refresh_token=td["refresh_token"],
        token_uri=td["token_uri"], client_id=td["client_id"],
        client_secret=td["client_secret"], scopes=td["scopes"],
    )
    # Always refresh — token may be expired even if creds.expired is False
    # because expiry isn't stored in the JSON file
    try:
        creds.refresh(Request())
        td["token"] = creds.token
        with open(OAUTH_TOKEN, "w") as f:
            json.dump(td, f, indent=2)
    except Exception:
        pass  # If refresh fails, try with existing token
    return creds

def get_ga4_client():
    return BetaAnalyticsDataClient(credentials=get_credentials())

def get_gsc_service():
    return build("searchconsole", "v1", credentials=get_credentials())

def get_magento_conn():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT", 3306)),
        user=os.getenv("MYSQL_USER"), password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DB"), cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
    )

def get_openai():
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ═══════════════════════════════════════════════════════════════════
# SHARED HELPERS
# ═══════════════════════════════════════════════════════════════════

def date_range(days: int):
    end   = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    return start, end

def rolling_30():
    end        = date.today() - timedelta(days=1)
    start      = end - timedelta(days=29)
    prev_end   = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=29)
    return start, end, prev_start, prev_end

def pct_delta(c, p):
    if not p: return None
    return round(((c - p) / p) * 100, 1)

def fmt_rev(v):
    if v >= 1_000_000: return f"${v/1_000_000:.2f}M"
    if v >= 1_000:     return f"${v/1_000:.1f}K"
    return f"${v:.0f}"

def lm_dates():
    today    = date.today()
    lm_end   = today.replace(day=1) - timedelta(days=1)
    lm_start = lm_end.replace(day=1)
    return lm_start, lm_end


# ═══════════════════════════════════════════════════════════════════
# GA4 ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/ga4/sessions-revenue")
def ga4_sessions_revenue(days: int = 30):
    """Daily sessions + revenue. Powers Revenue & Sessions chart."""
    try:
        client     = get_ga4_client()
        start, end = date_range(days)
        resp = client.run_report(RunReportRequest(
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
        return {"labels": labels, "sessions": sessions, "revenue": revenue}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@app.get("/api/ga4/kpis")
def ga4_kpis(days: int = 30):
    """Rolling N-day KPIs vs prior N days. Powers the 4 KPI cards."""
    try:
        client     = get_ga4_client()
        end        = date.today() - timedelta(days=1)
        start      = end - timedelta(days=days - 1)
        prev_end   = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=days - 1)

        def fetch(s, e):
            req = RunReportRequest(
                property=f"properties/{GA4_PROPERTY_ID}",
                date_ranges=[DateRange(start_date=s.isoformat(), end_date=e.isoformat())],
                metrics=[
                    Metric(name="sessions"), Metric(name="purchaseRevenue"),
                    Metric(name="sessionConversionRate"), Metric(name="engagementRate"),
                ],
            )
            row = client.run_report(req).rows[0]
            return {
                "sessions":        int(row.metric_values[0].value),
                "revenue":         round(float(row.metric_values[1].value), 2),
                "conversion_rate": round(float(row.metric_values[2].value), 4),
                "engagement_rate": round(float(row.metric_values[3].value) * 100, 2),
            }

        curr = fetch(start, end)
        prev = fetch(prev_start, prev_end)
        return {
            "current":  curr,
            "previous": prev,
            "period_days": days,
            "deltas": {
                "sessions":        pct_delta(curr["sessions"],        prev["sessions"]),
                "revenue":         pct_delta(curr["revenue"],         prev["revenue"]),
                "conversion_rate": pct_delta(curr["conversion_rate"], prev["conversion_rate"]),
                "engagement_rate": pct_delta(curr["engagement_rate"], prev["engagement_rate"]),
            }
        }
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@app.get("/api/ga4/traffic-sources")
def ga4_traffic_sources(days: int = 30):
    """Session breakdown by channel group. Powers Traffic Sources donut."""
    try:
        client     = get_ga4_client()
        start, end = date_range(days)
        resp = client.run_report(RunReportRequest(
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
        return {"sources": sources, "total": total}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════
# SEARCH CONSOLE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/gsc/summary")
def gsc_summary(days: int = 30):
    """Total impressions, clicks, CTR, avg position. Powers SEO KPI cards."""
    try:
        service    = get_gsc_service()
        start, end = date_range(days)
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
        }
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@app.get("/api/gsc/pages")
def gsc_pages(days: int = 30, limit: int = 20):
    """Top pages by impressions. Powers SEO opportunity list + scatter chart."""
    try:
        service    = get_gsc_service()
        start, end = date_range(days)
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
        return {"pages": pages, "low_ctr_count": low_ctr_count}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@app.get("/api/gsc/queries")
def gsc_queries(days: int = 30, limit: int = 20):
    """Top GSC search queries. For reference only."""
    try:
        service    = get_gsc_service()
        start, end = date_range(days)
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
        raise HTTPException(500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════
# MAGENTO ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/magento/category-revenue")
def magento_category_revenue(days: int = 30):
    """Top categories by revenue for the last N days vs prior N days."""
    try:
        end        = date.today() - timedelta(days=1)
        start      = end - timedelta(days=days - 1)
        prev_end   = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=days - 1)

        conn = get_magento_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    ccevt.value AS name,
                    SUM(CASE WHEN so.created_at >= %s AND so.created_at <= %s THEN soi.row_total ELSE 0 END) AS revenue_current,
                    SUM(CASE WHEN so.created_at >= %s AND so.created_at <= %s THEN soi.row_total ELSE 0 END) AS revenue_previous
                FROM catalog_category_entity cce
                JOIN eav_attribute ea ON ea.entity_type_id = 3 AND ea.attribute_code = 'name'
                JOIN catalog_category_entity_varchar ccevt
                    ON ccevt.entity_id = cce.entity_id AND ccevt.attribute_id = ea.attribute_id AND ccevt.store_id = 0
                JOIN catalog_category_product ccp ON ccp.category_id = cce.entity_id
                JOIN sales_order_item soi ON soi.product_id = ccp.product_id
                JOIN sales_order so ON so.entity_id = soi.order_id AND so.state NOT IN ('canceled','closed')
                WHERE cce.level = 2
                GROUP BY cce.entity_id, ccevt.value
                HAVING revenue_current > 0 OR revenue_previous > 0
                ORDER BY revenue_current DESC
                LIMIT 25
            """, (start.isoformat(), end.isoformat(), prev_start.isoformat(), prev_end.isoformat()))
            rows = cur.fetchall()
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
        return {"categories": categories}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@app.get("/api/magento/top-products-last-month")
def magento_top_products_last_month(days: int = 30):
    """Top products by revenue for the last N days using soi.name and soi.sku directly."""
    try:
        end   = date.today() - timedelta(days=1)
        start = end - timedelta(days=days - 1)
        conn = get_magento_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT soi.name AS name, soi.sku AS sku, SUM(soi.row_total) AS revenue
                FROM sales_order_item soi
                JOIN sales_order so ON so.entity_id = soi.order_id
                    AND so.state NOT IN ('canceled','closed')
                    AND so.created_at >= %s AND so.created_at <= %s
                WHERE soi.parent_item_id IS NULL
                GROUP BY soi.product_id, soi.name, soi.sku
                ORDER BY revenue DESC
                LIMIT 8
            """, (start.isoformat(), end.isoformat()))
            rows = cur.fetchall()
        conn.close()
        return {"products": [
            {"name": r["name"], "sku": r["sku"], "revenue": round(float(r["revenue"]), 2)}
            for r in rows
        ]}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@app.get("/api/magento/dormant-top-sellers")
def magento_dormant_top_sellers():
    """
    Products that were top sellers last month but have zero orders so far this month.
    Signals potential availability, visibility, or featured placement issues.
    """
    try:
        lm_start, lm_end = lm_dates()
        cm_start = date.today().replace(day=1)

        conn = get_magento_conn()
        with conn.cursor() as cur:
            # Top sellers last month
            cur.execute("""
                SELECT soi.product_id, soi.name, soi.sku, SUM(soi.row_total) AS revenue_lm
                FROM sales_order_item soi
                JOIN sales_order so ON so.entity_id = soi.order_id
                    AND so.state NOT IN ('canceled','closed')
                    AND so.created_at >= %s AND so.created_at <= %s
                WHERE soi.parent_item_id IS NULL
                GROUP BY soi.product_id, soi.name, soi.sku
                ORDER BY revenue_lm DESC
                LIMIT 30
            """, (lm_start.isoformat(), lm_end.isoformat()))
            top_lm = cur.fetchall()

            if not top_lm:
                conn.close()
                return {"products": []}

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

        conn.close()

        dormant = [
            {"name": r["name"], "sku": r["sku"], "revenue_last_month": round(float(r["revenue_lm"]), 2)}
            for r in top_lm if r["product_id"] not in sold_this_month
        ]
        return {"products": dormant[:10]}

    except Exception as e:
        raise HTTPException(500, detail=str(e))


@app.get("/api/opportunities")
def opportunities(days: int = 30):
    """
    Gathers raw signals from GSC + Magento, then asks GPT-4.1-mini
    to identify the most genuinely impactful opportunities.
    Returns varied, specific, actionable items — not a repetitive list.
    """
    try:
        signals = {}

        # GSC: top pages by impressions
        try:
            pages_data = gsc_pages(days=days, limit=30)
            signals["gsc_pages"] = pages_data["pages"][:20]
        except:
            signals["gsc_pages"] = []

        # Magento: category revenue with deltas
        try:
            signals["categories"] = magento_category_revenue()["categories"]
        except:
            signals["categories"] = []

        # Magento: dormant top sellers
        try:
            signals["dormant_products"] = magento_dormant_top_sellers()["products"][:6]
        except:
            signals["dormant_products"] = []

        # Magento: top products last month for context
        try:
            signals["top_products"] = magento_top_products_last_month()["products"][:5]
        except:
            signals["top_products"] = []

        prompt = f"""You are a business intelligence assistant for Mockett.com, which sells office hardware (grommets, power solutions, drawer pulls, cable management, signage hardware).

The web operations manager is a solo person — they cannot action dozens of items. Identify the 5-7 most genuinely impactful opportunities from the data below. Focus on items where the gap between current performance and potential is large, specific, and actionable in a focused session.

RAW DATA:

GSC PAGES (impressions, clicks, CTR, position):
{json.dumps(signals['gsc_pages'], indent=2)}

MAGENTO CATEGORY REVENUE (this month vs last month delta %):
{json.dumps(signals['categories'], indent=2)}

DORMANT TOP SELLERS (strong last month, zero orders this month):
{json.dumps(signals['dormant_products'], indent=2)}

TOP PRODUCTS LAST MONTH:
{json.dumps(signals['top_products'], indent=2)}

RULES:
- Select only items where the data shows a clear, specific gap or anomaly
- Do NOT list every page with a low CTR — only the ones where the gap is exceptional
- Vary the opportunity types — mix SEO, merchandising, and product signals
- Write titles and descriptions in plain business English — no technical jargon
- Each description must cite the specific number that makes it an opportunity
- action must come from this list ONLY: review_meta_titles, review_low_ctr_pages, review_featured_products, review_related_products, review_category_navigation, review_search_synonyms
- priority must be: high, med, or low
- icon must be one of: 📈 📄 📦 ⚠️ ⭐ 🔍
- No two items should have identical action values if avoidable

Return ONLY valid JSON, no markdown:
{{"opportunities":[{{"icon":"emoji","priority":"high|med|low","title":"specific plain-English title","desc":"1-2 sentences with specific numbers","action":"approved_action"}}]}}"""

        oai = get_openai()
        resp = oai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "Business intelligence assistant. Return only valid JSON. Be selective — quality over quantity."},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=1500,
            temperature=0.2,
        )

        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        result = json.loads(raw)
        result["generated_at"] = datetime.now().isoformat()
        result["total"] = len(result.get("opportunities", []))
        return result

    except Exception as e:
        raise HTTPException(500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════
# AI SUMMARY ENDPOINT
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/ai/summary")
def ai_summary(days: int = 30):
    """
    Gathers live data from GA4, GSC, and Magento, then asks GPT-4.1-mini
    for a constrained executive summary using only approved action types.
    """
    try:
        metrics = {}

        try:
            metrics["ga4"] = ga4_kpis(days=days)
        except:
            metrics["ga4"] = None

        try:
            metrics["gsc"] = gsc_summary(days=days)
        except:
            metrics["gsc"] = None

        try:
            metrics["categories"] = magento_category_revenue(days=days)["categories"][:6]
        except:
            metrics["categories"] = None

        try:
            metrics["top_products"] = magento_top_products_last_month()["products"][:5]
        except:
            metrics["top_products"] = None

        try:
            gsc_opps = gsc_pages(days=days, limit=20)
            metrics["low_ctr_pages"] = [
                p for p in gsc_opps["pages"] if p["impressions"] > 5000 and p["ctr"] < 2.0
            ][:3]
        except:
            metrics["low_ctr_pages"] = None

        # Build prompt from only available data
        sections = []
        if metrics["ga4"]:
            sections.append(f"GA4 — LAST {days} DAYS VS PRIOR {days} DAYS:\n{json.dumps(metrics['ga4'], indent=2)}")
        if metrics["gsc"]:
            sections.append(f"GOOGLE SEARCH CONSOLE — LAST {days} DAYS:\n{json.dumps(metrics['gsc'], indent=2)}")
        if metrics["categories"]:
            sections.append(f"TOP CATEGORIES BY REVENUE:\n{json.dumps(metrics['categories'], indent=2)}")
        if metrics["top_products"]:
            sections.append(f"TOP PRODUCTS LAST MONTH:\n{json.dumps(metrics['top_products'], indent=2)}")
        if metrics["low_ctr_pages"]:
            sections.append(f"PAGES WITH HIGH IMPRESSIONS BUT LOW CTR:\n{json.dumps(metrics['low_ctr_pages'], indent=2)}")

        # If no data at all, return a friendly message without calling OpenAI
        if not sections:
            return {
                "insights": [{
                    "title": "Data sources unavailable",
                    "type": "info",
                    "description": "GA4, Search Console, and Magento data could not be retrieved for this period. Please check your data source connections and try again.",
                    "action": "review_category_navigation"
                }],
                "generated_at": datetime.now().isoformat(),
                "data_snapshot": {"sessions": None, "revenue": None, "impressions": None, "ctr": None}
            }

        prompt = f"""You are a business intelligence assistant for Mockett.com, which sells office hardware (grommets, power solutions, drawer pulls, cable management).

BUSINESS DATA:
{chr(10).join(sections)}

Generate 4 executive insights based ONLY on the numbers above.

STRICT RULES:
- Every insight MUST cite a specific number from the data
- Plain English only — no technical terms, no jargon
- Never mention missing data, tracking, null values, or system issues
- Each insight must be actionable in under 2 hours
- Suggest exactly ONE action per insight from this approved list:
  review_search_synonyms, review_zero_result_terms, review_search_redirects,
  review_meta_titles, review_low_ctr_pages, review_related_products,
  review_featured_products, review_category_navigation
- No two insights should suggest the same action
- type must be one of: positive, warning, info, alert

Return ONLY valid JSON, no markdown:
{{"insights":[{{"title":"string","type":"positive|warning|info|alert","description":"2-3 sentences with specific numbers","action":"approved_action"}}]}}"""

        oai = get_openai()
        resp = oai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "Business intelligence assistant. Return only valid JSON. No markdown fences."},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=1200,
            temperature=0.25,
        )

        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        result = json.loads(raw)
        result["generated_at"] = datetime.now().isoformat()
        result["data_snapshot"] = {
            "sessions":    metrics["ga4"]["current"]["sessions"] if metrics["ga4"] else None,
            "revenue":     metrics["ga4"]["current"]["revenue"]  if metrics["ga4"] else None,
            "impressions": metrics["gsc"]["impressions"]          if metrics["gsc"] else None,
            "ctr":         metrics["gsc"]["ctr"]                  if metrics["gsc"] else None,
        }
        return result

    except Exception as e:
        raise HTTPException(500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/magento/health")
def magento_health():
    try:
        conn = get_magento_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS order_count FROM sales_order")
            row = cur.fetchone()
        conn.close()
        return {"status": "connected", "total_orders": row["order_count"]}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@app.get("/api/health")
def health():
    return {
        "status":       "ok",
        "ga4_property": GA4_PROPERTY_ID,
        "gsc_site":     GSC_SITE_URL,
        "auth":         "oauth",
        "token_file":   OAUTH_TOKEN,
    }