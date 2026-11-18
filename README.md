# Cross-Market Product Research — Render deployable server

A read-only product research backend covering Taobao, JD, Pinduoduo, Xianyu, SMZDM, Amazon, Zhihu, Bilibili, Douyin, Xiaohongshu, TikTok, YouTube, Tieba and Reddit.

**Real access status:** does not ship with e-commerce API credentials. Official JD Union and Taobao Affiliate price APIs are **optional** and must be configured with legitimate app credentials/permissions. Without authorization, `get_product_prices` returns no live price. Public search leads are not verified prices.

## HTTPS deployment

1. Push this repository to a **new private GitHub repository**. No real access tokens should go into git.
2. In Render > New > Blueprint, select the repository. `render.yaml` defines the web service and a generated `RESEARCH_API_TOKEN` secret. Render handles HTTPS at the public `.onrender.com` URL.
3. Set `BRAVE_SEARCH_API_KEY` in Render environment for reliable public discovery. Optionally add `JD_APP_KEY`/`JD_APP_SECRET`, `TAOBAO_APP_KEY`/`TAOBAO_APP_SECRET`/`TAOBAO_ADZONE_ID` for official affiliate quotes after permission approval.
4. Verify `https://<actual-service>.onrender.com/health` and authenticated MCP endpoint `https://<actual-service>.onrender.com/mcp`.
5. Use the actual endpoint URL in the private ChatGPT plugin's `mcp.json` with API-key bearer authentication. Connect the token through ChatGPT's credential flow (do not write the secret in `mcp.json`).

The Render **free plan is best for a proof of concept**: service sleeps and SQLite data in `/tmp` is ephemeral across restarts/redeploys. Upgrade to persistent disk or external SQL database for durable price history. Browser session persistence is not implemented. The server never uses the user's daily Chrome profile.

## Local tests

```bash
python -m pip install -r requirements.txt pytest
pytest -q
RESEARCH_API_TOKEN=devtoken uvicorn server:app --host 127.0.0.1 --port 8000
```

MCP JSON-RPC over Streamable HTTP:

```bash
curl -s http://127.0.0.1:8000/mcp -H 'Authorization: Bearer devtoken' \
  -H 'Accept: application/json, text/event-stream' -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

By default `/mcp` and `/api/*` are protected by Bearer tokens. `/health` has no secrets. Service provides seven read-only MCP tools: `search_products`, `get_product_prices`, `get_product_details`, `search_reviews`, `compare_offers`, `price_history`, `source_status`. It never logs in, purchases, manipulates browser cookies, circumvents CAPTCHA or guarantees personal checkout prices.