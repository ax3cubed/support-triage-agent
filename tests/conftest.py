"""A scripted stand-in for the chat model, so the graph can be tested without a key or a network."""

import os

from langchain_core.messages import AIMessage

# Tests never send traces, whatever the developer has in .env.
os.environ["LANGSMITH_TRACING"] = "false"


class ScriptedModel:
    """Returns the given tool calls turn by turn and records every message list it was shown."""

    def __init__(self, *turns: list[tuple[str, dict]]):
        self.turns = list(turns)
        self.seen: list[list] = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.seen.append(list(messages))
        calls = self.turns.pop(0) if self.turns else []
        tool_calls = [{"name": name, "args": args, "id": f"call_{len(self.seen)}_{i}"} for i, (name, args) in enumerate(calls)]
        return AIMessage(content="", tool_calls=tool_calls)
