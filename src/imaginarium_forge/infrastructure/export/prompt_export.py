"""Prompt export (spec §8.3/§8.4).

Both exporters consume one `ExportBundle` so Markdown and JSON can never
disagree about what was compiled. Serialization is stable (canonical field
order; sorted keys in JSON) — a byte-identical bundle exports byte-identically.

Privacy rule (§7.3/§8.4): the checkpoint is referenced by FILENAME only;
absolute local paths never leave the machine via exports.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from imaginarium_forge.domain.prompt.ast import PromptAST
from imaginarium_forge.domain.prompt.blocks import CompiledBlocks
from imaginarium_forge.domain.prompt.compilation import CharacterLockInput, StyleInput
from imaginarium_forge.domain.prompt.conflicts import Conflict
from imaginarium_forge.domain.prompt.lint import LintLevel, LintReport
from imaginarium_forge.domain.prompt.profiles import ResolvedProfile


class ExportRefs(BaseModel):
    """Human-context references. Filenames only — never absolute paths."""

    model_config = ConfigDict(frozen=True)

    project_name: str = ""
    prompt_project_title: str = ""
    character_name: str = ""
    character_version: str = ""
    outfit_name: str = ""
    style_name: str = ""
    style_version: str = ""
    checkpoint_filename: str = ""


class ExportBundle(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_text: str
    refs: ExportRefs
    ast: PromptAST
    profile: ResolvedProfile
    character: CharacterLockInput
    style: StyleInput
    blocks: CompiledBlocks
    conflicts: tuple[Conflict, ...] = ()
    lint: LintReport = LintReport()
    created_at: str = ""


def _fence(text: str, lang: str = "text") -> list[str]:
    """A-13 §16.1: user text may contain ``` — grow the fence until safe."""
    longest = 0
    run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    marks = "`" * max(3, longest + 1)
    return [f"{marks}{lang}", text, marks]


def _lint_lines(report: LintReport) -> list[str]:
    if not report.findings:
        return ["（無檢查項目觸發）"]
    icon = {LintLevel.ERROR: "❌", LintLevel.WARNING: "⚠️", LintLevel.INFORMATION: "ℹ️"}
    return [
        f"- {icon[f.level]} [{f.check.value}] {f.message_zh_tw}"
        + (f"（{f.detail}）" if f.detail else "")
        for f in report.findings
    ]


def export_markdown(bundle: ExportBundle) -> str:
    """§8.3 field set, in a fixed section order."""
    b = bundle.blocks
    refs = bundle.refs
    conflict_lines = (
        [
            f"- [{c.severity.value}｜{c.code.value}] {c.message_zh_tw}"
            for c in bundle.conflicts
        ]
        if bundle.conflicts
        else ["（無衝突）"]
    )
    notes = b.generation_notes or "（無）"
    sections = [
        "# Visual Prompt Export",
        "",
        f"- 匯出時間：{bundle.created_at}",
        f"- 編譯器版本：{bundle.profile.compiler_version}",
        f"- Profile 快照 SHA-256：`{bundle.profile.sha256()}`",
        f"- Checkpoint：{refs.checkpoint_filename or '（未指定）'}",
        "",
        "## 專案與 Canon 參照",
        f"- 專案：{refs.project_name or '—'}／提示專案：{refs.prompt_project_title or '—'}",
        f"- 角色：{refs.character_name or '—'}（版本 {refs.character_version or '—'}）"
        f"／服裝：{refs.outfit_name or '—'}",
        f"- 風格：{refs.style_name or '—'}（版本 {refs.style_version or '—'}）",
        "",
        "## 原始繁中描述",
        "",
        *_fence(bundle.source_text or "（空）"),
        "",
        "## Prompt AST（canonical JSON）",
        "",
        *_fence(bundle.ast.canonical_dump(), "json"),
        "",
        "## Resolved Profile",
        f"- 方言：{bundle.profile.dialect_id}@{bundle.profile.dialect_version}",
        f"- Checkpoint profile：{bundle.profile.checkpoint_profile_id or '（無）'}",
        f"- Preset：{bundle.profile.preset_id or '（無）'}",
        f"- experimental：{bundle.profile.experimental}",
        (
            f"- ⚠ {bundle.profile.low_confidence_warning}"
            if bundle.profile.low_confidence_warning
            else "- 警告：（無）"
        ),
        "",
        "## 區塊",
        f"- 角色鎖定：{b.character_lock_block or '—'}",
        f"- 服裝：{b.outfit_block or '—'}",
        f"- 姿勢／表情：{b.pose_expression_block or '—'}",
        f"- 鏡頭：{b.camera_block or '—'}",
        f"- 環境：{b.environment_block or '—'}",
        f"- 光照：{b.lighting_block or '—'}",
        f"- 風格：{b.style_block or '—'}",
        f"- 品質：{b.quality_block or '—'}",
        "",
        "## Positive Prompt",
        "",
        *_fence(b.positive_prompt),
        "",
        "## Negative Prompt",
        "",
        *_fence(b.negative_prompt),
        "",
        "## English Natural-Language Prompt",
        "",
        *_fence(b.natural_language_prompt),
        "",
        "## 覆蓋率",
        f"- 身分覆蓋率（解析時）：{b.identity_coverage.display}"
        + (
            f"（缺：{'、'.join(b.identity_coverage.missing)}）"
            if b.identity_coverage.missing
            else ""
        ),
        f"- 風格覆蓋率（解析時）：{b.style_coverage.display}"
        + (f"（缺：{'、'.join(b.style_coverage.missing)}）" if b.style_coverage.missing else ""),
        f"- 身分覆蓋率（最終輸出）：{b.identity_coverage_compiled.display}"
        + (
            f"（缺：{'、'.join(b.identity_coverage_compiled.missing)}）"
            if b.identity_coverage_compiled.missing
            else ""
        ),
        f"- 風格覆蓋率（最終輸出）：{b.style_coverage_compiled.display}"
        + (
            f"（缺：{'、'.join(b.style_coverage_compiled.missing)}）"
            if b.style_coverage_compiled.missing
            else ""
        ),
        "",
        "## 衝突",
        *conflict_lines,
        "",
        "## Linter",
        *_lint_lines(bundle.lint),
        "",
        "## 生成備註",
        "",
        *_fence(notes),
        "",
    ]
    return "\n".join(sections)


def export_json(bundle: ExportBundle) -> str:
    """§8.4 machine-readable export; stable key order; no absolute paths."""
    payload = {
        "source_input": {
            "language": bundle.ast.user_intent.source_language,
            "text": bundle.source_text,
        },
        "prompt_ast": json.loads(bundle.ast.canonical_dump()),
        "resolved_canon": bundle.character.model_dump(mode="json"),
        "resolved_style": bundle.style.model_dump(mode="json"),
        "resolved_profile_snapshot": {
            "sha256": bundle.profile.sha256(),
            "profile": json.loads(bundle.profile.canonical_dump()),
        },
        "compiled_blocks": bundle.blocks.model_dump(mode="json"),
        "positive_prompt": bundle.blocks.positive_prompt,
        "negative_prompt": bundle.blocks.negative_prompt,
        "natural_language_prompt": bundle.blocks.natural_language_prompt,
        # A2-15: explicit resolution-time AND final-compiled coverage — the
        # JSON and Markdown exports must expose the same categories
        "coverage": {
            "identity_resolution": bundle.blocks.identity_coverage.model_dump(
                mode="json"
            ),
            "style_resolution": bundle.blocks.style_coverage.model_dump(mode="json"),
            "identity_compiled": (
                bundle.blocks.identity_coverage_compiled.model_dump(mode="json")
            ),
            "style_compiled": (
                bundle.blocks.style_coverage_compiled.model_dump(mode="json")
            ),
        },
        "conflicts": [c.model_dump(mode="json") for c in bundle.conflicts],
        "lint": bundle.lint.model_dump(mode="json"),
        "reproducibility": {
            "compiler_version": bundle.profile.compiler_version,
            "profile_sha256": bundle.profile.sha256(),
            "checkpoint_filename": bundle.refs.checkpoint_filename,
            "parser_provider": bundle.ast.metadata.parser_provider,
            "parser_model": bundle.ast.metadata.parser_model,
            "created_at": bundle.created_at,
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)


# ---------------------------------------------------------------- Gate A A-13


def export_blocked_diagnostic(bundle: ExportBundle) -> str:
    """§16.3: diagnostics for a BLOCKED outcome — conflicts + lint + inputs,
    with final prompt strings deliberately excluded."""
    conflict_lines = (
        [
            f"- [{c.severity.value}｜{c.code.value}] {c.message_zh_tw}"
            for c in bundle.conflicts
        ]
        if bundle.conflicts
        else ["（無衝突）"]
    )
    sections = [
        "# blocked_diagnostic_report",
        "",
        "本報告來自「阻斷」的編譯結果：僅含診斷資訊，**不含**最終提示字串。",
        "",
        f"- 匯出時間：{bundle.created_at}",
        f"- 編譯器版本：{bundle.profile.compiler_version}",
        f"- 方言：{bundle.profile.dialect_id}@{bundle.profile.dialect_version}",
        f"- Checkpoint：{bundle.refs.checkpoint_filename or '（未指定）'}",
        "",
        "## 原始繁中描述",
        "",
        *_fence(bundle.source_text or "（空）"),
        "",
        "## 衝突",
        *conflict_lines,
        "",
        "## Linter",
        *_lint_lines(bundle.lint),
        "",
        "## 覆蓋率（解析時）",
        f"- 身分：{bundle.blocks.identity_coverage.display}",
        f"- 風格：{bundle.blocks.style_coverage.display}",
        "",
    ]
    report = "\n".join(sections)
    # hard guarantee: no final prompt content may leak into the diagnostic
    for leak in (
        bundle.blocks.positive_prompt,
        bundle.blocks.negative_prompt,
        bundle.blocks.natural_language_prompt,
    ):
        if leak and leak in report:
            raise ValueError("diagnostic export must not contain final prompts")
    return report


def bundle_from_variant(
    *,
    variant_json_blocks: str,
    variant_lint_json: str,
    variant_conflicts_json: str,
    source_text: str,
    refs: ExportRefs,
    ast: PromptAST,
    profile: ResolvedProfile,
    character: CharacterLockInput,
    style: StyleInput,
    created_at: str,
) -> ExportBundle:
    """§16.2: exports must come from the PERSISTED variant snapshot, not the
    live UI state. The caller loads the variant row and hands its stored JSON
    here; nothing is recompiled."""
    import json as _json

    from imaginarium_forge.domain.prompt.blocks import CompiledBlocks
    from imaginarium_forge.domain.prompt.conflicts import Conflict

    blocks = CompiledBlocks.model_validate_json(variant_json_blocks)
    lint = LintReport.model_validate_json(variant_lint_json)
    conflicts = tuple(
        Conflict.model_validate(item)
        for item in _json.loads(variant_conflicts_json or "[]")
    )
    return ExportBundle(
        source_text=source_text,
        refs=refs,
        ast=ast,
        profile=profile,
        character=character,
        style=style,
        blocks=blocks,
        conflicts=conflicts,
        lint=lint,
        created_at=created_at,
    )
