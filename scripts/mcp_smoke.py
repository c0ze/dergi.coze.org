"""Call every tool of the dergi.coze.org MCP server with the official MCP client and check the
answers. Usage: mcp_smoke.py [url]   (default https://dergi.coze.org/mcp)"""

import asyncio
import json
import sys

from mcp.client.client import Client

URL = sys.argv[1] if len(sys.argv) > 1 else "https://dergi.coze.org/mcp"


async def main() -> None:
    async with Client(URL) as c:
        print("server:", c.server_info.name, "| protocol", c.protocol_version)
        tools = (await c.list_tools()).tools
        print("tools:", ", ".join(t.name for t in tools))
        assert all(t.annotations and t.annotations.read_only_hint for t in tools), "every tool is read-only"

        async def call(name: str, **args) -> dict:
            r = await c.call_tool(name, args)
            assert not r.is_error, f"{name} failed: {r.content}"
            data = r.structured_content or json.loads(r.content[0].text)
            return data

        r = await call("search", query="makine dili kursu", limit=5)
        print("search:", [(x["title"][:30], x["magazine"], x["issue"], x["matched_by"]) for x in r["results"]])
        assert r["results"]
        r2 = await call("search", query="Turrican", magazine="64ler", limit=3)
        print("search in 64'ler:", [(x["title"][:30], x["issue"]) for x in r2["results"]])
        a = await call("get_article", url=r["results"][0]["url"], max_chars=1000)
        print("get_article:", a["title"], a["author"], a["pages"], a["total_chars"], "chars, next", a["next_offset"])
        assert a["text"]
        m = await call("list_magazines")
        print("list_magazines:", [x["name"] for x in m["magazines"]])
        li = await call("list_issues", magazine="Amiga Dünyası")
        print("list_issues:", len(li["issues"]), "issues of", li["magazine"])
        i = await call("get_issue", magazine="c64 turkiye", number=7)
        print("get_issue:", i["magazine_name"], i["number"], len(i["articles"]), "articles,", len(i["programs"]), "programs")
        au = await call("find_authors", query="kiper")
        print("find_authors:", [(x["name"], x["articles"]) for x in au["authors"]])
        ga = await call("get_author", author=au["authors"][0]["slug"], limit=3)
        print("get_author:", ga["name"], ga["total"], "articles")
        fg = await call("find_games", query="turrican")
        print("find_games:", [(x["name"], x["articles"]) for x in fg["games"]])
        gg = await call("get_game", game=fg["games"][0]["slug"])
        print("get_game:", gg["name"], {p: len(v) for p, v in gg["platforms"].items()})
        lt = await call("list_topics")
        print("list_topics:", len(lt["topics"]), "topics")
        gt = await call("get_topic", topic="demo", limit=3)
        print("get_topic:", gt["name"], gt["total"], "articles")
        bad = await c.call_tool("get_issue", {"magazine": "nope", "number": 1})
        print("bad magazine gives an error:", bad.is_error, bad.content[0].text[:80])
        assert bad.is_error
    print("OK")


asyncio.run(main())
