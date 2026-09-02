"""图结构:轮数上限路由(brief Step 2 逐字)。route_after_agent 是纯函数,不触 LLM。"""
from report_agent.chat.agent import route_after_agent


class FakeMsg:
    def __init__(self, tool_calls=None):
        self.tool_calls = tool_calls


def test_route_goes_to_tools_when_calls_and_rounds_left():
    assert route_after_agent({"messages": [FakeMsg([{"name": "x"}])], "tool_rounds": 3}, 8) == "tools"


def test_route_forces_final_at_round_limit():
    assert route_after_agent({"messages": [FakeMsg([{"name": "x"}])], "tool_rounds": 8}, 8) == "final"


def test_route_final_when_no_tool_calls():
    assert route_after_agent({"messages": [FakeMsg(None)], "tool_rounds": 0}, 8) == "final"
