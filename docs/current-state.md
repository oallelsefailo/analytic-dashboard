# Current State

Last updated: 2026-05-08

## Architecture

- `index.html`: single-page dashboard UI, Chart.js visualizations, navigation, date range controls, data fetching, rendering, trust states, empty/unavailable states, and AI/opportunity display.
- `app/main.py`: FastAPI backend with GA4, GSC, Magento, AI summary, opportunity, and health endpoints.
- `requirements.txt`: Python dependencies for the existing integrations.

## Final Trust And Polish Implementation

The latest implementation pass focused on final-mile executive readiness, trust, source-state honesty, AI reliability, and graceful failure handling without changing the stack or adding integrations.

Frontend changes:

- Added request lifecycle guards with request IDs, AbortController cancellation, and timeouts for dashboard, search, AI summary, and opportunities.
- Prevented stale responses from older date ranges from overwriting newer selected ranges.
- Reduced global loading after the initial dashboard load and reset stale KPI/timestamp values before retryable loads.
- Added partial source-state handling across GA4, Search Console, and Magento sub-endpoints.
- Distinguished unavailable-source states from valid empty-data states in KPI cards, SEO lists, search terms, and chart overlays.
- Added chart empty-state overlays for GA4 revenue/sessions, traffic sources, category revenue, SEO scatter, and product revenue charts.
- Clarified KPI labels for GA4 Ecommerce Revenue, Magento order-line revenue, low-CTR sampled top pages, rate deltas in percentage points, category overlap, and search-term sessions.
- Added long-value truncation and tooltips for dynamic search/product/category values.
- Added an executive signal strip using existing GA4, Search Console, and Magento metrics only.
- Replaced overconfident `Live` wording with selected-period/current/partial/unavailable copy.
- Added AI and Opportunities source coverage rows with partial-data and filtered-item metadata.

Backend changes:

- Narrowed static file serving to an optional `static/` directory instead of serving the repository root.
- Changed CORS from all origins to configurable internal/local origins via `CORS_ORIGINS`.
- Reduced unnecessary OAuth token writes and logged refresh failures instead of silently swallowing them.
- Server-derived period labels are used for AI prompts instead of trusting arbitrary frontend labels.
- Strengthened AI numeric grounding checks beyond merely requiring any digit.
- Added filtered-item metadata for AI summary and opportunity validation.
- Added deterministic non-AI fallbacks for OpenAI failures or malformed JSON.
- Removed misleading fallback action chips from all-source-unavailable AI responses.
- Added lightweight period/metric metadata to several endpoint responses.
- Marked the legacy dormant-top-sellers endpoint as deprecated in responses.
- Reduced health endpoint credential/config exposure.

## Known Notes

- No new APIs, services, packages, frameworks, databases, or tracking systems were introduced.
- The dashboard remains a focused executive operations portal, not a chatbot or GA4 replacement.
- Dormant top sellers remains available only as a deprecated legacy calendar-month endpoint; selected-period product drop-offs are the primary merchandising watchlist.
- Browser QA still depends on running the local app with real credentials and accessible source services.
