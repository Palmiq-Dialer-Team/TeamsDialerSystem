# In progress / roadmap

Current focus: small usability improvements and dashboard tools that make it easier for new users to get started and for admins to work with exports.

---

## 1. New-user / first-run experience (future)

Goal: Improve onboarding:

- Short guided tour or hints (for example, “Click here for config”, “Here for filtering”).
- Optional sample/test `.xlsx` so users can try the app without real leads.

Current status: Idea / planned; not yet implemented.

---

## 2. Dashboard export by date (future)

Goal: Add an “export by date” filter on the dashboard so admins can quickly export calls/dispositions for a specific day or date range without having to pull the full history and filter in Excel.

- Filter by a single day or a custom date range.
- Respect existing agent / disposition filters, but scoped to the selected dates.
- Keep performance reasonable for large databases (server-side date filtering, streamed export).

Current status: Idea / planned; design and API changes not yet implemented.

---

## Summary

| Area | Status | Notes |
|------|--------|-------|
| First-run tour + test xlsx | Planned | Better onboarding and example data for new users |
| Dashboard export by date | Planned | Date / date-range filters on dashboard exports |
