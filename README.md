# Mockett Analytics Dashboard

The Mockett Analytics Dashboard is an internal operational intelligence platform for mockett.com. It gives leadership and the web operations owner a concise view of website performance, search visibility, merchandising movement, and AI-assisted review targets without requiring daily jumps between GA4, Google Search Console, Magento, and manual notes.

This is not a public open-source package, a GA4 replacement, or a chatbot. It is a focused internal dashboard built to answer one practical question quickly: how is the site doing, and what deserves attention next?

## Product Philosophy

- Executive-facing, but practical enough for daily web operations.
- Lightweight GA4-style overview portal, not enterprise BI.
- AI-assisted, not AI-driven.
- Clear source confidence, selected-period context, and metric semantics matter more than feature breadth.
- Recommendations should stay narrow, realistic, and review-oriented for a one-person web operations workflow.

## Current Dashboard Experience

The platform currently provides:

- Executive overview with GA4 revenue, sessions, conversion, engagement, Search Console signals, and Magento merchandising signals.
- Selected-period and prior-period comparison context.
- Source status indicators for GA4, Search Console, and Magento.
- Revenue/session trends, traffic source breakdowns, SEO review candidates, category trends, product revenue, and product drop-off watchlists.
- Search Intelligence based on usable GA4 on-site search-term rows.
- AI Executive Brief and focused Opportunities pages.
- Empty, unavailable, partial-data, timeout, and deterministic fallback states.
- Date presets and custom date ranges that avoid incomplete current-day analytics.

## AI Philosophy

The AI layer is intentionally constrained. It summarizes available signals, highlights narrow review targets, and reduces manual analysis time. It should never feel like the primary product, make autonomous changes, invent causes, generate broad SKU cleanup work, or create a large task queue.

AI output is expected to:

- cite specific numbers from available data;
- stay within current dashboard sources;
- disclose source coverage and partial-data states;
- recommend focused reviews that can realistically be handled by one operator;
- remain useful when OpenAI is unavailable through deterministic fallback content.

## Data Sources

The current dashboard uses the existing project sources only:

- Google Analytics 4
- Google Search Console
- Magento 2
- OpenAI API for constrained summaries and review-target selection

No new APIs, databases, tracking systems, or external services should be added without explicit approval.

## Current Maturity

As of May 8, 2026, the dashboard is a strong internal beta and near executive-rollout candidate. It now feels cohesive, polished, and operationally useful, with meaningful trust improvements around source state, date coverage, metric labels, partial failures, loading behavior, and AI fallback handling.

The remaining work is final product polish rather than architectural redesign. The highest-value next pass should focus on sharper prioritization, more executive wording, more actionable Search Intelligence, mobile/tablet QA, and calmer behavior in sparse or unavailable data states.

## Intended Use

Use this dashboard for quick operating reviews:

- leadership pulse checks;
- weekly web performance review;
- Search Console and SEO opportunity triage;
- Magento category and product movement review;
- identifying a small number of realistic next actions.

Do not treat it as accounting truth, a full analytics warehouse, an autonomous site manager, or a replacement for deeper source-system investigation when a number needs to be reconciled.

## Project Shape

- `index.html` contains the single-page dashboard UI, charts, navigation, date controls, loading states, trust states, and AI/opportunity rendering.
- `app/main.py` contains the FastAPI backend for GA4, Search Console, Magento, AI summary, opportunities, and health endpoints.
- `docs/` contains review history, current state, audit findings, and implementation handoff notes.

## Internal Use Only

This repository is intended for internal Mockett development and operational use.
