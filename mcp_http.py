"""Stateless MCP Streamable HTTP endpoint for read-only product research.

This endpoint speaks JSON-RPC 2.0 over /mcp, answers over application/json,
and authenticates every request using RESEARCH_API_TOKEN. It never handles
browser cookies, account login, payment, or user profile data.
"""
from __future__ import annotations

import hmac
import json
import os
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from research_bridge.engine import ResearchEngine

router = APIRouter()
engine = ResearchEngine()
_rate: dict[str, deque[float]] = defaultdict(deque)
MAX_BYTES = 32_768
RATE_LIMIT = 60  # Per originating network address, per minute, per process.
SUPPORTED_VERSIONS = {'2025-03-26', '2025-06-18', '2025-11-25'}

TOOL_DESCRIPTIONS: dict[str, dict[str, Any]] = {
    'search_products': {
        'description': 'Discover public product URLs. Search results are not verified prices.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': {'type':'string'}, 'platforms': {'type':'array','items':{'type':'string'}},
            'limit': {'type':'integer','minimum':1,'maximum':12}}, 'required':['query']},
    },
    'get_product_prices': {
        'description': 'Fetch official affiliate API quotes when authorized, or return unverified discovery links.',
        'inputSchema': {'type':'object','properties': {
            'query': {'type':'string'}, 'platforms': {'type':'array','items':{'type':'string'}},
            'limit': {'type':'integer','minimum':1,'maximum':12}}, 'required':['query']},
    },
    'get_product_details': {
        'description':'Fetch public product page metadata from an HTTPS retail domain allowlist.',
        'inputSchema': {'type':'object','properties': {'url':{'type':'string','format':'uri'}},'required':['url']},
    },
    'search_reviews': {
        'description':'Discover public media and community reviews across the approved sites.',
        'inputSchema': {'type':'object','properties': {
            'query': {'type':'string'}, 'platforms': {'type':'array','items':{'type':'string'}},
            'limit': {'type':'integer','minimum':1,'maximum':12}}, 'required':['query']},
    },
    'compare_offers': {
        'description':'Compare provided verified price observations without silently mixing unknown SKU/currency.',
        'inputSchema': {'type':'object','properties': {'offers': {'type':'array','items':{'type':'object'},'maxItems':100}},'required':['offers']},
    },
    'price_history': {
        'description':'Read observations previously stored in this service. Free instances may have ephemeral storage.',
        'inputSchema': {'type':'object','properties': {
            'query': {'type':'string'}, 'platform': {'type':'string'},
            'limit': {'type':'integer','minimum':1,'maximum':100}},'required':['query']},
    },
    'source_status': {
        'description':'Report configured platform adapters without exposing credentials.',
        'inputSchema': {'type':'object','properties':{}},
    },
}


def rpc_result(request_id: Any, value: Any) -> JSONResponse:
    return JSONResponse({'jsonrpc':'2.0', 'id': request_id, 'result':value},
                       headers={'Cache-Control':'no-store'})


def rpc_error(request_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({'jsonrpc':'2.0','id':request_id,
                         'error':{'code':code,'message':message}},
                        headers={'Cache-Control':'no-store'})


def ensure_auth(authorization: str | None) -> None:
    secret = os.environ.get('RESEARCH_API_TOKEN')
    if not secret:
        raise HTTPException(503, detail='RESEARCH_API_TOKEN is missing')
    supplied = authorization[7:] if authorization and authorization.lower().startswith('bearer ') else ''
    if not hmac.compare_digest(secret, supplied):
        raise HTTPException(401, detail='Unauthorized', headers={'WWW-Authenticate':'Bearer'})


def throttle(request: Request) -> None:
    address = request.client.host if request.client else 'unknown'
    times = _rate[address]
    current = time.monotonic()
    while times and times[0] <= current-60:
        times.popleft()
    if len(times) >= RATE_LIMIT:
        raise HTTPException(429, detail='Rate limit exceeded')
    times.append(current)


async def invoke_tool(name: str, arguments: dict[str, Any]) -> Any:
    if name == 'source_status': return engine.source_status()
    if name == 'search_products': return await engine.search_products(arguments['query'], arguments.get('platforms'), arguments.get('limit',5))
    if name == 'search_reviews': return await engine.search_reviews(arguments['query'], arguments.get('platforms'), arguments.get('limit',4))
    if name == 'get_product_prices': return await engine.get_product_prices(arguments['query'], arguments.get('platforms'), arguments.get('limit',5))
    if name == 'get_product_details': return await engine.get_product_details(arguments['url'])
    if name == 'compare_offers': return engine.compare_offers(arguments['offers'])
    if name == 'price_history': return engine.price_history(arguments['query'], arguments.get('platform'), arguments.get('limit',50))
    raise KeyError(name)


@router.get('/mcp')
async def mcp_get():
    return JSONResponse({'error':'This stateless MCP endpoint supports POST only.'}, status_code=405)


@router.post('/mcp')
async def mcp_post(request: Request, authorization: str | None = Header(default=None)):
    ensure_auth(authorization)
    throttle(request)
    if int(request.headers.get('content-length') or '0') > MAX_BYTES:
        raise HTTPException(413, detail='Request exceeds 32 KiB')
    raw = await request.body()
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, detail='Request exceeds 32 KiB')
    try:
        message = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return rpc_error(None, -32700, 'Parse error')
    if not isinstance(message,dict):
        return rpc_error(None,-32600,'Invalid Request')
    method = message.get('method')
    request_id = message.get('id')
    if message.get('jsonrpc') != '2.0' or not isinstance(method,str):
        return rpc_error(request_id,-32600,'Invalid Request')
    if method.startswith('notifications/'):
        return Response(status_code=202)
    params=message.get('params') or {}
    if not isinstance(params,dict):
        return rpc_error(request_id,-32602,'Invalid params')
    if method=='initialize':
        requested=params.get('protocolVersion')
        version=requested if requested in SUPPORTED_VERSIONS else '2025-06-18'
        return rpc_result(request_id,{'protocolVersion':version,
            'capabilities':{'tools':{'listChanged':False}},
            'serverInfo':{'name':'cross-market-research','version':'0.2.0'},
            'instructions':'Read-only product research; official affiliate prices require credentials; never claim personal checkout verification.'})
    if method=='ping':return rpc_result(request_id,{})
    if method=='tools/list':
        return rpc_result(request_id,{'tools':[
            {'name':name, **data} for name,data in TOOL_DESCRIPTIONS.items()]})
    if method=='tools/call':
        name=params.get('name'); arguments=params.get('arguments',{})
        if name not in TOOL_DESCRIPTIONS:
            return rpc_error(request_id,-32602,'Unknown tool')
        if not isinstance(arguments,dict):
            return rpc_error(request_id,-32602,'Invalid tool arguments')
        try:
            result=await invoke_tool(name,arguments)
            return rpc_result(request_id,{'content':[{'type':'text',
                'text':json.dumps(result,ensure_ascii=False,default=str)}],
                'isError':False})
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            return rpc_result(request_id,{'content':[{'type':'text',
                'text':'Invalid parameters: '+str(exc)[:240]}],'isError':True})
        except Exception as exc:
            return rpc_result(request_id,{'content':[{'type':'text',
                'text':'Source execution failed: '+type(exc).__name__}],'isError':True})
    return rpc_error(request_id,-32601,'Method not found')