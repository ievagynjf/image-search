"""FastAPI endpoints for product research. HTTP API requires an API bearer token.

The service is read-only; it never attempts to log in, order, add to cart, or bypass blocks.
"""
import hmac
import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from research_bridge.engine import ResearchEngine

app = FastAPI(title='Cross-market Product Research', version='0.1.0', docs_url=None, redoc_url=None)
engine = ResearchEngine()


def require_token(authorization: str | None = Header(default=None)):
    expected = os.environ.get('RESEARCH_API_TOKEN')
    # no credential = localhost testing only; production always sets a nonempty token
    if not expected:
        raise HTTPException(503, 'RESEARCH_API_TOKEN must be configured')
    supplied = authorization.partition(' ')[2] if authorization and authorization.lower().startswith('bearer ') else ''
    if not hmac.compare_digest(expected, supplied):
        raise HTTPException(401, 'Invalid bearer token', headers={'WWW-Authenticate': 'Bearer'})


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    platforms: list[str] | None = None
    limit: int = Field(default=5, ge=1, le=12)


class DetailsRequest(BaseModel):
    url: str = Field(min_length=10, max_length=2048)


class CompareRequest(BaseModel):
    offers: list[dict[str, Any]] = Field(max_length=100)


@app.get('/health')
def health():
    return {'status': 'ready', 'product_sources': engine.source_status()}


@app.post('/api/search-products', dependencies=[Depends(require_token)])
async def products(payload: QueryRequest):
    return await engine.search_products(payload.query, payload.platforms, payload.limit)


@app.post('/api/search-reviews', dependencies=[Depends(require_token)])
async def reviews(payload: QueryRequest):
    return await engine.search_reviews(payload.query, payload.platforms, payload.limit)


@app.post('/api/prices', dependencies=[Depends(require_token)])
async def prices(payload: QueryRequest):
    return await engine.get_product_prices(payload.query, payload.platforms, payload.limit)


@app.post('/api/product-details', dependencies=[Depends(require_token)])
async def details(payload: DetailsRequest):
    return await engine.get_product_details(payload.url)


@app.post('/api/compare', dependencies=[Depends(require_token)])
def compare(payload: CompareRequest):
    return engine.compare_offers(payload.offers)


@app.get('/api/history', dependencies=[Depends(require_token)])
def history(query: str, platform: str | None = None, limit: int = 50):
    return engine.price_history(query, platform, limit)

# Password-protected stateless MCP endpoint for hosted ChatGPT clients.
from mcp_http import router as mcp_router
app.include_router(mcp_router)