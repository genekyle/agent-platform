from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def build_server_params(browser_url: str = "http://127.0.0.1:9222") -> StdioServerParameters:
    return StdioServerParameters(
        command="npx",
        args=[
            "-y",
            "chrome-devtools-mcp@latest",
            "--browserUrl",
            browser_url,
        ],
    )


async def get_session(browser_url: str = "http://127.0.0.1:9222"):
    return stdio_client(build_server_params(browser_url))