from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def build_server_params(browser_url: str = "http://127.0.0.1:9222") -> StdioServerParameters:
    # --prefer-offline: use the npm cache and skip the registry staleness check, so a
    # capture doesn't re-hit npm every session. @latest is kept as a fallback for the
    # first-ever run; once cached, prefer-offline keeps it fully local (hotspot-friendly).
    return StdioServerParameters(
        command="npx",
        args=[
            "-y",
            "--prefer-offline",
            "chrome-devtools-mcp@latest",
            "--browserUrl",
            browser_url,
        ],
    )


async def get_session(browser_url: str = "http://127.0.0.1:9222"):
    return stdio_client(build_server_params(browser_url))