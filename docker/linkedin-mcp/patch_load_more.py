"""Make post search click "Load more" before reading the page.

Scrolls to the end, clicks, and repeats: up to one click per requested page (max_scrolls / 5), so
`max_pages` keeps meaning "how deep to go". Stops when the button is gone.
"""
from pathlib import Path

import linkedin_mcp_server.linkedin.capture as capture

path = Path(capture.__file__)
src = path.read_text()
anchor = """        if CaptureMode.ACTIVITY in plan.mode:
            scrolls = plan.max_scrolls"""
assert src.count(anchor) == 1, "capture.py changed: the Load more patch no longer applies"
patch = """        if CaptureMode.POST_PERMALINKS in plan.mode:
            import re as _re
            pattern = _re.compile(r"^\\s*(load more|show more results|ver más resultados|mostrar más resultados|cargar más)\\s*$", _re.I)
            clicks = max(1, (plan.max_scrolls or 5) // 5)
            clicked = 0
            for i in range(clicks):
                # The button is rendered at the end of the list only once the page is scrolled there.
                await self._session.scroll_body(pause_time=0.8, max_scrolls=3)
                button = self._session.page.locator("button, a, [role=button]").filter(has_text=pattern)
                try:
                    if await button.count() == 0:
                        break
                    target = button.first
                    await target.scroll_into_view_if_needed(timeout=3000)
                    await target.click(timeout=3000)
                    await self._session.delay(2.0)
                    clicked += 1
                except OffLinkedInLandingError:
                    raise
                except Exception as e:
                    logger.warning("Load more click failed after %d clicks: %s", i, e)
                    break
            logger.warning("Post search: clicked Load more %d time(s)", clicked)

"""
path.write_text(src.replace(anchor, patch + anchor))
print("patched", path)
