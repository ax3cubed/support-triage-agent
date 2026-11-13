"""A scripted stand-in for the chat model, so the graph can be tested without a key or a network."""

import os

from langchain_core.messages import AIMessage

# Tests never talk to LangSmith, whatever the developer has in the shell or in .env. Turning tracing
# off is not enough: `evaluate` traces anyway when it finds a key (see evals.run.run_experiment). The keys are
# set to empty and not removed, because load_dotenv would put a removed one back.
for _name in ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2"):
    os.environ[_name] = "false"
for _name in ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"):
    os.environ[_name] = ""
# If something slips past both, it fails against a closed local port and never leaves the machine.
for _name in ("LANGSMITH_ENDPOINT", "LANGCHAIN_ENDPOINT"):
    os.environ[_name] = "http://127.0.0.1:9"


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
