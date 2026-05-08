# Current State

Last updated: 2026-05-07

## Architecture

- `index.html`: single-page dashboard UI, Chart.js visualizations, navigation, date range controls, data fetching, rendering, empty states, and AI/opportunity display.
- `app/main.py`: FastAPI backend with GA4, GSC, Magento, AI summary, opportunity, and health endpoints.
- `requirements.txt`: Python dependencies for the existing integrations.

## Recent Trust/Correctness Pass

The latest implementation pass focused on trust, metric clarity, and safer failure states.

Backend changes:

- GA4 conversion KPI now returns `sessionConversionRate` as a displayed percentage.
- Empty GA4 KPI responses return zeroed metrics instead of crashing on missing rows.
- API errors no longer expose raw exception strings to clients.
- Magento category revenue excludes child order lines with `parent_item_id IS NULL`.
- Magento DB routes use `try/finally` connection cleanup, including health.
- AI summary and opportunities filter unsupported actions, duplicate actions, missing numeric citations, and broad-work language.
- AI responses include readable action labels and source/partial-data metadata.
- `/api/magento/dormant-top-sellers` is explicitly marked as a legacy calendar-month endpoint.

Frontend changes:

- Data source chips now show checking, loaded, unavailable, or partial states.
- The live badge now reflects partial/unavailable data instead of always claiming live status.
- Mock/fallback chart data was removed from failure paths.
- Empty states were added for unavailable or empty charts/lists.
- The missing `searchChart` JavaScript path was removed.
- Date coverage now shows selected and comparison dates.
- Manager-facing labels were cleaned up.
- Custom ranges cannot end today or in the future.
- Basic responsive breakpoints were added.
- Chart colors now adapt when toggling light/dark mode.

## Known Notes

- `docs/agent-review-notes.md` remains the detailed review and planning source.
- Dormant top sellers remains available only as a legacy calendar-month endpoint; selected-period product drop-offs are the primary merchandising watchlist.
- Browser verification was limited because the in-app browser blocked localhost, but Python compile, app import, and inline JavaScript parsing passed.

