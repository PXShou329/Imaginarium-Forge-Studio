"""Sanitized translation errors that never stringify author content or secrets."""

from __future__ import annotations


class OfflineTranslationError(RuntimeError):
    """Base error carrying a stable code and fixed, author-facing message."""

    def __init__(self, code: str, user_message: str) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message


class TranslationPackMissingError(OfflineTranslationError):
    def __init__(self) -> None:
        super().__init__(
            "translation_pack_missing",
            "尚未安裝可用的離線翻譯模型包。",
        )


class TranslationPackCorruptError(OfflineTranslationError):
    def __init__(self, code: str = "translation_pack_corrupt") -> None:
        super().__init__(code, "離線翻譯模型包未通過完整性驗證。")


class TranslationInstallCancelled(OfflineTranslationError):
    def __init__(self) -> None:
        super().__init__("translation_install_cancelled", "模型包安裝已停止。")


class TranslationCancelled(OfflineTranslationError):
    """Internal cooperative-cancellation signal from a bounded model chunk."""

    def __init__(self) -> None:
        super().__init__("translation_cancelled", "本機翻譯已停止。")


class TranslationBackendError(OfflineTranslationError):
    def __init__(self, code: str = "translation_backend_unavailable") -> None:
        super().__init__(code, "本機翻譯引擎目前無法使用。")


class TranslationExecutionError(OfflineTranslationError):
    """Recoverable execution error with non-repr partial output."""

    def __init__(
        self,
        *,
        partial_text: str,
        completed_segments: int,
        total_segments: int,
        code: str = "translation_execution_failed",
    ) -> None:
        super().__init__(code, "本機翻譯未完成；已完成的段落仍保留。")
        self.partial_text = partial_text
        self.completed_segments = completed_segments
        self.total_segments = total_segments


__all__ = [
    "OfflineTranslationError",
    "TranslationBackendError",
    "TranslationCancelled",
    "TranslationExecutionError",
    "TranslationInstallCancelled",
    "TranslationPackCorruptError",
    "TranslationPackMissingError",
]
