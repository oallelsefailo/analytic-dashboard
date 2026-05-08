# Project Context

The Mockett Analytics Dashboard is an internal operational intelligence dashboard for mockett.com. It combines existing Magento 2, GA4, Google Search Console, and OpenAI integrations into a compact executive and web-operations view.

This project is not a GA4 replacement, a chatbot, or an autonomous site-management tool. Its purpose is to reduce manual review work by surfacing the most important revenue, traffic, SEO, merchandising, and AI-assisted signals in one place.

## Product Philosophy

- Executive-facing internal dashboard.
- Built for a one-person web operations workflow.
- Concise operational intelligence over exhaustive reporting.
- Trust, clarity, and date-range consistency matter more than feature breadth.
- AI should summarize and detect signals, not invent implementation plans.

## Current Data Sources

- Google Analytics 4.
- Google Search Console.
- Magento 2.
- OpenAI API.

Do not add new APIs, packages, services, databases, tracking systems, or external integrations without explicit approval.

## AI Boundaries

AI output should stay grounded in available dashboard data. Recommendations should be narrow, review-oriented, and realistically completable by one web operations person.

Avoid:

- Broad SKU cleanup.
- Catalog rewrites.
- Architecture recommendations.
- Inferred causes not supported by data.
- Large work queues.

