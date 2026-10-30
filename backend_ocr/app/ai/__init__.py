"""Shared LLM plumbing — the client, the cost ledger and the budget gate.

Before this existed there were nine independent `openai.chat.completions.create`
call sites, each constructing its own client, describing its JSON shape in prose,
validating the result by hand, and retrying nothing. None of them counted a
token. This package is the one place any of that happens.
"""
from .llm import LLMError, LLMRefused, LLMResult, complete_json   # noqa: F401
