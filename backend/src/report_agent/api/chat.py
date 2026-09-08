import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from report_agent.chat.agent import build_chat_agent
from report_agent.chat.sse import sse_stream
from report_agent.observability import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


class ChatIn(BaseModel):
    content: str


@router.post("/reports/{report_id}/chat/sessions")
async def create_session(report_id: str, request: Request):
    detail = await request.app.state.db_access.get_report_detail(report_id)
    if detail is None:
        raise HTTPException(404, "报告不存在")
    session_id = await request.app.state.db_access.create_session(report_id)
    return {"session_id": session_id}


@router.get("/reports/{report_id}/chat/sessions")
async def list_sessions(report_id: str, request: Request):
    """追问会话列表:该报告下有消息的会话,最近在前(前端追问面板左侧记录栏)。"""
    detail = await request.app.state.db_access.get_report_detail(report_id)
    if detail is None:
        raise HTTPException(404, "报告不存在")
    return {"sessions": await request.app.state.db_access.list_sessions(report_id)}


@router.post("/chat/sessions/{session_id}/messages")
async def send_message(session_id: str, body: ChatIn, request: Request):
    session = await request.app.state.db_access.get_session(session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    graph = build_chat_agent(request.app.state.deps, session["report_id"],
                             checkpointer=getattr(request.app.state, "chat_checkpointer", None))

    async def gen():
        async for item in sse_stream(graph, request.app.state.deps, session_id,
                                     body.content, session["report_id"]):
            # token 是纯文本增量,直接发原文(dumps 会把它变成带引号转义的 JSON 字符串,
            # 真实 smoke 中前端拼接出 "我来""帮" 的引号伪影);结构化事件仍序列化为 JSON
            data = item["data"] if item["event"] == "token" else json.dumps(
                item["data"], ensure_ascii=False
            )
            yield {"event": item["event"], "data": data}

    return EventSourceResponse(gen())


@router.get("/chat/sessions/{session_id}/history")
async def history(session_id: str, request: Request):
    if await request.app.state.db_access.get_session(session_id) is None:
        raise HTTPException(404, "会话不存在")
    return {"messages": await request.app.state.db_access.get_messages(session_id)}
