"""Make post search click "Load more" before reading the page, open each post's "… more", and copy
each post's link.

Scrolls to the end, clicks, and repeats: up to one click per requested page (max_scrolls / 5), so
`max_pages` keeps meaning "how deep to go". Stops when the button is gone. Then it clicks every
post's "… more" button, so long posts are read whole (the text is already on the page: no request).
Only <button> elements are clicked, and if a click ever leaves the results page it goes back and
stops expanding.

The search page has no permalink per post (the upstream reader takes them from network payloads,
unordered), so each post's control menu is opened and its "Copy link to post" clicked with the
clipboard write caught in the page: one lnkd.in short link per post, in result order, with the
author from the menu button's label. They come back as references of kind "post_link". No page
load, only the short-link request LinkedIn makes for the copy.
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
            more = _re.compile(r"^\s*(?:…|\.\.\.)?\s*(?:see\s+)?(?:more|más)\s*$", _re.I)
            start_url = self._session.page.url
            expanded = 0
            toggles = self._session.page.locator("button").filter(has_text=more)
            try:
                for k in range(min(await toggles.count(), 60)):
                    t = toggles.nth(k)
                    if not await t.is_visible():
                        continue
                    await t.click(timeout=2000)
                    expanded += 1
                    await self._session.delay(0.3)
                    if self._session.page.url != start_url:
                        await self._session.page.go_back()
                        logger.warning("Post search: a '… more' click left the page; stopped expanding")
                        break
            except OffLinkedInLandingError:
                raise
            except Exception as e:
                logger.warning("'… more' expansion stopped after %d: %s", expanded, e)
            logger.warning("Post search: expanded %d post(s)", expanded)
            _post_links = []
            try:
                await self._session.page.evaluate(
                    "() => { window.__vigiaCopied = []; navigator.clipboard.writeText = async t => { window.__vigiaCopied.push(t); }; }")
                prefix = "Open control menu for post by "
                menus = self._session.page.locator(f'button[aria-label^="{prefix}"]')
                copy = _re.compile(r"copy link", _re.I)
                for k in range(min(await menus.count(), 60)):
                    m = menus.nth(k)
                    label = await m.get_attribute("aria-label") or ""
                    await m.scroll_into_view_if_needed(timeout=2000)
                    await m.click(timeout=2000)
                    await self._session.delay(0.5)
                    item = self._session.page.locator("[role=menuitem]").filter(has_text=copy)
                    link = ""
                    if await item.count():
                        before = await self._session.page.evaluate("() => window.__vigiaCopied.length")
                        await item.first.click(timeout=2000)
                        await self._session.delay(0.6)
                        got = await self._session.page.evaluate("() => window.__vigiaCopied")
                        if len(got) > before:
                            link = got[-1]
                    else:
                        await self._session.page.keyboard.press("Escape")
                    if self._session.page.url != start_url:
                        await self._session.page.go_back()
                        logger.warning("Post search: a menu click left the page; stopped copying links")
                        break
                    _post_links.append({"kind": "post_link", "url": link, "text": label[len(prefix):]})
            except OffLinkedInLandingError:
                raise
            except Exception as e:
                logger.warning("Copying post links stopped after %d: %s", len(_post_links), e)
            logger.warning("Post search: copied %d post link(s)", sum(1 for x in _post_links if x["url"]))

"""
src = src.replace(anchor, patch + anchor)
ret = """        return ExtractedSection(text=cleaned, references=references)"""
assert src.count(ret) == 1, "capture.py changed: the post-link patch no longer applies"
src = src.replace(ret, """        if locals().get("_post_links"):
            references = list(references) + _post_links
""" + ret)
path.write_text(src)
print("patched", path)
