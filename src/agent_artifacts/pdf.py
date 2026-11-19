"""HTML to PDF with headless Chromium, cut off from every network.

The HTML is untrusted, and Chromium runs on the server, so the page must not
reach anything: not the internet, not this service, not other ports on the
host. Three layers do that: every request the page makes is aborted, the
browser's proxy points at a closed port with no loopback exception (that
also catches WebSockets), and WebRTC may not send UDP outside the proxy;
the context is offline on top of that.
The page is loaded with ``set_content``, so it has no file:// origin either.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

TIMEOUT_SECONDS = 20.0
MAX_BYTES = 50 * 1024 * 1024

_ARGS = [
    "--proxy-server=127.0.0.1:9",
    "--proxy-bypass-list=<-loopback>",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--js-flags=--max-old-space-size=512",
]


async def render(html: str, out: Path) -> None:
    """Write ``html`` as a PDF to ``out``. Raises on failure, after
    ``TIMEOUT_SECONDS``, or if the PDF is over ``MAX_BYTES``."""
    data = await asyncio.wait_for(_render(html), TIMEOUT_SECONDS)
    if len(data) > MAX_BYTES:
        raise ValueError(f"PDF is {len(data)} bytes, over the limit")
    out.write_bytes(data)


async def _render(html: str) -> bytes:
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=_ARGS)
        try:
            context = await browser.new_context(offline=True)
            await context.route("**/*", lambda route: route.abort())
            page = await context.new_page()
            await page.set_content(html)
            return await page.pdf(print_background=True, format="A4")
        finally:
            await browser.close()
