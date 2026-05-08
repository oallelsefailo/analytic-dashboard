# Mockett Analytics Dashboard Agent Review Notes

Review date: 2026-05-07

Scope: review and planning only. No functional code changes were made or recommended as part of this pass.

## Project Summary

The Mockett Analytics Dashboard is a compact internal analytics portal for mockett.com. It combines existing Magento 2, GA4, Google Search Console, and OpenAI integrations into a lightweight executive and web-operations view. It is not intended to replace GA4; it should make the most important revenue, traffic, SEO, merchandising, and AI-assisted signals easier to review quickly.

Current architecture is intentionally small:

- `index.html`: single-page dashboard UI, Chart.js visualizations, date range picker, navigation, data fetching, and rendering logic.
- `app/main.py`: FastAPI backend with GA4, GSC, Magento, AI summary, opportunity, and health endpoints.
- `requirements.txt`: existing Python dependencies only.

## Current Strengths

- The overall product concept is strong: it reduces the need to jump between GA4, Search Console, Magento, and manual notes.
- The dashboard shell already feels like an internal executive tool: sidebar navigation, KPI cards, source tags, panels, and restrained dark UI give it a credible foundation.
- KPI tooltips are helpful and manager-friendly in several areas, especially sessions, engagement, impressions, CTR, and position.
- The date range model is mostly centralized in `date_range()` and `period_context()`, and preset/custom ranges are generally passed through to GA4, GSC, Magento, AI summary, and opportunities.
- Product drop-offs are operationally useful because they compare the selected period against the prior equivalent period and cap the output to a manageable list.
- The AI prompt philosophy is aligned with the product premise: it tells the model to cite numbers, avoid broad SKU cleanup, avoid inferred causes, use plain English, and keep work review-oriented.
- Opportunities are separated from the executive overview, which helps avoid overwhelming the primary dashboard.

## Current Weaknesses

- The largest trust issue is that the UI can still look live when data is missing or fallback/mock data is being shown. The source chips and Live badge are hardcoded as connected.
- Some default markup contains stale sample values and deltas that may remain visible if a request fails or if only part of a request succeeds.
- Several labels are still too technical or internal-facing for managers, such as `GA4 searchTerm`, `Case-grouped`, `Blank Rows Filtered`, `Low CTR`, `GPT-4.1-mini`, and raw AI action IDs.
- Date range labels say `Last 30 Days` while backend presets end yesterday. That is probably correct for stable analytics data, but it needs visible "through [date]" context.
- The dashboard is desktop-first. There are no meaningful responsive breakpoints for KPI grids, two-column layouts, sidebar, or topbar controls.
- The frontend still has leftover or incomplete paths, especially a missing `searchChart` canvas referenced by JavaScript and a hidden legacy search placeholder.
- Empty states are uneven. Some panels show good messages, while others can leave blank charts, old values, or only table headers.

## Suggested Improvements

- Make data trust explicit. Show whether GA4, GSC, Magento, and AI loaded successfully for the selected range, and clearly mark unavailable or fallback states.
- Make date coverage explicit. Show actual start/end dates or "through yesterday" context on presets, charts, and AI panels.
- Align every KPI label with the exact metric source and calculation. Distinguish GA4 revenue from Magento line-item revenue.
- Replace technical labels with business-facing wording while preserving source details in tooltips or small tags.
- Remove or finish leftover UI code before adding new features.
- Add consistent empty states for no data, partial data, backend unavailable, and AI unavailable.
- Add lightweight AI response validation before returning model output to the frontend.
- Keep recommendations narrow, review-oriented, and limited to signals from current data sources only.

## Agent 1: Executive UX Reviewer

### What Is Strong

- The dashboard already has a polished executive shell.
- KPI cards, charts, source tags, and panel hierarchy create a professional quick-review experience.
- Tooltips explain several business concepts in plain language.
- The executive premise is sound: the dashboard answers "how is the site doing?" faster than opening each source separately.

### What Feels Unfinished Or Less Professional

- Hardcoded Live/source-connected UI can undermine trust if data calls fail.
- Date labels do not show the exact covered dates.
- Some labels expose implementation details instead of business meaning.
- The Search Intelligence page feels partially unfinished because JavaScript references a chart canvas that does not exist.
- SEO KPI deltas are hardcoded in markup and not refreshed with live GSC data.
- The layout may not hold up on smaller screens.

### Executive Recommendations

- Show exact selected dates and comparison dates.
- Change manager-facing labels:
  - `GA4 searchTerm` -> `Searches with Terms`
  - `Case-grouped` -> `Grouped Similar Searches`
  - `Blank Rows Filtered` -> `Filtered Empty Searches`
  - `Low CTR Pages` -> `Low Click-Rate Pages`
- Hide the model name from the primary executive UI; keep it only in diagnostics if needed.
- Add a visible status state when any data source fails.

## Agent 2: Web Operations Reviewer

### What Is Strong

- The dashboard should reduce solo web-ops workload by combining KPI review, SEO pages, Magento categories/products, search terms, product drop-offs, AI summary, and opportunities.
- The opportunity concept is appropriately capped and review-oriented.
- The AI prompts already discourage broad SKU cleanup, catalog rewrites, inferred causes, and huge work queues.
- Product drop-offs are realistic because they surface a small list of products that changed materially.

### Operational Risks

- AI constraints are prompt-only. There is no post-processing to enforce action allowlists, max counts, duplicate actions, required numbers, or banned broad-work language.
- The opportunities prompt asks for `5-7` items but also says `up to 5`, which weakens selectivity.
- The AI summary asks for exactly four insights, which can create filler when signals are weak.
- Partial source failures are swallowed before AI prompting, so AI can generate "valid" output from incomplete data.

### Web-Ops Recommendations

- Let AI return fewer insights or opportunities when the signal is weak.
- Validate AI output server-side before returning it.
- Label partial data clearly so the web-ops person knows whether an item is worth trusting.
- Keep opportunity actions narrow and under two hours.

## Agent 3: Data Logic Reviewer

### Backend/Data Findings

- Conversion KPI likely has a unit mismatch. `sessionConversionRate` is returned as a decimal in `app/main.py`, while the frontend appends `%`. Engagement is multiplied by 100, but conversion is not. The UI label `Orders per 100 Visitors` also implies order conversion, while GA4 `sessionConversionRate` may include configured conversions beyond purchases.
- Magento category revenue has double-count risk. The category query joins `catalog_category_product` to `sales_order_item` and sums `row_total` without `parent_item_id IS NULL`, unlike the top-products and drop-off queries. Parent/child items and multi-category assignment can inflate category totals.
- `Low CTR Pages` count is calculated only across the fetched page limit, not the whole site. The KPI reads like a sitewide count.
- GA4 revenue and Magento revenue appear in the same dashboard but need clearer visible source labeling.
- Search `Top Search` can show a stale value when a new range has no top term.
- Some frontend defaults can remain stale when individual responses fail.

### Date-Range Consistency Findings

- Preset ranges are rolling windows ending yesterday. This is a sensible analytics default but needs clearer UI text.
- Custom ranges are sent through as `start_date` and `end_date` and are generally respected by backend endpoints.
- Comparison periods are generally prior equivalent periods, which is consistent with the rolling-day preset model.
- The UI does not currently expose true calendar `last month`, `this month`, or `quarter` presets. The backend AI prompt mentions `calendar_month`, but no period type currently appears to set it.
- The dormant top sellers endpoint still uses calendar last month/this month semantics and is separate from the selected-range model. It appears less aligned with the current dashboard direction.

## Agent 4: AI Prompt Reviewer

### AI Prompt Findings

- The AI receives selected period context, dates, and prior equivalent comparison dates. Range plumbing is mostly correct.
- The prompts correctly say AI is a signal detector, not an implementation planner.
- The prompts require specific numbers and "why it matters" explanations.
- The prompts constrain broad or unrealistic work, which is appropriate for a solo web-ops workflow.

### AI Risks

- `Generate 4 executive insights` can force filler.
- `Never mention missing data` can cause overconfident output when only partial source data was available.
- Some allowed actions are unsupported by the data currently passed into the prompt, such as zero-result terms, search redirects, and synonyms.
- Prompt rules are not enforced after generation.
- 7/14/30/90/270 day ranges need more differentiated wording:
  - 7/14 days: short pulse, avoid trend certainty.
  - 30 days: operational review.
  - 90 days: quarterly trend review.
  - 270 days: durable pattern review, avoid urgent language unless extreme.
- AI summary renders raw action IDs, unlike opportunities which map actions to readable labels.

### AI Recommendations

- Change summary output to "up to 4 insights; fewer if signals are weak."
- Remove unsupported action types unless the existing data payload supports them.
- Add server-side validation for action IDs, count, duplicate actions, missing numeric citations, and broad-work phrasing.
- Add source availability metadata to AI responses so the frontend can show partial-data context.

## Agent 5: Frontend/UI Reviewer

### Frontend/UI Findings

- `buildSeoChart()` assumes every page has a `url`, but fallback calls pass objects without `url`. If GSC/backend fails, fallback rendering can throw and leave the dashboard half-rendered.
- `buildSearchChart()` references `searchChart`, but no matching canvas exists in the visible markup.
- Responsive behavior is likely weak because grids are fixed and only print-specific media rules exist.
- Light mode does not fully retheme charts because chart colors are hardcoded for dark mode and `toggleTheme()` only calls `chart.update()`.
- Empty states are uneven:
  - SEO list can render headers with no rows and no message.
  - Empty traffic/category/product data can leave stale donut center text, blank charts, or loading KPIs.
- Several labels look stale or placeholder-like:
  - Data source chips always say connected.
  - Merch chart title says revenue but canvas text mentions viewed-not-purchased.
  - Hidden legacy search placeholder remains in markup.
- Long dynamic KPI values, such as top search terms and top SKUs, can overflow cards.

## Agent 6: Backend/Maintainability Reviewer

### Security/Maintainability Findings

- AI routes swallow upstream GSC/Magento failures and replace them with empty data without logging or source-status metadata.
- Empty GA4 KPI responses can 500 because `rows[0]` is assumed to exist.
- Magento database connections are closed only on the success path. Query failures can leak connections.
- `api_error()` returns raw exception strings to clients and does not log structured diagnostics.
- Date logic is partly centralized, but unused `rolling_30()`, `lm_dates()`, and `dormant-top-sellers` keep old calendar-month patterns in the code.
- Routes, service calls, SQL, formatting, prompt construction, and error policy all live in one file. It is manageable now, but future fixes will be easy to miss.
- CORS allows all origins. That may be acceptable for local/internal development, but it should be reviewed before wider internal deployment.
- The OAuth token refresh writes back to `credentials/oauth-token.json`; failures are silently ignored.

## Agent 7: QA Reviewer

### QA Findings

- The app should be tested across all date ranges: 7, 14, 30, 90, 270, and custom.
- Refresh behavior should be tested on every page. Current navigation appears in-memory only, so refreshing likely returns the executive overview rather than preserving the active page.
- AI summary and opportunities should be tested on every range, especially short and long windows.
- Search Intelligence needs explicit testing for empty terms, failed GA4 search term response, and long top terms.
- SEO needs testing for zero low-CTR results and for GSC failures.
- Merchandising needs testing for empty category/product/drop-off data and very long SKU/product names.
- Browser console should be checked for the missing `searchChart` canvas and fallback `buildSeoChart()` URL errors.
- Endpoint 500 risk should be checked for empty GA4 responses, invalid custom dates, no Magento rows, and partial credential/API failures.

### Suggested QA Matrix

- Date ranges: each preset plus one valid custom range, one future custom range, one start-after-end range, and one over-500-day range.
- Pages: overview, search, SEO, merchandising, AI summary, opportunities.
- Refresh: refresh while on each page and confirm expected behavior.
- Data states: all sources healthy, GA4 unavailable, GSC unavailable, Magento unavailable, OpenAI unavailable, empty selected period.
- Console: no errors during initial load, page switch, date switch, AI timeout, and fallback.
- Labels: no stale `Last 30 Days`, old SEO deltas, hidden placeholder leakage, or raw action IDs.

## Backend/Data Findings

- Verify conversion rate unit and label before relying on the KPI.
- Adjust category revenue semantics or label it carefully as category-attributed line revenue, not total category revenue.
- Keep GA4 revenue and Magento revenue visibly distinct.
- Make low-CTR counts and thresholds consistent between KPI, list, backend, and AI.
- Return consistent `period` objects where possible across endpoints.
- Consider returning source status metadata for endpoint groups and AI routes.

## Frontend/UI Findings

- Add real data-source and fallback states.
- Remove or complete the missing search chart path.
- Replace hardcoded sample/fallback values with clear unavailable states.
- Add responsive layouts for common laptop/tablet widths.
- Improve light-mode chart colors.
- Add empty states for every list/chart.
- Map AI summary action IDs to readable labels.
- Guard long KPI values with truncation or compact display rules.

## AI Prompt Findings

- Prompt quality is directionally good and aligned with the premise.
- The next pass should focus less on stronger prose and more on reliability:
  - fewer forced items
  - range-specific style rules
  - post-generation validation
  - source availability context
  - supported action lists only

## Date-Range Consistency Findings

- Rolling presets are consistent but need visible actual-date labels.
- Custom range selection needs clearer guardrails against future/incomplete ranges.
- AI and opportunity endpoints mostly receive the selected range correctly.
- Calendar-month language should stay out of AI responses unless calendar-month period support is explicitly added.
- Dormant top sellers should either stay separate with a clear calendar label or be replaced by the selected-period product drop-off model.

## Security/Maintainability Findings

- Do not expose raw backend exception strings in production-facing responses.
- Add structured logging around source, endpoint, selected range, and upstream failure.
- Close DB connections with `try/finally` or context-managed connection helpers.
- Avoid silent credential refresh failures where possible.
- Review open CORS before broader deployment.
- Keep credentials and `.env` untouched.
- Avoid adding any new services, APIs, databases, packages, or tracking systems.

## Recommended Next Development Team

- Data/backend engineer: metric semantics, endpoint shapes, source-status metadata, safe empty handling, DB connection cleanup.
- Frontend/UI engineer: visible trust states, responsive fixes, label cleanup, empty/loading states, stale fallback cleanup.
- AI prompt engineer: prompt count/style updates, action allowlist cleanup, response validation rules.
- QA engineer: date-range/page/source-failure regression pass.

## Recommended Implementation Order

1. Fix trust and correctness blockers:
   - conversion rate unit/label
   - Magento category revenue double-count risk or label
   - hardcoded Live/source-connected states
   - mock/fallback data visibility
   - missing `searchChart`/fallback console errors

2. Normalize date and comparison context:
   - show actual selected dates
   - show prior comparison dates
   - make preset labels clear that they end yesterday
   - keep calendar-month language out unless explicitly supported

3. Improve endpoint reliability:
   - safe empty GA4 rows
   - DB cleanup on error
   - structured source failure metadata
   - less raw exception exposure

4. Polish frontend trust and readability:
   - business-friendly labels
   - consistent empty states
   - stale default value cleanup
   - readable AI action chips
   - responsive breakpoints
   - light-mode chart colors

5. Tighten AI output:
   - "up to" counts
   - range-specific prompt style
   - remove unsupported actions
   - validate model JSON before returning

6. Run QA matrix:
   - all date ranges
   - all pages
   - refresh behavior
   - partial data failures
   - browser console
   - endpoint errors

## Exact Agents Needed For The Next Implementation Pass

- Agent A: Data Semantics Fixer
  - Owns conversion rate, Magento revenue semantics, low-CTR count semantics, date period metadata, and endpoint response consistency.

- Agent B: Backend Reliability Fixer
  - Owns safe empty responses, DB connection cleanup, structured logging/error handling, source availability metadata, and AI route partial-data handling.

- Agent C: Frontend Trust/UI Fixer
  - Owns live/fallback/source states, stale labels, missing search chart path, empty states, responsive layout, long KPI text, and light-mode chart contrast.

- Agent D: AI Output Guardrails Fixer
  - Owns AI prompt count wording, action allowlists, range-specific prompt style, post-generation validation, and readable action labels.

- Agent E: QA Regression Agent
  - Owns manual/browser test matrix, console checks, endpoint smoke tests, date-range switching, refresh behavior, and partial-failure scenarios.

## Do Not Implement Yet

This pass intentionally stops at documentation. The next implementation prompt should ask Codex to make a scoped fix pass, starting with trust/correctness blockers and avoiding new integrations.

## Top 5 Recommended Improvements

1. Make data trust visible: source status, fallback state, and partial data warnings.
2. Fix metric semantics: conversion rate unit/label, Magento category revenue attribution, GA4 vs Magento revenue labeling, and low-CTR count scope.
3. Make date ranges explicit: selected dates, comparison dates, and "through yesterday" context.
4. Clean frontend reliability issues: missing `searchChart`, fallback `seoChart` URL error, stale hardcoded values, and uneven empty states.
5. Tighten AI reliability: return fewer items when signals are weak, remove unsupported actions, validate model output, and map action IDs to readable labels.

## Suggested Next Codex Prompt Focus

Ask for an implementation pass that fixes the top trust and correctness blockers without adding integrations or packages:

> Implement the first phase of the Mockett dashboard review notes. Focus only on trust/correctness fixes: visible data-source/fallback states, date coverage labels, conversion KPI semantics, Magento category revenue labeling or query safety, low-CTR threshold consistency, missing searchChart/fallback console errors, and AI output count/action validation. Do not add APIs, packages, services, databases, tracking systems, or new external integrations.

## Safe To Continue?

Yes. The project is safe to continue from the current state as a review-first internal dashboard. The next pass should prioritize trust, metric clarity, and error-state honesty before expanding features.
