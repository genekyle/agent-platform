import json
import re


def extract_json_from_mcp_text(raw_text: str) -> dict:
    fenced = re.search(r"```json\s*(\{.*\})\s*```", raw_text, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))

    bare = re.search(r"(\{.*\})", raw_text, re.DOTALL)
    if bare:
        return json.loads(bare.group(1))

    raise ValueError(f"Could not find JSON payload in MCP response:\n{raw_text}")