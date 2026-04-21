import json
import time
from typing import Any, Dict, List, Optional

import ollama
import requests
import streamlit as st

MCP_URL = "http://127.0.0.1:8000/mcp"
MODEL = "llama3.2"
REQUEST_TIMEOUT = 30


class MCPClient:
    def __init__(self, mcp_url: str) -> None:
        self.mcp_url = mcp_url
        self.session = requests.Session()
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self._request_id = 0
        self._cached_tools: Optional[List[Dict[str, Any]]] = None
        self._cached_ollama_tools: Optional[List[Dict[str, Any]]] = None

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _post(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        response = self.session.post(
            self.mcp_url,
            headers=self.headers,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        data = response.json()
        if "error" in data:
            raise RuntimeError(f"MCP error for {method}: {data['error']}")
        return data

    def initialize(self) -> Dict[str, Any]:
        return self._post(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {
                    "name": "streamlit-ollama-mcp-client",
                    "version": "1.0.0",
                },
            },
        )

    def list_tools(self, refresh: bool = False) -> List[Dict[str, Any]]:
        if self._cached_tools is not None and not refresh:
            return self._cached_tools

        data = self._post("tools/list")
        tools = data.get("result", {}).get("tools", [])
        if not isinstance(tools, list):
            raise RuntimeError(f"Unexpected tools/list response: {data}")

        self._cached_tools = tools
        self._cached_ollama_tools = self._convert_tools_to_ollama(tools)
        return tools

    def get_ollama_tools(self, refresh: bool = False) -> List[Dict[str, Any]]:
        self.list_tools(refresh=refresh)
        return self._cached_ollama_tools or []

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._post(
            "tools/call",
            {
                "name": name,
                "arguments": arguments or {},
            },
        )

    def _convert_tools_to_ollama(self, mcp_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ollama_tools: List[Dict[str, Any]] = []

        for tool in mcp_tools:
            input_schema = tool.get("inputSchema") or {
                "type": "object",
                "properties": {},
            }

            if input_schema.get("type") != "object":
                input_schema = {
                    "type": "object",
                    "properties": {},
                }

            ollama_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": input_schema,
                    },
                }
            )

        return ollama_tools


def mcp_result_to_text(tool_result: Dict[str, Any]) -> str:
    result = tool_result.get("result", {})

    if isinstance(result, str):
        return result

    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(str(item.get("text", "")))
                    else:
                        parts.append(json.dumps(item, ensure_ascii=False, indent=2))
                else:
                    parts.append(str(item))
            text = "\n".join(p for p in parts if p).strip()
            if text:
                return text

        return json.dumps(result, ensure_ascii=False, indent=2)

    return json.dumps(tool_result, ensure_ascii=False, indent=2)


def safe_content(message: Dict[str, Any]) -> str:
    value = message.get("content")
    return value if isinstance(value, str) else ""


def init_state() -> None:
    if "mcp" not in st.session_state:
        st.session_state.mcp = MCPClient(MCP_URL)
        init_result = st.session_state.mcp.initialize()
        server_info = init_result.get("result", {}).get("serverInfo", {})
        st.session_state.server_name = server_info.get("name", "unknown")
        st.session_state.server_version = server_info.get("version", "")
        st.session_state.tools = st.session_state.mcp.get_ollama_tools()

    if "messages" not in st.session_state:
        st.session_state.messages = [
            {
                "role": "system",
                "content": (
                    "You are a helpful assistant. "
                    "Use tools when useful. "
                    "When a tool is called, wait for the tool result and then answer clearly."
                    "Do not show buy hold sell. Display analyst rating, outlook and other key information."
                ),
            }
        ]

    if "tool_events" not in st.session_state:
        st.session_state.tool_events = []


def reload_tools() -> None:
    st.session_state.tools = st.session_state.mcp.get_ollama_tools(refresh=True)


def run_turn(user_input: str) -> None:
    messages: List[Dict[str, Any]] = st.session_state.messages
    mcp: MCPClient = st.session_state.mcp
    tools: List[Dict[str, Any]] = st.session_state.tools

    messages.append({"role": "user", "content": user_input})

    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.status("Thinking...", expanded=True) as status:
            try:
                response = ollama.chat(
                    model=MODEL,
                    messages=messages,
                    tools=tools,
                )
            except Exception as e:
                status.update(label="Ollama request failed", state="error")
                st.error(f"Ollama error: {e}")
                return

            assistant_message = response.get("message", {})
            tool_calls = assistant_message.get("tool_calls", []) or []
            assistant_text = safe_content(assistant_message)

            if not tool_calls:
                final_text = assistant_text or "[No text returned]"
                status.update(label="Responded", state="complete")
                st.markdown(final_text)
                messages.append({"role": "assistant", "content": final_text})
                return

            status.write(f"Model requested {len(tool_calls)} tool call(s).")
            messages.append(assistant_message)

            for tool_call in tool_calls:
                function = tool_call.get("function", {})
                tool_name = function.get("name")
                tool_args = function.get("arguments", {}) or {}

                if not tool_name:
                    tool_output_text = "Tool call was missing a function name."
                    duration = 0.0
                else:
                    start = time.time()
                    try:
                        raw_result = mcp.call_tool(tool_name, tool_args)
                        tool_output_text = mcp_result_to_text(raw_result)
                        duration = time.time() - start
                        status.write(f"Tool `{tool_name}` completed in {duration:.2f}s")
                    except Exception as e:
                        duration = time.time() - start
                        tool_output_text = f"Tool call failed for {tool_name}: {e}"
                        status.write(f"Tool `{tool_name}` failed after {duration:.2f}s")

                st.session_state.tool_events.append(
                    {
                        "name": tool_name or "unknown_tool",
                        "arguments": tool_args,
                        "duration_seconds": round(duration, 2),
                        "output": tool_output_text,
                    }
                )

                messages.append(
                    {
                        "role": "tool",
                        "name": tool_name or "unknown_tool",
                        "content": tool_output_text,
                    }
                )

            try:
                final_response = ollama.chat(
                    model=MODEL,
                    messages=messages,
                )
                final_message = final_response.get("message", {})
                final_text = safe_content(final_message) or "[No text returned]"
                status.update(label="Responded", state="complete")
                st.markdown(final_text)
                messages.append({"role": "assistant", "content": final_text})
            except Exception as e:
                status.update(label="Final response failed", state="error")
                st.error(f"Ollama final-response error: {e}")


def render_history() -> None:
    for message in st.session_state.messages:
        role = message.get("role")
        if role == "system" or role == "tool":
            continue
        with st.chat_message("assistant" if role == "assistant" else "user"):
            st.markdown(message.get("content", ""))


def main() -> None:
    st.set_page_config(page_title="MCP + Ollama Chat", page_icon="💬", layout="wide")
    init_state()

    st.title("MCP + Ollama Chat")
    st.caption(
        f"Connected to MCP server: {st.session_state.server_name} {st.session_state.server_version}".strip()
    )

    with st.sidebar:
        st.subheader("Settings")
        st.write(f"Model: `{MODEL}`")
        st.write(f"MCP URL: `{MCP_URL}`")
        st.write(f"Loaded tools: `{len(st.session_state.tools)}`")
        if st.button("Reload tools"):
            reload_tools()
            st.success(f"Reloaded {len(st.session_state.tools)} tool(s).")
        if st.button("Clear chat"):
            st.session_state.messages = [st.session_state.messages[0]]
            st.session_state.tool_events = []
            st.rerun()

        if st.session_state.tool_events:
            st.subheader("Recent tool calls")
            for event in reversed(st.session_state.tool_events[-5:]):
                with st.expander(f"{event['name']} ({event['duration_seconds']}s)"):
                    st.code(json.dumps(event["arguments"], indent=2), language="json")
                    st.text(event["output"][:4000])

    render_history()

    user_input = st.chat_input("Ask something...")
    if user_input:
        run_turn(user_input)


if __name__ == "__main__":
    main()
