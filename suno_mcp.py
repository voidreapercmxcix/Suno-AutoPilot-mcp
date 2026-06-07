#!/usr/bin/env python3
"""
Suno Autopilot MCP Server
=========================

A FastMCP server that drives Suno.com via Chrome DevTools Protocol (CDP),
exposing a small set of high-level tools so the model's system prompt can
stay tiny. Each tool encapsulates a complex multi-step browser routine.

Chrome must already be running with --remote-debugging-port=9222 (see
start-suno-autopilot.sh). The server attaches to that Chrome; it never
launches its own browser.

Tools:
  - inject_cookie     : Insert hCaptcha accessibility cookie via CDP
  - validate_cookie   : Confirm the hCaptcha cookie is present and active
  - navigate_suno     : Go to suno.com/create and wait for full load
  - select_workspace  : Click a workspace by name
  - clear_form        : Click Clear and confirm the dialog
  - type_lyrics       : Type lyrics char-by-char into the Lyrics field
  - type_style        : Type style char-by-char into the Style field
  - click_create      : Verify Create is active, then click it
  - wait_next         : Sleep a random 45-90 seconds before the next job
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx
import websockets
from fastmcp import FastMCP


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CDP_HOST = os.environ.get("SUNO_CDP_HOST", "127.0.0.1")
CDP_PORT = int(os.environ.get("SUNO_CDP_PORT", "9222"))
CDP_BASE = f"http://{CDP_HOST}:{CDP_PORT}"

# The hCaptcha accessibility cookie. This is the well-known cookie issued by
# hCaptcha's accessibility programme - the user is responsible for supplying
# a valid token via the env var if they have one. If unset we still emit a
# cookie scaffold the user can replace.
HCAPTCHA_COOKIE_NAME = "hc_accessibility"
HCAPTCHA_COOKIE_VALUE = os.environ.get("HCAPTCHA_ACCESSIBILITY_TOKEN", "")
HCAPTCHA_DOMAIN = ".hcaptcha.com"

# Suno DOM hints - kept here, not in the system prompt, so the model never
# has to remember them.
LYRICS_SELECTORS = [
    "textarea[placeholder*='lyrics' i]",
    "textarea[data-testid*='lyrics' i]",
    "div[contenteditable='true'][aria-label*='lyrics' i]",
    "textarea[aria-label*='lyrics' i]",
]
STYLE_SELECTORS = [
    "textarea[data-testid*='style' i]",
    "textarea[aria-label*='style' i]",
]
CREATE_BUTTON_SELECTORS = [
    "button[data-testid='create-button']",
    "button[aria-label*='create' i]",
]
CLEAR_BUTTON_SELECTORS = [
    "button[data-testid='clear-button']",
    "button[aria-label*='clear' i]",
]
CONFIRM_DIALOG_SELECTORS = [
    "button[data-testid='confirm-clear']",
]

DEFAULT_TYPE_DELAY_MS = 25  # per-character keystroke delay, jittered


# ---------------------------------------------------------------------------
# CDP plumbing
# ---------------------------------------------------------------------------


@dataclass
class CdpTarget:
    target_id: str
    ws_url: str
    url: str
    title: str


class CdpClient:
    """Tiny CDP client. One open websocket per call; closes cleanly so the
    underlying Chrome connection limit is never exhausted."""

    def __init__(self) -> None:
        self._msg_id = 0

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def list_targets(self) -> list[CdpTarget]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{CDP_BASE}/json")
            r.raise_for_status()
            data = r.json()
        out: list[CdpTarget] = []
        for t in data:
            if t.get("type") != "page":
                continue
            out.append(
                CdpTarget(
                    target_id=t["id"],
                    ws_url=t["webSocketDebuggerUrl"],
                    url=t.get("url", ""),
                    title=t.get("title", ""),
                )
            )
        return out

    async def pick_suno_target(self) -> CdpTarget:
        """Find the Suno tab; fall back to the first page target."""
        targets = await self.list_targets()
        if not targets:
            raise RuntimeError(
                f"No CDP page targets at {CDP_BASE}. Is the debug Chrome running?"
            )
        for t in targets:
            if "suno.com" in t.url:
                return t
        return targets[0]

    async def open_new_tab(self, url: str) -> CdpTarget:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.put(f"{CDP_BASE}/json/new?{url}")
            r.raise_for_status()
            t = r.json()
        return CdpTarget(
            target_id=t["id"],
            ws_url=t["webSocketDebuggerUrl"],
            url=t.get("url", url),
            title=t.get("title", ""),
        )

    async def send(
        self,
        target: CdpTarget,
        method: str,
        params: Optional[dict] = None,
        *,
        expect: bool = True,
        timeout: float = 15.0,
    ) -> dict:
        """Open a transient WS connection, send one command, await reply."""
        msg_id = self._next_id()
        payload = {"id": msg_id, "method": method, "params": params or {}}
        async with websockets.connect(target.ws_url, max_size=16 * 1024 * 1024) as ws:
            await ws.send(json.dumps(payload))
            if not expect:
                return {}
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                raw = await asyncio.wait_for(
                    ws.recv(), timeout=max(0.1, deadline - time.monotonic())
                )
                msg = json.loads(raw)
                if msg.get("id") == msg_id:
                    if "error" in msg:
                        raise RuntimeError(
                            f"CDP error {method}: {msg['error']}"
                        )
                    return msg.get("result", {})
            raise TimeoutError(f"CDP {method} timed out after {timeout}s")

    async def send_session(
        self,
        target: CdpTarget,
        commands: list[tuple[str, dict]],
        *,
        timeout: float = 30.0,
    ) -> list[dict]:
        """Run several commands on a single WS session, in order."""
        results: list[dict] = []
        async with websockets.connect(target.ws_url, max_size=16 * 1024 * 1024) as ws:
            for method, params in commands:
                msg_id = self._next_id()
                await ws.send(
                    json.dumps({"id": msg_id, "method": method, "params": params})
                )
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    raw = await asyncio.wait_for(
                        ws.recv(),
                        timeout=max(0.1, deadline - time.monotonic()),
                    )
                    msg = json.loads(raw)
                    if msg.get("id") == msg_id:
                        if "error" in msg:
                            raise RuntimeError(
                                f"CDP error {method}: {msg['error']}"
                            )
                        results.append(msg.get("result", {}))
                        break
                else:
                    raise TimeoutError(f"CDP {method} timed out after {timeout}s")
        return results

    async def eval_js(
        self,
        target: CdpTarget,
        expression: str,
        *,
        await_promise: bool = True,
        return_by_value: bool = True,
    ) -> Any:
        res = await self.send(
            target,
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": return_by_value,
                "userGesture": True,
            },
            timeout=30.0,
        )
        if "exceptionDetails" in res:
            raise RuntimeError(
                f"JS exception: {res['exceptionDetails'].get('text')}"
            )
        return res.get("result", {}).get("value")


CDP = CdpClient()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _selectors_js_array(selectors: list[str]) -> str:
    return "[" + ", ".join(json.dumps(s) for s in selectors) + "]"


async def _find_first(target: CdpTarget, selectors: list[str]) -> bool:
    js = f"""
    (() => {{
      const sels = {_selectors_js_array(selectors)};
      for (const s of sels) {{
        const el = document.querySelector(s);
        if (el) return true;
      }}
      return false;
    }})()
    """
    return bool(await CDP.eval_js(target, js))


async def _focus_first(target: CdpTarget, selectors: list[str]) -> bool:
    js = f"""
    (() => {{
      const sels = {_selectors_js_array(selectors)};
      for (const s of sels) {{
        const el = document.querySelector(s);
        if (el) {{
          el.scrollIntoView({{block: 'center'}});
          el.focus();
          if (typeof el.click === 'function') el.click();
          return true;
        }}
      }}
      return false;
    }})()
    """
    return bool(await CDP.eval_js(target, js))


async def _clear_field(target: CdpTarget, selectors: list[str]) -> None:
    js = f"""
    (() => {{
      const sels = {_selectors_js_array(selectors)};
      for (const s of sels) {{
        const el = document.querySelector(s);
        if (!el) continue;
        el.focus();
        if ('value' in el) {{
          const proto = el.tagName === 'TEXTAREA'
            ? window.HTMLTextAreaElement.prototype
            : window.HTMLInputElement.prototype;
          const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
          setter.call(el, '');
          el.dispatchEvent(new Event('input', {{bubbles: true}}));
        }} else {{
          el.textContent = '';
          el.dispatchEvent(new InputEvent('input', {{bubbles: true}}));
        }}
        return true;
      }}
      return false;
    }})()
    """
    await CDP.eval_js(target, js)


async def _type_into(
    target: CdpTarget,
    selectors: list[str],
    text: str,
    delay_ms: int = DEFAULT_TYPE_DELAY_MS,
) -> dict:
    """Set the target field value in a way React actually registers.

    Input.insertText and raw key events both write to the DOM but leave
    React's internal state empty, so the next re-render (e.g. focusing
    the style field) wipes the text. The native-prototype-setter trick
    bypasses React's own property override and fires a real input event
    so React's event delegation picks it up and calls setState correctly.
    """
    focused = await _focus_first(target, selectors)
    if not focused:
        return {"ok": False, "error": "field not found", "selectors": selectors}

    await asyncio.sleep(0.15)

    js = f"""
    (() => {{
      const sels = {_selectors_js_array(selectors)};
      for (const s of sels) {{
        const el = document.querySelector(s);
        if (!el) continue;
        el.focus();
        if (el.getAttribute('contenteditable') === 'true') {{
          // contenteditable path
          el.textContent = '';
          el.dispatchEvent(new InputEvent('input', {{bubbles: true,
            inputType: 'insertText', data: {json.dumps(text)}}}) );
          document.execCommand('selectAll', false, null);
          document.execCommand('insertText', false, {json.dumps(text)});
          return el.textContent.length;
        }} else {{
          // textarea / input path — native setter so React sees the change
          const proto = el.tagName === 'TEXTAREA'
            ? window.HTMLTextAreaElement.prototype
            : window.HTMLInputElement.prototype;
          const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
          setter.call(el, {json.dumps(text)});
          el.dispatchEvent(new Event('input',  {{bubbles: true}}));
          el.dispatchEvent(new Event('change', {{bubbles: true}}));
          return el.value.length;
        }}
      }}
      return 0;
    }})()
    """
    result = await CDP.eval_js(target, js)
    return {
        "ok": bool(result),
        "typed": result or 0,
        "error": None if result else "field not found or value not set",
    }


async def _wait_for(
    target: CdpTarget,
    expression: str,
    *,
    timeout_s: float = 20.0,
    interval_s: float = 0.25,
) -> bool:
    """Poll a JS boolean expression until truthy or timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            v = await CDP.eval_js(target, expression)
            if v:
                return True
        except Exception:
            pass
        await asyncio.sleep(interval_s)
    return False


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="suno-autopilot",
    instructions=(
        "High-level Suno automation tools. The model only needs to call these "
        "in sensible order; all DOM details, keystroke simulation, dialog "
        "handling and cookie injection are owned by this server."
    ),
)


@mcp.tool
async def inject_cookie(
    value: Optional[str] = None,
    name: str = HCAPTCHA_COOKIE_NAME,
    domain: str = HCAPTCHA_DOMAIN,
) -> dict:
    """Inject the hCaptcha accessibility cookie into the running Chrome.

    Args:
        value:  Cookie value to set. Falls back to env var
                HCAPTCHA_ACCESSIBILITY_TOKEN. Required - hCaptcha will reject
                an empty value.
        name:   Cookie name (default: hc_accessibility).
        domain: Domain to scope it to (default: .hcaptcha.com).

    Returns:
        {"ok": bool, "name": str, "domain": str, "error": str?}
    """
    cookie_value = value or HCAPTCHA_COOKIE_VALUE
    if not cookie_value:
        return {
            "ok": False,
            "error": (
                "No cookie value provided. Pass `value=...` or set the "
                "HCAPTCHA_ACCESSIBILITY_TOKEN environment variable before "
                "starting LM Studio."
            ),
        }

    target = await CDP.pick_suno_target()
    expires = int(time.time()) + 60 * 60 * 24 * 365  # 1 year

    await CDP.send(
        target,
        "Network.setCookie",
        {
            "name": name,
            "value": cookie_value,
            "domain": domain,
            "path": "/",
            "secure": True,
            "httpOnly": False,
            "sameSite": "None",
            "expires": expires,
        },
    )
    return {"ok": True, "name": name, "domain": domain}


@mcp.tool
async def validate_cookie(
    name: str = HCAPTCHA_COOKIE_NAME,
    domain: str = HCAPTCHA_DOMAIN,
) -> dict:
    """Check that the hCaptcha accessibility cookie is present and not
    expired for the given domain. Should be called before navigate_suno.

    Returns:
        {"ok": bool, "present": bool, "expired": bool, "value_len": int?,
         "error": str?}
    """
    target = await CDP.pick_suno_target()
    url = f"https://{domain.lstrip('.')}/"
    res = await CDP.send(target, "Network.getCookies", {"urls": [url]})
    now = int(time.time())
    for c in res.get("cookies", []):
        if c.get("name") == name:
            exp = c.get("expires") or 0
            expired = 0 < exp < now
            return {
                "ok": not expired,
                "present": True,
                "expired": expired,
                "value_len": len(c.get("value", "")),
            }
    return {
        "ok": False,
        "present": False,
        "expired": False,
        "error": (
            f"Cookie {name!r} not found for {domain}. Call inject_cookie first."
        ),
    }


@mcp.tool
async def navigate_suno(
    url: str = "https://suno.com/create",
    wait_seconds: float = 30.0,
) -> dict:
    """Navigate the Suno tab to suno.com/create (or supplied URL) and wait
    for the page to fully load - meaning the Lyrics field is visible.

    Returns:
        {"ok": bool, "url": str, "ready": bool, "error": str?}
    """
    try:
        target = await CDP.pick_suno_target()
    except Exception:
        target = await CDP.open_new_tab(url)

    await CDP.send(target, "Page.enable")
    await CDP.send(target, "Page.navigate", {"url": url})

    ready_expr = f"""
    (() => {{
      if (document.readyState !== 'complete') return false;
      const sels = {_selectors_js_array(LYRICS_SELECTORS)};
      return sels.some(s => !!document.querySelector(s));
    }})()
    """
    ready = await _wait_for(target, ready_expr, timeout_s=wait_seconds)
    return {
        "ok": ready,
        "url": url,
        "ready": ready,
        "error": None if ready else "Lyrics field never appeared - page may not be the Create view",
    }


@mcp.tool
async def select_workspace(name: str) -> dict:
    """Click the workspace whose visible label matches `name` (case-insensitive,
    trimmed). Use the exact workspace name from Suno's sidebar.

    Args:
        name: The workspace name to select.

    Returns:
        {"ok": bool, "clicked": str?, "error": str?}
    """
    target = await CDP.pick_suno_target()
    js = f"""
    (() => {{
      const want = {json.dumps(name)}.trim().toLowerCase();
      const candidates = Array.from(document.querySelectorAll(
        "[role='button'], button, a, li, div"
      ));
      for (const el of candidates) {{
        const label = (el.innerText || el.textContent || '').trim();
        if (!label) continue;
        if (label.toLowerCase() === want) {{
          el.scrollIntoView({{block: 'center'}});
          el.click();
          return label;
        }}
      }}
      // Fallback: contains match
      for (const el of candidates) {{
        const label = (el.innerText || el.textContent || '').trim();
        if (!label) continue;
        if (label.toLowerCase().includes(want)) {{
          el.scrollIntoView({{block: 'center'}});
          el.click();
          return label;
        }}
      }}
      return null;
    }})()
    """
    clicked = await CDP.eval_js(target, js)
    if not clicked:
        return {"ok": False, "error": f"Workspace {name!r} not found on page."}
    return {"ok": True, "clicked": clicked}


@mcp.tool
async def clear_form() -> dict:
    """Click the Clear button and confirm the modal dialog. Leaves both the
    Lyrics and Style fields empty.

    Returns:
        {"ok": bool, "cleared": bool, "error": str?}
    """
    target = await CDP.pick_suno_target()

    clicked = await _focus_first(target, CLEAR_BUTTON_SELECTORS)
    if not clicked:
        # Last resort: text match
        js = """
        (() => {
          const buttons = Array.from(document.querySelectorAll('button'));
          for (const b of buttons) {
            if ((b.innerText || '').trim().toLowerCase() === 'clear') {
              b.click();
              return true;
            }
          }
          return false;
        })()
        """
        clicked = bool(await CDP.eval_js(target, js))
    if not clicked:
        return {"ok": False, "error": "Clear button not found"}

    # Brief settle — check if a confirm dialog appears; click it if so.
    # Suno may or may not show a confirm dialog depending on UI version.
    await asyncio.sleep(0.5)
    confirm_expr = f"""
    (() => {{
      const sels = {_selectors_js_array(CONFIRM_DIALOG_SELECTORS)};
      for (const s of sels) {{
        const el = document.querySelector(s);
        if (el) {{ el.click(); return true; }}
      }}
      const buttons = Array.from(document.querySelectorAll(
        "[role='dialog'] button, [aria-modal='true'] button"
      ));
      for (const b of buttons) {{
        const t = (b.innerText || '').trim().toLowerCase();
        if (['confirm', 'yes', 'clear', 'ok'].includes(t)) {{
          b.click();
          return true;
        }}
      }}
      return false;
    }})()
    """
    # Fire confirm if dialog appeared; don't fail if it didn't.
    try:
        await CDP.eval_js(target, confirm_expr)
    except Exception:
        pass
    return {"ok": True, "cleared": True}


@mcp.tool
async def type_lyrics(text: str, delay_ms: int = DEFAULT_TYPE_DELAY_MS) -> dict:
    """Type lyrics into the Lyrics field character-by-character (React-safe).

    Args:
        text: Full lyrics block including [INTRO]/[VERSE]/etc tags. Newlines
              are preserved as Enter keystrokes.
        delay_ms: Per-keystroke delay in ms; jittered ±40%.

    Returns:
        {"ok": bool, "typed": int, "error": str?}
    """
    target = await CDP.pick_suno_target()
    return await _type_into(target, LYRICS_SELECTORS, text, delay_ms=delay_ms)


@mcp.tool
async def type_style(text: str, delay_ms: int = DEFAULT_TYPE_DELAY_MS) -> dict:
    """Type the producer-style brief into the Style field.

    Suno's style textarea has no stable testId or aria-label, so we locate
    it as the first textarea after the lyrics-textarea in DOM order.

    Args:
        text: Style description (<= 900 chars recommended).

    Returns:
        {"ok": bool, "typed": int, "error": str?}
    """
    if len(text) > 900:
        return {
            "ok": False,
            "typed": 0,
            "error": f"Style is {len(text)} chars; Suno caps at 900.",
        }
    target = await CDP.pick_suno_target()

    js = f"""
    (() => {{
      // Style textarea = first textarea after lyrics-textarea in DOM order
      const all = Array.from(document.querySelectorAll('textarea'));
      const lyricsIdx = all.findIndex(t => t.getAttribute('data-testid') === 'lyrics-textarea');
      const el = lyricsIdx >= 0 ? all[lyricsIdx + 1] : null;
      if (!el) return 0;
      el.scrollIntoView({{block: 'center'}});
      el.focus();
      const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
      setter.call(el, {json.dumps(text)});
      el.dispatchEvent(new Event('input',  {{bubbles: true}}));
      el.dispatchEvent(new Event('change', {{bubbles: true}}));
      return el.value.length;
    }})()
    """
    result = await CDP.eval_js(target, js)
    return {
        "ok": bool(result),
        "typed": result or 0,
        "error": None if result else "Style field not found (expected textarea after lyrics-textarea)",
    }


@mcp.tool
async def click_create(verify_timeout_s: float = 10.0) -> dict:
    """Verify the Create button is enabled, then click it.

    Returns:
        {"ok": bool, "clicked": bool, "disabled": bool?, "error": str?}
    """
    target = await CDP.pick_suno_target()

    active_expr = f"""
    (() => {{
      const sels = {_selectors_js_array(CREATE_BUTTON_SELECTORS)};
      for (const s of sels) {{
        const el = document.querySelector(s);
        if (el) return el.disabled === false &&
                     el.getAttribute('aria-disabled') !== 'true';
      }}
      // text-match fallback — handles emoji prefix e.g. "✨ Create"
      const buttons = Array.from(document.querySelectorAll('button'));
      for (const b of buttons) {{
        if ((b.innerText || '').trim().toLowerCase().includes('create')) {{
          return b.disabled === false &&
                 b.getAttribute('aria-disabled') !== 'true';
        }}
      }}
      return false;
    }})()
    """
    active = await _wait_for(
        target, active_expr, timeout_s=verify_timeout_s, interval_s=0.3
    )
    if not active:
        return {
            "ok": False,
            "clicked": False,
            "disabled": True,
            "error": "Create button never became active - fields may be empty",
        }

    click_js = f"""
    (() => {{
      const sels = {_selectors_js_array(CREATE_BUTTON_SELECTORS)};
      for (const s of sels) {{
        const el = document.querySelector(s);
        if (el) {{ el.click(); return true; }}
      }}
      const buttons = Array.from(document.querySelectorAll('button'));
      for (const b of buttons) {{
        if ((b.innerText || '').trim().toLowerCase().includes('create')) {{
          b.click();
          return true;
        }}
      }}
      return false;
    }})()
    """
    clicked = bool(await CDP.eval_js(target, click_js))
    return {"ok": clicked, "clicked": clicked, "disabled": False}


@mcp.tool
async def wait_next(min_seconds: int = 45, max_seconds: int = 90) -> dict:
    """Sleep a random interval between two generations. Defaults to 45-90s.

    Returns:
        {"ok": True, "slept_seconds": float}
    """
    if min_seconds < 0 or max_seconds < min_seconds:
        return {
            "ok": False,
            "error": f"Bad range: min={min_seconds}, max={max_seconds}",
        }
    delay = random.uniform(min_seconds, max_seconds)
    await asyncio.sleep(delay)
    return {"ok": True, "slept_seconds": round(delay, 2)}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Default transport is stdio - which is exactly what LM Studio expects.
    mcp.run()
