"""Make post search click "Load more" before reading the page.

Up to one click per requested page (max_scrolls / 5), with a pause between clicks, so
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
            for i in range(clicks):
                button = self._session.page.get_by_role("button", name=pattern)
                try:
                    if await button.count() == 0:
                        logger.debug("No 'Load more' button after %d clicks", i)
                        break
                    target = button.first
                    await target.scroll_into_view_if_needed(timeout=3000)
                    await target.click(timeout=3000)
                    await self._session.delay(2.0)
                except OffLinkedInLandingError:
                    raise
                except Exception as e:
                    logger.debug("Load more click failed after %d clicks: %s", i, e)
                    break

"""
path.write_text(src.replace(anchor, patch + anchor))
print("patched", path)
