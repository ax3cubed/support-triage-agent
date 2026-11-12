"""A scripted stand-in for the chat model, so the graph can be tested without a key or a network."""

import os

from langchain_core.messages import AIMessage

# Tests never send traces, whatever the developer has in .env.
os.environ["LANGSMITH_TRACING"] = "false"


class ScriptedModel:
    """Returns the given turns in order and records every message list it was shown.

    A turn is a list of (tool name, arguments) calls, a string for a reply in plain words,
    or an exception to raise in place of answering.
    """

    def __init__(self, *turns):
        self.turns = list(turns)
        self.seen: list[list] = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.seen.append(list(messages))
        turn = self.turns.pop(0) if self.turns else []
        if isinstance(turn, Exception):
            raise turn
        if isinstance(turn, str):
            return AIMessage(content=turn)
        calls = [{"name": name, "args": args, "id": f"call_{len(self.seen)}_{i}"} for i, (name, args) in enumerate(turn)]
        return AIMessage(content="", tool_calls=calls)
