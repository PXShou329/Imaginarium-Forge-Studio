"""Product-wide visual system for the local Streamlit workbench."""

from __future__ import annotations

import streamlit as st

_CSS = r"""
<style>
:root {
  --forge-bg: #091017;
  --forge-surface: #111b23;
  --forge-raised: #18252e;
  --forge-border: #34444d;
  --forge-border-strong: #6d5b42;
  --forge-text: #f5ecdc;
  --forge-muted: #c7bcaa;
  --forge-subtle: #998f82;
  --forge-amber: #d9aa60;
  --forge-amber-soft: rgba(217, 170, 96, 0.16);
  --forge-teal: #91bfad;
  --forge-red: #d18478;
  --forge-action-primary: #ddb56f;
  --forge-action-primary-top: #edcc8e;
  --forge-action-primary-hover: #ecc47e;
  --forge-action-primary-hover-top: #f6daa4;
  --forge-action-primary-ink: #20170c;
  --forge-action-secondary: #1b2932;
  --forge-action-secondary-top: #22343e;
  --forge-action-secondary-hover: #2a3e49;
  --forge-action-secondary-hover-top: #324a56;
  --forge-action-secondary-ink: #f5ecdc;
  --forge-action-disabled: #141e24;
  --forge-action-disabled-ink: #8e9799;
  --forge-pill: #1a2831;
  --forge-pill-hover: #2a414c;
  --forge-pill-ink: #eee4d3;
  --forge-pill-selected-top: #ecd194;
  --forge-pill-selected: #d9ad67;
  --forge-pill-selected-ink: #24190d;
  --forge-focus: #f0c77f;
  --forge-paper: #eee0c2;
  --forge-paper-ink: #34271f;
  --forge-radius: 12px;
  --forge-action-height: 2.75rem;
  --forge-font: "Segoe UI", "Noto Sans TC", "PingFang TC",
    "Microsoft JhengHei", sans-serif;
  --forge-serif: Georgia, "Noto Serif TC", "PMingLiU", serif;
  --forge-mono: "Cascadia Mono", "SFMono-Regular", Consolas, monospace;
}

html, body, [class*="css"] {
  font-family: var(--forge-font);
}

body,
[data-testid="stAppViewContainer"] {
  color: var(--forge-text);
}

.stApp {
  background:
    radial-gradient(circle at 88% -8%, rgba(113, 88, 51, 0.2), transparent 34rem),
    radial-gradient(circle at 7% 20%, rgba(49, 92, 98, 0.12), transparent 31rem),
    linear-gradient(180deg, #0b131a 0%, #0d171e 48%, #081016 100%);
}

[data-testid="stHeader"] {
  min-height: 0;
  height: 0;
  background: transparent;
  backdrop-filter: none;
  border-bottom: 0;
}

[data-testid="stStatusWidget"],
[data-testid="stDecoration"] {
  display: none !important;
}

/* Keep Streamlit's native sidebar reopen control reachable. Minimal toolbar
   mode removes deployment chrome; this surface contains only that control. */
[data-testid="stToolbar"] {
  position: fixed;
  top: 0.45rem;
  left: 0.45rem;
  z-index: 1200;
  display: flex !important;
  width: auto;
  height: auto;
  padding: 0;
  background: transparent;
}

[data-testid="stExpandSidebarButton"] {
  width: 2.35rem !important;
  height: 2.35rem !important;
  border: 1px solid var(--forge-border) !important;
  border-radius: 8px !important;
  background: rgba(17, 27, 35, 0.96) !important;
  color: var(--forge-text) !important;
}

/* Streamlit injects a chain-link action into every title.  With the compact
   app header it appears as a detached glyph beside the home hero, so suppress
   that exact H1 action while keeping the semantic heading intact. */
h1 span[data-testid="stHeaderActionElements"] > a[aria-label="Link to heading"] {
  display: none !important;
}

/* This single iframe runs the route-change scroll reset.  Collapse only its
   keyed host; prompt-copy and future media iframes keep their normal layout. */
.st-key-route_scroll_reset {
  position: absolute !important;
  width: 0 !important;
  height: 0 !important;
  min-height: 0 !important;
  margin: 0 !important;
  padding: 0 !important;
  overflow: hidden !important;
  pointer-events: none !important;
}

.st-key-route_scroll_reset [data-testid="stElementContainer"],
.st-key-route_scroll_reset [data-testid="stIFrame"] {
  width: 0 !important;
  height: 0 !important;
  min-height: 0 !important;
  border: 0 !important;
  overflow: hidden !important;
}

[data-testid="stMainBlockContainer"] {
  max-width: 1500px;
  padding-top: 0.8rem;
  padding-bottom: 6.5rem;
}

[data-testid="stSidebar"] {
  background:
    linear-gradient(180deg, rgba(217, 170, 96, 0.09), transparent 15rem),
    linear-gradient(90deg, rgba(91, 57, 35, 0.12), transparent 45%),
    #0b1319;
  border-right: 1px solid var(--forge-border);
}

[data-testid="stSidebarContent"] {
  padding-top: 1rem;
}

.if-brand {
  padding: 0.7rem 0.4rem 1.15rem;
  margin-bottom: 0.35rem;
  border-bottom: 1px solid var(--forge-border);
}

.if-brand-mark {
  width: 2.35rem;
  height: 2.35rem;
  display: grid;
  place-items: center;
  margin-bottom: 0.7rem;
  border: 1px solid rgba(232, 162, 74, 0.7);
  border-radius: 50% 50% 44% 44%;
  background: linear-gradient(145deg, rgba(217, 170, 98, 0.22), rgba(134, 183, 160, 0.05));
  color: var(--forge-amber);
  font: 700 1.08rem/1 var(--forge-serif);
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.08), 0 10px 30px rgba(0, 0, 0, 0.2);
}

.if-brand-name {
  color: var(--forge-text);
  font-family: var(--forge-serif);
  font-size: 1.08rem;
  font-weight: 700;
  letter-spacing: 0.025em;
}

.if-brand-sub {
  margin-top: 0.2rem;
  color: var(--forge-muted);
  font-size: 0.76rem;
  letter-spacing: 0.08em;
}

/* AppShell topbar. Native controls keep keyboard semantics and dirty guards. */
.st-key-app_topbar {
  position: sticky;
  top: 0.35rem;
  z-index: 850;
  margin: 0 0 1rem;
  padding: 0.42rem 0.5rem;
  border: 1px solid rgba(132, 109, 73, 0.62);
  border-radius: 11px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.035), transparent 70%),
    rgba(12, 21, 28, 0.94);
  backdrop-filter: blur(18px);
  box-shadow:
    inset 0 1px rgba(255, 255, 255, 0.04),
    0 14px 34px rgba(0, 0, 0, 0.25);
}

.st-key-app_topbar [data-testid="stHorizontalBlock"] {
  align-items: center;
  gap: 0.38rem;
}

.st-key-app_topbar .stButton > button,
.st-key-app_topbar [data-testid="stPopover"] > button {
  min-height: 2.45rem;
  padding: 0.36rem 0.55rem;
  border-radius: 8px;
  font-size: 0.76rem;
  white-space: nowrap;
}

.if-breadcrumb {
  display: flex;
  min-height: 2.45rem;
  align-items: center;
  gap: 0.52rem;
  padding: 0 0.4rem;
  color: var(--forge-muted);
  font-size: 0.74rem;
  white-space: nowrap;
  overflow: hidden;
}

.if-breadcrumb span {
  color: var(--forge-amber);
  font-weight: 700;
}

.if-breadcrumb i {
  color: var(--forge-subtle);
  font-style: normal;
}

.if-breadcrumb strong {
  min-width: 0;
  overflow: hidden;
  color: var(--forge-text);
  font-weight: 650;
  text-overflow: ellipsis;
}

.if-breadcrumb small {
  margin-left: auto;
  overflow: hidden;
  color: var(--forge-subtle);
  text-overflow: ellipsis;
}

.if-kicker {
  color: var(--forge-amber);
  font: 650 0.78rem/1.4 var(--forge-font);
  letter-spacing: 0.09em;
  margin-bottom: 0.4rem;
}

.st-key-page_header {
  margin-bottom: 1.1rem;
  padding: 0.35rem 0 0.25rem;
}

.st-key-page_header h1 {
  max-width: 58rem;
  margin: 0.28rem 0 0.35rem !important;
}

.st-key-page_header [data-testid="stCaptionContainer"] {
  max-width: 62rem;
  font-size: 0.84rem;
  line-height: 1.6;
}

.if-page-header-rule {
  height: 1px;
  margin: 0.15rem 0 1.35rem;
  background: linear-gradient(90deg, var(--forge-border-strong), transparent 72%);
}

h1 {
  color: var(--forge-text) !important;
  font-family: var(--forge-serif) !important;
  font-size: clamp(2.1rem, 3.5vw, 3.2rem) !important;
  font-weight: 700 !important;
  letter-spacing: -0.025em !important;
  line-height: 1.1 !important;
}

h2, h3 {
  color: var(--forge-text) !important;
  font-family: var(--forge-font) !important;
  letter-spacing: -0.012em !important;
}

h2 { font-size: clamp(1.5rem, 2.2vw, 2.15rem) !important; }

p, label, [data-testid="stCaptionContainer"] {
  color: var(--forge-muted);
}

.if-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 0.48rem;
  margin: 0.85rem 0 1.2rem;
}

.if-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.42rem;
  min-height: 1.85rem;
  padding: 0.22rem 0.68rem;
  border: 1px solid var(--forge-border-strong);
  border-radius: 999px;
  background: rgba(28, 36, 48, 0.82);
  color: #d8dee8;
  font-size: 0.76rem;
  font-weight: 640;
}

.if-badge::before {
  content: "";
  width: 0.42rem;
  height: 0.42rem;
  border-radius: 999px;
  background: var(--forge-amber);
  box-shadow: 0 0 0 3px var(--forge-amber-soft);
}

.if-badge.teal::before {
  background: var(--forge-teal);
  box-shadow: 0 0 0 3px rgba(99, 182, 173, 0.12);
}

.if-cap-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.9rem;
  margin: 0.55rem 0 1.4rem;
}

.if-cap-card {
  min-height: 10.2rem;
  padding: 1.15rem 1.2rem;
  border: 1px solid var(--forge-border);
  border-radius: var(--forge-radius);
  background:
    linear-gradient(160deg, rgba(255, 255, 255, 0.035), transparent 50%),
    rgba(17, 27, 35, 0.94);
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.035), 0 16px 50px rgba(0, 0, 0, 0.14);
}

.if-cap-index {
  color: var(--forge-amber);
  font: 700 0.68rem/1 var(--forge-mono);
  letter-spacing: 0.12em;
}

.if-cap-title {
  color: var(--forge-text);
  margin-top: 0.78rem;
  font-size: 1.04rem;
  font-weight: 720;
}

.if-cap-copy {
  color: var(--forge-muted);
  margin-top: 0.45rem;
  font-size: 0.84rem;
  line-height: 1.65;
}

.if-cap-state {
  color: var(--forge-teal);
  margin-top: 0.8rem;
  font: 650 0.72rem/1.2 var(--forge-mono);
  letter-spacing: 0.06em;
}

[data-testid="stMetric"] {
  min-height: 6.25rem;
  padding: 1rem 1.05rem;
  border: 1px solid var(--forge-border);
  border-radius: var(--forge-radius);
  background: linear-gradient(150deg, rgba(217, 170, 96, 0.1), rgba(17, 27, 35, 0.92) 52%);
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.035);
}

[data-testid="stMetricLabel"] { color: var(--forge-muted); }
[data-testid="stMetricValue"] {
  color: var(--forge-text);
  font-weight: 730;
  letter-spacing: -0.035em;
}

[data-testid="stVerticalBlockBorderWrapper"],
[data-testid="stForm"],
[data-testid="stExpander"] {
  border-color: var(--forge-border) !important;
  border-radius: var(--forge-radius) !important;
}

[data-testid="stVerticalBlockBorderWrapper"],
[data-testid="stForm"] {
  background:
    linear-gradient(145deg, rgba(255, 255, 255, 0.025), transparent 44%),
    rgba(17, 27, 35, 0.72);
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.025);
}

[data-testid="stExpander"] {
  background: rgba(17, 27, 35, 0.82);
  overflow: hidden;
}

.stButton > button,
.stDownloadButton > button,
.stLinkButton > a,
.stFormSubmitButton > button {
  min-height: var(--forge-action-height);
  height: auto;
  padding: 0.66rem 0.92rem;
  box-sizing: border-box;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 1.2;
  text-align: center;
  white-space: normal;
}

.stButton > button,
.stDownloadButton > button,
.stLinkButton > a,
.stFormSubmitButton > button {
  border: 1px solid var(--forge-border-strong);
  border-radius: 11px;
  background-color: var(--forge-action-secondary);
  background-image: linear-gradient(
    180deg,
    var(--forge-action-secondary-top),
    var(--forge-action-secondary)
  );
  color: var(--forge-action-secondary-ink) !important;
  font-weight: 650;
  text-decoration: none;
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.055), 0 6px 18px rgba(0, 0, 0, 0.12);
  transition:
    border-color 120ms ease,
    transform 120ms ease,
    background-color 120ms ease,
    box-shadow 120ms ease;
}

.stButton > button *,
.stDownloadButton > button *,
.stLinkButton > a *,
.stFormSubmitButton > button * {
  color: inherit !important;
}

.stButton > button [data-testid="stMarkdownContainer"],
.stDownloadButton > button [data-testid="stMarkdownContainer"],
.stLinkButton > a [data-testid="stMarkdownContainer"],
.stFormSubmitButton > button [data-testid="stMarkdownContainer"] {
  display: flex;
  min-height: 0;
  align-items: center;
  justify-content: center;
}

.stButton > button [data-testid="stMarkdownContainer"] p,
.stDownloadButton > button [data-testid="stMarkdownContainer"] p,
.stLinkButton > a [data-testid="stMarkdownContainer"] p,
.stFormSubmitButton > button [data-testid="stMarkdownContainer"] p {
  margin: 0;
  line-height: 1.2;
}

.stButton > button:hover:not(:disabled),
.stDownloadButton > button:hover:not(:disabled),
.stLinkButton > a:hover:not([disabled]):not([aria-disabled="true"]),
.stFormSubmitButton > button:hover:not(:disabled) {
  border-color: var(--forge-amber);
  background-color: var(--forge-action-secondary-hover);
  background-image: linear-gradient(
    180deg,
    var(--forge-action-secondary-hover-top),
    var(--forge-action-secondary-hover)
  );
  color: var(--forge-action-secondary-ink) !important;
  transform: translateY(-1px);
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.075), 0 9px 22px rgba(0, 0, 0, 0.18);
}

.stButton > button[kind="primary"],
.stDownloadButton > button[kind="primary"],
.stLinkButton > a:is(
  [kind="primary"], [data-testid="stBaseLinkButton-primary"]
),
.stFormSubmitButton > button:is([kind="primary"], [kind="primaryFormSubmit"]) {
  border-color: #ffd18a;
  background-color: var(--forge-action-primary);
  background-image: linear-gradient(
    180deg,
    var(--forge-action-primary-top),
    var(--forge-action-primary)
  );
  color: var(--forge-action-primary-ink) !important;
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.42), 0 8px 22px rgba(52, 29, 11, 0.22);
}

.stButton > button[kind="primary"]:hover:not(:disabled),
.stDownloadButton > button[kind="primary"]:hover:not(:disabled),
.stLinkButton > a:is(
  [kind="primary"], [data-testid="stBaseLinkButton-primary"]
):hover:not([disabled]):not([aria-disabled="true"]),
.stFormSubmitButton > button:is(
  [kind="primary"], [kind="primaryFormSubmit"]
):hover:not(:disabled) {
  border-color: #ffe2b1;
  background-color: var(--forge-action-primary-hover);
  background-image: linear-gradient(
    180deg,
    var(--forge-action-primary-hover-top),
    var(--forge-action-primary-hover)
  );
  color: var(--forge-action-primary-ink) !important;
}

.stButton > button[kind="tertiary"],
.stDownloadButton > button[kind="tertiary"],
.stLinkButton > a:is(
  [kind="tertiary"], [data-testid="stBaseLinkButton-tertiary"]
),
.stFormSubmitButton > button:is([kind="tertiary"], [kind="tertiaryFormSubmit"]) {
  border-color: transparent;
  background: transparent;
  color: var(--forge-amber) !important;
  box-shadow: none;
}

.stButton > button:disabled,
.stDownloadButton > button:disabled,
.stLinkButton > a[disabled],
.stLinkButton > a[aria-disabled="true"],
.stFormSubmitButton > button:disabled {
  border-color: #51434b !important;
  background-color: var(--forge-action-disabled) !important;
  background-image: none !important;
  color: var(--forge-action-disabled-ink) !important;
  opacity: 1 !important;
  cursor: not-allowed;
  box-shadow: none;
  transform: none;
}

.stButton > button:active:not(:disabled),
.stDownloadButton > button:active:not(:disabled),
.stLinkButton > a:active:not([disabled]):not([aria-disabled="true"]),
.stFormSubmitButton > button:active:not(:disabled) {
  transform: translateY(0);
}

/* Tag-builder choices: warm paper chips stay legible against the writing desk. */
[data-testid="stButtonGroup"] button[data-variant="pills"] {
  min-height: 2.35rem;
  border-color: #765d69;
  background: var(--forge-pill);
  color: var(--forge-pill-ink);
  font-weight: 620;
}

[data-testid="stButtonGroup"] button[data-variant="pills"] * {
  color: inherit !important;
}

[data-testid="stButtonGroup"] button[data-variant="pills"]:is(
  [data-hovered], [data-focus-visible]
) {
  border-color: #e5b873;
  background: var(--forge-pill-hover);
}

[data-testid="stButtonGroup"] button[data-variant="pills"][data-selected] {
  border-color: #ffd391 !important;
  background: linear-gradient(
    180deg,
    var(--forge-pill-selected-top),
    var(--forge-pill-selected)
  ) !important;
  color: var(--forge-pill-selected-ink) !important;
  box-shadow: 0 3px 10px rgba(0, 0, 0, 0.16);
}

[data-testid="stButtonGroup"] button[data-variant="pills"][data-selected] * {
  color: var(--forge-pill-selected-ink) !important;
}

button:focus-visible,
input:focus-visible,
textarea:focus-visible,
[role="button"]:focus-visible,
[role="tab"]:focus-visible,
[role="radio"]:focus-visible {
  outline: 3px solid var(--forge-focus) !important;
  outline-offset: 2px !important;
}

[data-baseweb="input"] > div,
[data-baseweb="textarea"] > div,
[data-baseweb="select"] > div,
[data-baseweb="base-input"] {
  border-color: var(--forge-border-strong) !important;
  border-radius: 10px !important;
  background-color: rgba(11, 20, 27, 0.94) !important;
}

[data-baseweb="input"] input,
[data-baseweb="textarea"] textarea,
[data-baseweb="select"] input,
[data-baseweb="select"] [role="combobox"] {
  color: var(--forge-text) !important;
  caret-color: var(--forge-amber);
}

input::placeholder,
textarea::placeholder {
  color: var(--forge-subtle) !important;
  opacity: 1 !important;
}

[data-baseweb="tab-list"] {
  gap: 0.2rem;
  border-bottom: 1px solid var(--forge-border);
}

[data-baseweb="tab"] {
  min-height: 2.8rem;
  border-radius: 9px 9px 0 0;
  color: var(--forge-muted);
}

[aria-selected="true"][data-baseweb="tab"] {
  color: var(--forge-text);
  background: var(--forge-amber-soft);
}

[data-testid="stAlert"] {
  border: 1px solid var(--forge-border-strong);
  border-radius: 12px;
  background: rgba(17, 27, 35, 0.9);
}

[data-testid="stExpander"] summary,
[data-testid="stExpander"] summary * {
  color: var(--forge-text) !important;
}

code, pre, [data-testid="stCodeBlock"] {
  font-family: var(--forge-mono) !important;
}

hr {
  border-color: var(--forge-border) !important;
}

.if-sidebar-context {
  display: grid;
  gap: 0.22rem;
  margin: 0.6rem 0 1rem;
  padding: 0.8rem 0.85rem;
  border: 1px solid rgba(132, 109, 73, 0.55);
  border-radius: 10px;
  background: rgba(21, 32, 39, 0.86);
}

.if-sidebar-context span,
.if-sidebar-label {
  color: var(--forge-subtle);
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.09em;
  text-transform: uppercase;
}

.if-sidebar-context strong {
  overflow: hidden;
  color: var(--forge-text);
  font-family: var(--forge-serif);
  font-size: 0.88rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.if-sidebar-label {
  margin: 0.25rem 0 0.45rem;
}

[data-testid="stSidebar"] .stButton > button {
  min-height: 2.35rem;
  justify-content: flex-start;
  padding: 0.42rem 0.68rem;
  border-color: transparent;
  border-radius: 8px;
  background: transparent;
  box-shadow: none;
  color: var(--forge-muted) !important;
  font-size: 0.78rem;
  text-align: left;
}

[data-testid="stSidebar"] .stButton > button:hover:not(:disabled) {
  border-color: rgba(217, 170, 96, 0.38);
  background: rgba(217, 170, 96, 0.08);
  color: var(--forge-text) !important;
  transform: none;
  box-shadow: none;
}

[data-testid="stSidebar"] .stButton > button[kind="primary"] {
  border-color: rgba(217, 170, 96, 0.48);
  border-left: 3px solid var(--forge-amber);
  background: linear-gradient(90deg, rgba(217, 170, 96, 0.18), rgba(217, 170, 96, 0.06));
  color: #fff2dc !important;
  box-shadow: none;
}

[data-testid="stSidebar"] [data-testid="stExpander"] {
  margin: 0.12rem 0;
  border: 0 !important;
  background: transparent;
  box-shadow: none;
}

[data-testid="stSidebar"] [data-testid="stExpander"] summary {
  min-height: 2.4rem;
  padding: 0.4rem 0.62rem;
  border-radius: 8px;
  font-size: 0.8rem;
  font-weight: 700;
}

[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover {
  background: rgba(217, 170, 96, 0.08);
}

.if-sidebar-status {
  display: flex;
  align-items: center;
  gap: 0.48rem;
  margin-top: 1.05rem;
  padding: 0.65rem 0.7rem;
  border-top: 1px solid var(--forge-border);
  color: var(--forge-muted);
  font-size: 0.72rem;
}

.if-status-dot {
  width: 0.46rem;
  height: 0.46rem;
  border-radius: 999px;
  background: var(--forge-teal);
  box-shadow: 0 0 0 3px rgba(145, 191, 173, 0.12);
}

.if-sidebar-status.is-error .if-status-dot {
  background: var(--forge-red);
  box-shadow: 0 0 0 3px rgba(209, 132, 120, 0.13);
}

/* Creative dashboard */
.st-key-home_hero {
  position: relative;
  margin-bottom: 2.2rem;
  padding: clamp(1rem, 2vw, 1.5rem);
  overflow: hidden;
  border: 1px solid rgba(132, 109, 73, 0.68);
  border-radius: 16px;
  background:
    linear-gradient(110deg, rgba(21, 34, 43, 0.98), rgba(10, 18, 24, 0.88)),
    var(--forge-surface);
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.045), 0 22px 55px rgba(0, 0, 0, 0.25);
}

.st-key-home_hero_message {
  container-type: inline-size;
  width: min(100%, 38rem);
  margin-inline: auto;
  text-align: center;
}

.st-key-home_hero_message h1 {
  margin: 0.35rem 0 0.45rem !important;
  font-size: clamp(1.15rem, 8.7cqw, 3.15rem) !important;
  line-height: 1.1 !important;
  text-align: center;
  white-space: nowrap;
}

.st-key-home_hero_message [data-testid="stMarkdownContainer"] p {
  width: min(100%, 34rem);
  margin-inline: auto;
  text-align: center;
  text-wrap: balance;
}

.st-key-home_hero .if-badges {
  width: min(100%, 34rem);
  justify-content: center;
  margin: 0.85rem auto 1.2rem;
}

.st-key-home_hero [data-testid="stImage"] {
  overflow: hidden;
  border: 1px solid rgba(217, 170, 96, 0.34);
  border-radius: 12px;
  box-shadow: 0 18px 45px rgba(0, 0, 0, 0.34);
}

.st-key-home_hero [data-testid="stImage"] img {
  min-height: 22rem;
  object-fit: cover;
}

/* The dashboard illustration is decorative context, not an image-inspection
   surface.  Keep Streamlit's fullscreen affordance off this one image only. */
.st-key-home_hero button[aria-label="Fullscreen"] {
  display: none !important;
}

.if-section-heading {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 1rem;
  margin: 2rem 0 0.85rem;
  padding-bottom: 0.68rem;
  border-bottom: 1px solid var(--forge-border);
}

.if-section-heading h2 {
  margin: 0.18rem 0 0 !important;
  font-family: var(--forge-serif) !important;
  font-size: clamp(1.35rem, 2vw, 1.8rem) !important;
}

.if-section-heading p {
  max-width: 50rem;
  margin: 0.28rem 0 0;
  font-size: 0.8rem;
  line-height: 1.55;
}

.if-section-eyebrow {
  color: var(--forge-amber);
  font-size: 0.64rem;
  font-weight: 750;
  letter-spacing: 0.13em;
}

.st-key-home_current_project {
  padding: 0.15rem 0 1.2rem;
}

.if-recent-book {
  position: relative;
  display: grid;
  grid-template-columns: 4.5rem minmax(0, 1fr);
  min-height: 13rem;
  margin: 0.2rem 0 0.6rem;
  overflow: hidden;
  border: 1px solid rgba(217, 170, 96, 0.28);
  border-radius: 8px 13px 13px 8px;
  background: var(--forge-surface);
  box-shadow: 0 14px 30px rgba(0, 0, 0, 0.2);
}

.if-recent-book.is-selected {
  border-color: rgba(236, 196, 126, 0.78);
  box-shadow: 0 0 0 2px rgba(217, 170, 96, 0.1), 0 18px 36px rgba(0, 0, 0, 0.24);
}

.if-recent-book-art {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 0.25rem;
  border-right: 1px solid rgba(255, 239, 207, 0.16);
  color: rgba(255, 239, 207, 0.78);
  font: 650 0.65rem/1 var(--forge-serif);
  letter-spacing: 0.08em;
}

.if-recent-book.garnet .if-recent-book-art {
  background: linear-gradient(160deg, #65313a, #321e27);
}
.if-recent-book.forest .if-recent-book-art {
  background: linear-gradient(160deg, #31564e, #172f30);
}
.if-recent-book.indigo .if-recent-book-art {
  background: linear-gradient(160deg, #3b4966, #202c43);
}
.if-recent-book.ochre .if-recent-book-art { background: linear-gradient(160deg, #6c5031, #382b1e); }

.if-recent-book-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  padding: 0.95rem 1rem;
}

.if-recent-book-copy > span {
  color: var(--forge-amber);
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.if-recent-book-copy h3 {
  margin: 0.45rem 0 0 !important;
  overflow: hidden;
  font-family: var(--forge-serif) !important;
  font-size: 1.13rem !important;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.if-recent-book-copy p {
  display: -webkit-box;
  margin: 0.45rem 0 0.75rem;
  overflow: hidden;
  font-size: 0.76rem;
  line-height: 1.5;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.if-recent-book-copy small {
  margin-top: auto;
  color: var(--forge-muted);
  font-size: 0.67rem;
  line-height: 1.45;
}

.if-recent-book-copy time {
  margin-top: 0.28rem;
  color: var(--forge-subtle);
  font-size: 0.65rem;
}

.st-key-story_workspace_shell,
.st-key-prompt_workspace_shell {
  margin-top: 0.55rem;
  padding: 0.85rem;
  border: 1px solid rgba(52, 68, 77, 0.82);
  border-radius: 14px;
  background: rgba(9, 17, 23, 0.48);
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.025);
}

/* Friendly ways to begin, replacing phase/status report cards on the home page. */
.if-path-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.85rem;
  margin: 0.6rem 0 1.6rem;
}

.if-path-card {
  min-height: 9.2rem;
  padding: 1.1rem 1.15rem;
  border: 1px solid var(--forge-border);
  border-radius: 18px;
  background:
    linear-gradient(145deg, rgba(217, 170, 98, 0.075), transparent 48%),
    rgba(20, 31, 39, 0.94);
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.04), 0 18px 45px rgba(0, 0, 0, 0.12);
}

.if-path-icon {
  color: var(--forge-amber);
  font: 700 1.15rem/1 var(--forge-serif);
}

.if-path-title {
  margin-top: 0.8rem;
  color: var(--forge-text);
  font: 700 1.02rem/1.3 var(--forge-serif);
}

.if-path-copy {
  margin-top: 0.45rem;
  color: var(--forge-muted);
  font-size: 0.84rem;
  line-height: 1.62;
}

/* Project covers */
.if-book-cover {
  position: relative;
  display: grid;
  grid-template-columns: 2.65rem minmax(0, 1fr);
  min-height: 17.5rem;
  margin: 0.35rem 0 0.7rem;
  overflow: hidden;
  border: 1px solid rgba(235, 207, 155, 0.31);
  border-radius: 8px 17px 17px 8px;
  color: #fff7e8;
  box-shadow:
    inset 0 1px rgba(255, 255, 255, 0.12),
    inset -12px 0 24px rgba(0, 0, 0, 0.1),
    0 18px 36px rgba(0, 0, 0, 0.24);
  transform: translateY(0);
  transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease;
}

.if-book-cover::after {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  opacity: 0.18;
  background:
    repeating-linear-gradient(0deg, transparent 0 3px, rgba(255, 255, 255, 0.11) 3px 4px),
    repeating-linear-gradient(90deg, transparent 0 5px, rgba(0, 0, 0, 0.1) 5px 6px);
  mix-blend-mode: soft-light;
}

.if-book-cover:hover {
  transform: translateY(-3px);
  border-color: rgba(235, 207, 155, 0.56);
  box-shadow: 0 24px 46px rgba(0, 0, 0, 0.3);
}

.if-book-cover.garnet { background: linear-gradient(135deg, #6f3038, #3e202a 72%); }
.if-book-cover.forest { background: linear-gradient(135deg, #31564e, #1d3735 72%); }
.if-book-cover.indigo { background: linear-gradient(135deg, #414368, #282943 72%); }
.if-book-cover.ochre { background: linear-gradient(135deg, #735332, #46331f 72%); }

.if-book-cover.is-selected {
  border-color: rgba(239, 193, 116, 0.82);
  box-shadow: 0 0 0 2px rgba(217, 170, 98, 0.14), 0 25px 50px rgba(0, 0, 0, 0.32);
}

.if-book-cover.is-archived { filter: saturate(0.45); opacity: 0.76; }

.if-book-spine {
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 0.25rem;
  border-right: 1px solid rgba(255, 238, 201, 0.2);
  background: rgba(20, 11, 14, 0.28);
  color: rgba(255, 238, 201, 0.76);
  font: 650 0.66rem/1 var(--forge-serif);
  letter-spacing: 0.1em;
}

.if-book-spine span:first-child { writing-mode: vertical-rl; }

.if-book-face {
  z-index: 1;
  display: flex;
  min-width: 0;
  flex-direction: column;
  padding: 1.15rem 1.25rem 1.05rem;
}

.if-book-state {
  align-self: flex-start;
  padding: 0.22rem 0.55rem;
  border: 1px solid rgba(255, 241, 211, 0.24);
  border-radius: 999px;
  color: rgba(255, 244, 221, 0.82);
  font-size: 0.68rem;
  letter-spacing: 0.055em;
}

.if-book-ornament {
  margin-top: 1.2rem;
  color: #e8c485;
  font: 700 1.25rem/1 var(--forge-serif);
}

.if-book-face h3 {
  margin: 0.65rem 0 0 !important;
  color: #fff8e9 !important;
  font-family: var(--forge-serif) !important;
  font-size: clamp(1.35rem, 2.2vw, 1.78rem) !important;
  line-height: 1.17 !important;
  overflow-wrap: anywhere;
}

.if-book-face p {
  display: -webkit-box;
  margin: 0.68rem 0 1rem;
  overflow: hidden;
  color: rgba(255, 244, 223, 0.78);
  font-size: 0.84rem;
  line-height: 1.62;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.if-book-folio {
  margin-top: auto;
  padding-top: 0.72rem;
  border-top: 1px solid rgba(255, 242, 214, 0.16);
  color: rgba(255, 242, 214, 0.88);
  font-size: 0.72rem;
  line-height: 1.45;
}

.if-book-date {
  margin-top: 0.32rem;
  color: #e7d3af;
  font-size: 0.68rem;
}

.if-shelf-plank {
  height: 0.8rem;
  margin: -0.3rem 0 1.65rem;
  border-top: 1px solid #876546;
  border-bottom: 3px solid #211611;
  border-radius: 4px;
  background: linear-gradient(180deg, #735036, #432d20);
  box-shadow: 0 10px 15px rgba(0, 0, 0, 0.32);
}

/* First-run shelf */
.if-empty-shelf {
  position: relative;
  margin: 0.55rem 0 1rem;
  padding: clamp(1.2rem, 3vw, 1.8rem);
  overflow: hidden;
  border: 1px dashed rgba(217, 170, 98, 0.46);
  border-radius: 24px;
  background:
    radial-gradient(circle at 85% 20%, rgba(217, 170, 98, 0.12), transparent 14rem),
    rgba(11, 20, 27, 0.78);
  text-align: center;
}

.if-empty-shelf-stars { color: var(--forge-amber); letter-spacing: 0.25em; }

.if-empty-shelf h2 {
  max-width: 36rem;
  margin: 0.55rem auto 0 !important;
  font-size: clamp(1.35rem, 3vw, 1.9rem) !important;
}

.if-empty-shelf p {
  max-width: 38rem;
  margin: 0.55rem auto 0.9rem;
  line-height: 1.7;
}

.if-empty-book {
  width: min(14rem, 62vw);
  height: 2.75rem;
  margin: 0 auto;
  border: 1px solid rgba(239, 202, 139, 0.6);
  border-radius: 4px 12px 12px 4px;
  background: linear-gradient(130deg, #6f3038, #3c2028);
  color: rgba(255, 245, 223, 0.86);
  box-shadow: 0 9px 18px rgba(0, 0, 0, 0.25);
}

.if-empty-book span {
  display: block;
  padding: 0.68rem 1rem;
  font: 650 0.86rem/1 var(--forge-serif);
  letter-spacing: 0.08em;
}

/* The selected project's two-page spread. */
.if-open-book {
  position: relative;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin: 0.7rem 0 1.25rem;
  filter: drop-shadow(0 24px 30px rgba(0, 0, 0, 0.3));
}

.if-open-book::after {
  content: "";
  position: absolute;
  inset: 0 auto 0 50%;
  width: 2px;
  background: linear-gradient(180deg, transparent, rgba(81, 56, 39, 0.28), transparent);
  box-shadow: 0 0 16px rgba(56, 37, 27, 0.28);
}

.if-open-page {
  min-height: 19rem;
  padding: clamp(1.35rem, 3.2vw, 2.35rem);
  color: var(--forge-paper-ink);
  background:
    radial-gradient(circle at 20% 0%, rgba(255, 255, 255, 0.65), transparent 20rem),
    repeating-linear-gradient(0deg, transparent 0 25px, rgba(82, 59, 43, 0.025) 25px 26px),
    var(--forge-paper);
}

.if-open-page-left {
  border-radius: 18px 4px 4px 18px;
  box-shadow: inset -16px 0 24px rgba(74, 51, 35, 0.08);
}

.if-open-page-right {
  border-radius: 4px 18px 18px 4px;
  box-shadow: inset 16px 0 24px rgba(74, 51, 35, 0.08);
}

.if-open-running {
  color: #6a4b35;
  font-size: 0.69rem;
  font-weight: 700;
  letter-spacing: 0.12em;
}

.if-open-mark {
  margin-top: 1.45rem;
  color: #a36f3d;
  font: 700 1.15rem/1 var(--forge-serif);
}

.if-open-page h2 {
  margin: 0.65rem 0 0 !important;
  color: #362820 !important;
  font-family: var(--forge-serif) !important;
  font-size: clamp(1.65rem, 3.2vw, 2.55rem) !important;
  line-height: 1.13 !important;
  overflow-wrap: anywhere;
}

.if-open-page p {
  margin-top: 0.95rem;
  color: #695446;
  line-height: 1.75;
}

.if-open-date {
  margin-top: 1.3rem;
  color: #72513a;
  font-size: 0.74rem;
}

.if-book-sections {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.65rem;
  margin-top: 1.6rem;
}

.if-book-section {
  min-height: 5.2rem;
  padding: 0.85rem;
  border: 1px solid rgba(95, 69, 49, 0.16);
  border-radius: 12px;
  background: rgba(255, 252, 241, 0.38);
}

.if-book-section span {
  display: block;
  color: #765c49;
  font-size: 0.76rem;
}

.if-book-section strong {
  display: block;
  margin-top: 0.35rem;
  color: #3e2c23;
  font: 700 1.65rem/1 var(--forge-serif);
}

.if-open-progress {
  margin-top: 0.75rem;
  color: #6f5846;
  font-size: 0.76rem;
}

.if-open-progress span {
  display: block;
  margin-top: 0.18rem;
  color: #765541;
}

.st-key-mobile_navigation {
  display: none;
}

@media (max-width: 1279px) {
  [data-testid="stMainBlockContainer"] { padding-inline: 1rem; }
  [data-testid="stSidebar"] { min-width: 15rem; max-width: 15rem; }
  [data-testid="stHeader"] {
    min-height: 2.8rem;
    height: 2.8rem;
    background: rgba(9, 16, 23, 0.92);
  }
  .st-key-app_topbar { top: 3.05rem; }
  .if-breadcrumb small { display: none; }

  .st-key-home_recent_grid [data-testid="stHorizontalBlock"],
  .st-key-home_quick_create [data-testid="stHorizontalBlock"] {
    flex-flow: row wrap;
  }

  .st-key-home_recent_grid [data-testid="stColumn"],
  .st-key-home_quick_create [data-testid="stColumn"] {
    width: calc(50% - 0.4rem) !important;
    flex: 1 1 calc(50% - 0.4rem) !important;
  }

  .st-key-story_workspace_shell
    [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]:nth-child(3)) {
    flex-flow: row wrap;
  }

  .st-key-story_workspace_shell
    [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]:nth-child(3))
    > [data-testid="stColumn"] {
    min-width: calc(50% - 0.45rem);
    flex: 1 1 calc(50% - 0.45rem) !important;
  }
}

@media (max-width: 900px) {
  [data-testid="stMainBlockContainer"] { padding: 1.3rem 0.9rem 4rem; }
  .if-cap-grid { grid-template-columns: 1fr; }
  .if-cap-card { min-height: auto; }
  .if-path-grid { grid-template-columns: 1fr; }
  .if-path-card { min-height: auto; }
  h1 { font-size: clamp(2rem, 9vw, 3rem) !important; }

  .st-key-prompt_workspace_shell > div > [data-testid="stHorizontalBlock"] {
    flex-direction: column;
  }

  .st-key-prompt_workspace_shell > div > [data-testid="stHorizontalBlock"]
    > [data-testid="stColumn"] {
    width: 100% !important;
    flex: 1 1 100% !important;
  }
}

@media (max-width: 767px) {
  [data-testid="stMainBlockContainer"] {
    padding: 0.7rem 0.72rem 6.4rem;
  }

  [data-testid="stMain"] [data-testid="stHorizontalBlock"] {
    flex-direction: column;
  }

  [data-testid="stMain"] [data-testid="stColumn"] {
    width: 100% !important;
    min-width: 100% !important;
    flex: 1 1 100% !important;
  }

  .st-key-app_topbar {
    position: sticky;
    top: 3.05rem;
    padding: 0.35rem;
  }

  .st-key-app_topbar [data-testid="stHorizontalBlock"] {
    flex-flow: row nowrap !important;
  }

  .st-key-app_topbar [data-testid="stColumn"] {
    display: none;
    width: auto !important;
    min-width: 0 !important;
    flex: 0 0 auto !important;
  }

  .st-key-app_topbar [data-testid="stColumn"]:first-child,
  .st-key-app_topbar [data-testid="stColumn"]:nth-child(4) {
    display: block;
  }

  .st-key-app_topbar [data-testid="stColumn"]:first-child {
    width: 2.75rem !important;
    min-width: 2.75rem !important;
    flex: 0 0 2.75rem !important;
  }

  .st-key-app_topbar [data-testid="stColumn"]:nth-child(4) {
    margin-left: auto;
    flex: 1 1 auto !important;
  }

  .st-key-app_topbar .if-breadcrumb span,
  .st-key-app_topbar .if-breadcrumb i,
  .st-key-app_topbar .if-breadcrumb small {
    display: none;
  }

  .st-key-app_topbar .if-breadcrumb {
    min-height: 2.45rem;
    justify-content: flex-start;
    padding-inline: 0.65rem;
  }

  .st-key-app_topbar .if-breadcrumb strong {
    display: block;
    font-size: 0.78rem;
  }

  .st-key-mobile_navigation {
    position: fixed;
    inset: auto 0 0 0;
    z-index: 1000;
    display: block;
    padding: 0.42rem 0.35rem max(0.42rem, env(safe-area-inset-bottom));
    border-top: 1px solid rgba(132, 109, 73, 0.72);
    background: rgba(9, 16, 23, 0.97);
    backdrop-filter: blur(18px);
    box-shadow: 0 -12px 30px rgba(0, 0, 0, 0.3);
  }

  .st-key-mobile_navigation [data-testid="stHorizontalBlock"] {
    flex-flow: row nowrap !important;
    gap: 0.28rem;
  }

  .st-key-mobile_navigation [data-testid="stColumn"] {
    display: block;
    width: 20% !important;
    min-width: 0 !important;
    max-width: 20% !important;
    flex: 1 1 20% !important;
  }

  .st-key-mobile_navigation .stButton > button {
    min-height: 3.25rem;
    padding: 0.3rem 0.15rem;
    border-radius: 8px;
    font-size: 0.68rem;
    line-height: 1.05;
  }

  .st-key-mobile_navigation [data-testid="stPopover"] > button {
    min-height: 3.25rem;
    width: 100%;
    padding: 0.3rem 0.15rem;
    border-radius: 8px;
    font-size: 0.68rem;
    white-space: nowrap;
  }

  .st-key-home_recent_grid [data-testid="stColumn"],
  .st-key-home_quick_create [data-testid="stColumn"] {
    width: 100% !important;
    min-width: 100% !important;
    flex: 1 1 100% !important;
  }

  .st-key-home_hero [data-testid="stImage"] img {
    min-height: 13rem;
  }

  .if-book-cover { min-height: 15.5rem; }
  .if-open-book { grid-template-columns: 1fr; }
  .if-open-book::after { display: none; }
  .if-open-page { min-height: auto; }
  .if-open-page-left { border-radius: 17px 17px 4px 4px; box-shadow: none; }
  .if-open-page-right { border-radius: 4px 4px 17px 17px; box-shadow: none; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
  }
}
</style>
"""


def apply_theme() -> None:
    """Inject the small CSS layer that Streamlit's theme config cannot express."""

    st.markdown(_CSS, unsafe_allow_html=True)
