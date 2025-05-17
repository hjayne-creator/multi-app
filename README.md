# ContentApps: Flask Multi-App Suite

This is a modular Flask project containing multiple content-focused tools for personal use and experimentation.  
**Apps included:**  
- **Content Planner**: Generate strategic content plans using AI and search data.
- **Content Gaps**: Identify and analyze gaps in your content strategy.
- **Topic Competitors**: Research SEO competitors and keyword opportunities.

---

## Features

- **Flask Blueprints**: Each app is modular, with its own routes, templates, and static files.
- **Jinja2 Templates**: Server-rendered HTML with Tailwind CSS for modern styling.
- **PostgreSQL Database**: Managed via SQLAlchemy and Flask-Migrate.
- **Background Tasks**: (For Content Planner) Celery is used for long-running AI/content jobs.
- **CSRF Protection**: Enabled for all forms and AJAX requests.
- **Deployed on [Render.com](https://render.com)**

---
## CSRF Protection

- All forms and AJAX requests require a CSRF token.
- For AJAX, include the token in the `X-CSRFToken` header.
- See `apps/content_plan/static/js/main.js` for an example.

## Ignore
Ignore all files starting with z_

## Notes

- This project is for personal use and proof-of-concept (POC) purposes.
- Built and maintained by a solo (junior) developer.
- Modular structure makes it easy to add new apps/tools in the future.