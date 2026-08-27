"""Normalized application-layer errors (UI never sees SQLAlchemy exceptions)."""

from __future__ import annotations

from dataclasses import dataclass


class ApplicationError(Exception):
    """Base for all service-layer failures shown to the UI."""


class NotFoundError(ApplicationError):
    pass


class ValidationFailedError(ApplicationError):
    pass


class StaleStoryMemoryError(ApplicationError):
    """A proposal was reviewed against a different memory projection."""

    def __init__(
        self,
        *,
        proposal_id: str,
        expected_fingerprint: str,
        current_fingerprint: str,
    ) -> None:
        self.proposal_id = proposal_id
        self.expected_fingerprint = expected_fingerprint
        self.current_fingerprint = current_fingerprint
        super().__init__(
            f"故事記憶提案 {proposal_id} 已過期：先前故事狀態在提案後發生變更。"
            "請重新產生提案並再次確認。"
        )


class StoryMemoryConflictError(ApplicationError):
    """An accepted-memory proposal would overwrite or mis-target history."""

    def __init__(self, *, proposal_id: str, messages: tuple[str, ...]) -> None:
        self.proposal_id = proposal_id
        self.messages = messages
        detail = "；".join(messages) or "提案與目前故事記憶衝突"
        super().__init__(f"故事記憶提案 {proposal_id} 無法接受：{detail}")


class ConflictError(ApplicationError):
    """Integrity/uniqueness/foreign-key conflicts, normalized."""


class CompilationBlockedError(ApplicationError):
    """A blocked compilation cannot be persisted, accepted, exported, or
    used for experiments (Gate A A-04)."""


class StaleCompilationInputError(ApplicationError):
    """Inputs changed after compilation; the action requires a fresh compile
    whose fingerprint matches the current inputs (Gate A A-01/A-04)."""


class ResolutionInputMismatchError(ApplicationError):
    """A2-02: a ResolutionOutcome was paired with a compile request whose
    inputs differ from what was actually resolved (foreign resolution reuse,
    eligibility-verdict reuse, changed AST/mode/selection)."""


class PreflightConfirmationRequiredError(ApplicationError):
    """A2-16: deterministic content preflight flagged obvious adult markers
    under a non-adult mode; an explicit nonsexual acknowledgement (tied to the
    exact preflight inputs) is required before compilation may proceed."""


class NoValidVariantError(ApplicationError):
    """Version acceptance requires at least one valid (non-blocked) compiled
    variant (Gate A A-04)."""


class PlanningChainMismatchError(ApplicationError):
    """A3-R02: the selected planning versions do not form ONE coherent chain.

    Project ownership alone proved insufficient: an Outline version from a
    DIFFERENT outline in the same project, and a Chapter Plan from a different
    chapter, were both accepted and silently used for generation. The message
    names the failing relationship rather than saying only "invalid".
    """

    def __init__(
        self,
        *,
        relationship: str,
        expected_parent: str,
        received_version: str,
        detail: str = "",
    ) -> None:
        self.relationship = relationship
        self.expected_parent = expected_parent
        self.received_version = received_version
        message = (
            f"規劃鏈不一致（{relationship}）："
            f"預期歸屬於 {expected_parent}，但收到的版本是 {received_version}"
        )
        if detail:
            message = f"{message}；{detail}"
        super().__init__(message)


class UnacceptedPlanningError(ApplicationError):
    """A3-R12: accepted mode requires accepted planning versions."""

    def __init__(self, *, entity_label: str, version_id: str) -> None:
        self.entity_label = entity_label
        self.version_id = version_id
        super().__init__(
            f"{entity_label}版本 {version_id} 尚未被接受。"
            "請先接受該版本，或改用 Preview 模式並明確選定草稿版本。"
        )


class RequiredContextOverflowError(ApplicationError):
    """A3-R11: the never-trimmed blocks alone exceed the payload budget.

    Silently sending an oversized payload was the previous behaviour, because
    the budget only measured block bodies and ignored the rendered headings
    and the system message entirely.
    """

    def __init__(self, *, budget: int, required_size: int) -> None:
        self.budget = budget
        self.required_size = required_size
        super().__init__(
            f"必要上下文（Hard Canon、禁止揭露、Scene Card）與系統契約本身即需 "
            f"{required_size} 字元，超過預算 {budget} 字元。"
            "這些區塊不可裁切；請提高預算或精簡 Scene Card。"
        )


class SourcePlanningChainUnavailableError(ApplicationError):
    """A3-R03: an ordinary revision cannot reconstruct its historical context.

    Fails closed on purpose. Substituting the latest accepted Requirement,
    Bible, Outline, Chapter Plan or Scene Card would silently change the
    meaning and provenance of the revision. Recovery is possible, but only
    through the separate, explicitly-confirmed recovery-rebase workflow.
    """

    reason_code = "source_planning_chain_unavailable"

    def __init__(self, *, missing_version_ids: tuple[str, ...]) -> None:
        self.missing_version_ids = missing_version_ids
        listed = "、".join(missing_version_ids) or "（未知）"
        super().__init__(
            "來源草稿的原始規劃版本鏈無法重建，普通修訂不能繼續，"
            "因為其歷史生成上下文並不完整。"
            f"缺失或無法讀取的版本：{listed}。"
            "若確定原始資料已損毀，請改用「以新規劃鏈復原（Recovery Rebase）」流程。"
        )


@dataclass(frozen=True, order=True, slots=True)
class MissingStoryExportReference:
    """One exact historical row named by a persisted StoryExport snapshot."""

    reference_type: str
    reference_id: str


class StoryExportReferencesUnavailableError(ApplicationError):
    """S8.3: an export cannot be reproduced because exact history is missing."""

    def __init__(
        self,
        *,
        export_id: str,
        missing_references: tuple[MissingStoryExportReference, ...],
    ) -> None:
        self.export_id = export_id
        self.missing_references = tuple(sorted(set(missing_references)))
        listed = "、".join(
            f"{ref.reference_type}:{ref.reference_id}"
            for ref in self.missing_references
        )
        super().__init__(
            f"故事匯出 {export_id} 無法從歷史快照重建；"
            f"缺失參照：{listed or '（未知）'}。"
        )


class StoryExportIntegrityError(ApplicationError):
    """S8.3: persisted export evidence is inconsistent or unverifiable."""

    def __init__(self, *, export_id: str, detail: str) -> None:
        self.export_id = export_id
        self.detail = detail
        super().__init__(f"故事匯出 {export_id} 的持久化快照無法驗證：{detail}")
