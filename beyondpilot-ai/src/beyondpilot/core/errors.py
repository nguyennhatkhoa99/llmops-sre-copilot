"""Error codes and the single exception type the service raises.

Code anywhere in the service raises ``AppError(ErrorCode.X)``. The HTTP layer turns
it into ``{"code": ..., "message": ...}`` with the status from ``CATALOGUE``.
The optional ``detail`` is for logs only and never reaches the client.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    INVALID_INPUT = "INVALID_INPUT"
    UNSAFE_INPUT = "UNSAFE_INPUT"
    CONFIG_INVALID = "CONFIG_INVALID"
    SLOT_NOT_FOUND = "SLOT_NOT_FOUND"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    FEATURE_UNAVAILABLE = "FEATURE_UNAVAILABLE"
    NOT_READY = "NOT_READY"
    INTERNAL = "INTERNAL"


@dataclass(frozen=True)
class ErrorSpec:
    status: int
    message: str


CATALOGUE: dict[ErrorCode, ErrorSpec] = {
    ErrorCode.UNAUTHENTICATED: ErrorSpec(401, "Thiếu API key."),
    ErrorCode.FORBIDDEN: ErrorSpec(403, "API key không hợp lệ."),
    ErrorCode.INVALID_INPUT: ErrorSpec(422, "Dữ liệu gửi lên không hợp lệ."),
    ErrorCode.UNSAFE_INPUT: ErrorSpec(400, "Nội dung bị chặn vì không an toàn."),
    ErrorCode.CONFIG_INVALID: ErrorSpec(500, "Cấu hình dịch vụ không hợp lệ."),
    ErrorCode.SLOT_NOT_FOUND: ErrorSpec(500, "Không tìm thấy bản cài cho slot."),
    ErrorCode.UPSTREAM_UNAVAILABLE: ErrorSpec(503, "Dịch vụ phía sau đang không phản hồi."),
    ErrorCode.UPSTREAM_TIMEOUT: ErrorSpec(504, "Dịch vụ phía sau phản hồi quá chậm."),
    ErrorCode.UPSTREAM_ERROR: ErrorSpec(502, "Dịch vụ phía sau trả lỗi."),
    ErrorCode.VALIDATION_FAILED: ErrorSpec(422, "Kết quả không qua bước kiểm tra số liệu."),
    ErrorCode.FEATURE_UNAVAILABLE: ErrorSpec(501, "Tính năng chưa được cài đặt."),
    ErrorCode.NOT_READY: ErrorSpec(503, "Dịch vụ chưa sẵn sàng."),
    ErrorCode.INTERNAL: ErrorSpec(500, "Lỗi nội bộ."),
}


class AppError(Exception):
    """The only exception type that should cross module boundaries."""

    def __init__(self, code: ErrorCode, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail or CATALOGUE[code].message}")

    @property
    def status(self) -> int:
        return CATALOGUE[self.code].status

    @property
    def message(self) -> str:
        return CATALOGUE[self.code].message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "message": self.message}
