from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def build_server_params(browser_url: str = "http://127.0.0.1:9222") -> StdioServerParameters:
    # --prefer-offline: use the npm cache and skip the registry staleness check, so a
    # capture doesn't re-hit npm every session. Pinned to 1.1.1: @latest floats and a
    # silent breaking change (1.1.1 switched take_screenshot to save-to-file + text path
    # instead of inline image) once broke all captures. Bump deliberately after testing.
    return StdioServerParameters(
        command="npx",
        args=[
            "-y",
            "--prefer-offline",
            "chrome-devtools-mcp@1.1.1",
            "--browserUrl",
            browser_url,
        ],
    )


async def get_session(browser_url: str = "http://127.0.0.1:9222"):
    return stdio_client(build_server_params(browser_url))