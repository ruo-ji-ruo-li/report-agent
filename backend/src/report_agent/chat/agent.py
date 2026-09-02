"""LangGraph 有界 Agent:4 工具 + 工具轮数上限 + 超限强制收敛。spec §6。

流式护栏:回答完整组装后由 sse 层跑规则护栏(spec §6.3 注明的流式折中策略)。
"""
from typing import Annotated, TypedDict

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from report_agent.chat.tools import make_tools
from report_agent.llm.prompts import load_prompt

CONVERGE_INSTRUCTION = (
    "\n\n【系统指令】工具调用轮数已达上限。基于已获取的全部信息直接作答;"
    "信息不足的部分明确说明并建议咨询医生。禁止再调用任何工具。"
)


class ChatState(TypedDict):
    messages: Annotated[list, add_messages]
    tool_rounds: int


def route_after_agent(state: ChatState, max_rounds: int) -> str:
    last = state["messages"][-1] if state["messages"] else None
    if getattr(last, "tool_calls", None) and state.get("tool_rounds", 0) < max_rounds:
        return "tools"
    return "final"


def build_chat_agent(deps, report_id: str, checkpointer=None):
    settings = deps.settings
    model = ChatOpenAI(
        model=settings.chat_model, base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key, temperature=0.1,
    )
    tools = make_tools(deps, report_id)
    system = SystemMessage(content=load_prompt("agent_system"))
    max_rounds = settings.agent_max_tool_rounds

    def agent_node(state: ChatState) -> dict:
        return {"messages": [model.bind_tools(tools).invoke([system, *state["messages"]])]}

    tool_node = ToolNode(tools)

    async def tools_node(state: ChatState) -> dict:
        # langgraph>=1 将 async 工具包装为仅异步的 StructuredTool,sync invoke 抛
        # "StructuredTool does not support sync invocation" → 必须走 ainvoke。
        result = await tool_node.ainvoke({"messages": state["messages"]})
        return {"messages": result["messages"], "tool_rounds": state.get("tool_rounds", 0) + 1}

    def final_node(state: ChatState) -> dict:
        # 评审 Important-1(Fix B):agent 已直接作答(最后消息无 tool_calls)时
        # 该消息即最终答案,直接短路返回,避免同一答案二次生成(成本翻倍 +
        # 历史拼接损坏);仅真正达限(最后消息仍带 tool_calls)才做收敛调用。
        last = state["messages"][-1] if state["messages"] else None
        if not getattr(last, "tool_calls", None):
            return {}
        return {"messages": [model.invoke(
            [SystemMessage(content=load_prompt("agent_system") + CONVERGE_INSTRUCTION),
             *state["messages"]]
        )]}

    graph = StateGraph(ChatState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_node("final", final_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent", lambda s: route_after_agent(s, max_rounds),
        {"tools": "tools", "final": "final"},
    )
    graph.add_edge("tools", "agent")
    graph.add_edge("final", END)
    return graph.compile(checkpointer=checkpointer)
