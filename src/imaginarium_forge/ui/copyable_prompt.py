"""Reusable Streamlit prompt preview with an explicit copy control.

``st.code`` already exposes a compact copy icon in supported Streamlit builds,
but it is easy to miss.  This component keeps the readable code block and adds
an adjacent, clearly labelled button.  Prompt text is base64 encoded before it
enters the iframe, so user-authored text can never terminate the script tag.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from html import escape

import streamlit as st

_EMPTY_PROMPT_COPY = "尚未產生提示詞"
_DEFAULT_BUTTON_LABEL = "複製提示詞"


@lru_cache(maxsize=256)
def build_copy_button_html(
    prompt: str,
    *,
    key: str | None = None,
    button_label: str = _DEFAULT_BUTTON_LABEL,
    help_text: str | None = None,
) -> str:
    """Return an isolated, injection-safe copy button document.

    The prompt is never interpolated as HTML or JavaScript source.  Base64 also
    preserves non-ASCII text should another page reuse the component for a
    bilingual draft.  ``key`` is hashed because it may itself be user-authored.
    """

    encoded_prompt = base64.b64encode(prompt.encode("utf-8")).decode("ascii")
    identity = key if key is not None else f"{button_label}\0{prompt}"
    token = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    button_id = f"if-copy-{token}"
    status_id = f"if-copy-status-{token}"
    safe_label = escape(button_label, quote=True)
    safe_help = escape(
        help_text or "將這段英文提示詞複製到剪貼簿",
        quote=True,
    )
    disabled = ' disabled aria-disabled="true"' if not prompt.strip() else ""

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; }}
  html, body {{
    width: 100%; height: 100%; margin: 0; padding: 0; overflow: hidden;
    background: transparent; color-scheme: dark;
  }}
  .copy-row {{ display: flex; align-items: stretch; height: 48px; }}
  button {{
    width: 100%; min-height: 48px; padding: 9px 12px; border: 1px solid #9a652d;
    border-radius: 10px; background: #f2bf74; color: #2a190f; font: 700 14px/1.2 sans-serif;
    cursor: pointer; box-shadow: 0 6px 16px rgba(52, 29, 11, .22);
  }}
  button:hover:not(:disabled) {{ background: #ffd89b; border-color: #ffe2b1; }}
  button:focus-visible {{ outline: 3px solid #ffd28d; outline-offset: -4px; }}
  button:disabled {{
    border-color: #51434b; background: #2b242a; color: #b9aaa1;
    opacity: 1; cursor: not-allowed; box-shadow: none;
  }}
  .status {{
    position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0;
    overflow: hidden; clip: rect(0 0 0 0); clip-path: inset(50%);
    border: 0; white-space: nowrap;
  }}
</style>
</head>
<body>
  <div class="copy-row">
    <button type="button" id="{button_id}" title="{safe_help}"{disabled}>{safe_label}</button>
    <span class="status" id="{status_id}" role="status" aria-live="polite"></span>
  </div>
<script>
(() => {{
  const button = document.getElementById("{button_id}");
  const status = document.getElementById("{status_id}");
  const payload = "{encoded_prompt}";
  const originalLabel = button.textContent;

  const decodePrompt = () => {{
    const bytes = Uint8Array.from(atob(payload), character => character.charCodeAt(0));
    return new TextDecoder("utf-8").decode(bytes);
  }};

  const fallbackCopy = text => {{
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.left = "-9999px";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.focus();
    area.select();
    const copied = document.execCommand("copy");
    area.remove();
    if (!copied) throw new Error("copy command rejected");
  }};

  button.addEventListener("click", async () => {{
    const text = decodePrompt();
    if (!text.trim()) return;
    try {{
      if (navigator.clipboard && window.isSecureContext) {{
        await navigator.clipboard.writeText(text);
      }} else {{
        fallbackCopy(text);
      }}
      button.textContent = "已複製 ✓";
      status.textContent = "可以貼到繪圖工具了";
      window.setTimeout(() => {{
        button.textContent = originalLabel;
        status.textContent = "";
      }}, 1800);
    }} catch (error) {{
      try {{
        fallbackCopy(text);
        button.textContent = "已複製 ✓";
        status.textContent = "可以貼到繪圖工具了";
      }} catch (fallbackError) {{
        button.textContent = "複製失敗";
        status.textContent = "請改用提示詞框右上角的複製圖示";
      }}
    }}
  }});
}})();
</script>
</body>
</html>"""


def render_copyable_prompt(
    label: str,
    prompt: str,
    *,
    key: str | None = None,
    help_text: str | None = None,
) -> None:
    """Render a labelled prompt block and a visible copy button beside it."""

    st.html(f'<div style="font-weight:700; margin:.25rem 0 .35rem">{escape(label)}</div>')
    if help_text:
        st.caption(help_text)
    prompt_column, copy_column = st.columns(
        [5, 1.25],
        gap="small",
        vertical_alignment="top",
    )
    with prompt_column:
        st.code(prompt if prompt.strip() else _EMPTY_PROMPT_COPY, language=None, wrap_lines=True)
    with copy_column:
        document = build_copy_button_html(
            prompt,
            key=key,
            help_text=help_text,
        )
        st.iframe(document, height=48, tab_index=0)


__all__ = ["build_copy_button_html", "render_copyable_prompt"]
