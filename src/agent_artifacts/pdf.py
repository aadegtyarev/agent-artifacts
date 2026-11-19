"""HTML to PDF with headless Chromium, cut off from every network.

The HTML is untrusted, and Chromium runs on the server, so the page must not
reach anything: not the internet, not this service, not other ports on the
host. Three layers do that: every request the page makes is aborted, the
browser's proxy points at a closed port with no loopback exception (that
also catches WebSockets), and WebRTC may not send UDP outside the proxy.
The page is loaded with ``set_content``, so it has no file:// origin either.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright

TIMEOUT_SECONDS = 20.0

_ARGS = [
    "--proxy-server=127.0.0.1:9",
    "--proxy-bypass-list=<-loopback>",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
]


async def render(html: str, out: Path) -> None:
    """Write ``html`` as a PDF to ``out``, atomically. Raises on failure or
    after ``TIMEOUT_SECONDS``."""
    tmp = out.with_suffix(".tmp")
    try:
        await asyncio.wait_for(_render(html, tmp), TIMEOUT_SECONDS)
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)


async def _render(html: str, out: Path) -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=_ARGS)
        try:
            context = await browser.new_context(service_workers="block", accept_downloads=False)
            await context.route("**/*", lambda route: route.abort())
            page = await context.new_page()
            await page.set_content(html, wait_until="load")
            await page.pdf(path=out, print_background=True, format="A4")
        finally:
            await browser.close()
