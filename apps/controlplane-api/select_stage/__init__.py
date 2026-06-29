"""The SELECT stage of the CDP-AX browser agent.

Decision-making only (pure logic + budget-gated Anthropic calls). Execution of
pointer paths lives in apps/mcp/app/executor — the selector never generates
trajectories, only intent + target. See select_stage/schema.py for the frozen
contract. (Package is `select_stage`, not `select` — the latter is a stdlib
module and would shadow it, breaking asyncio's selector event loop.)
"""
