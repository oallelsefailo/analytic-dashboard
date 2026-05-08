# Mockett Analytics Dashboard Agent Rules

Before making changes, review:

- README.md
- docs/project-context.md
- docs/current-state.md
- docs/agent-review-notes.md

Project Philosophy:
- executive-facing internal dashboard
- not a GA4 replacement
- not a chatbot
- focused on concise operational intelligence
- designed for a one-person web operations team

AI Rules:
- AI should summarize and detect signals
- avoid generic recommendations
- avoid large SKU cleanup suggestions
- avoid hallucinated architecture recommendations

Development Rules:
- do not introduce new APIs or services
- use existing Magento + GA4 + GSC integrations only
- preserve existing FastAPI architecture
- prioritize trust, clarity, and date-range consistency