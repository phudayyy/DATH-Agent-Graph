"""Kỷ luật thời gian. Mọi mốc thời gian trong brain đều tz-aware và ở UTC.

Vì sao có file riêng cho mấy dòng này: spike Ngày 2 (ARCHITECTURE.md §15) cho thấy
lỗi bi-temporal đắt nhất đều là loại **hỏng âm thầm** — so sánh một `datetime` naive
với một `datetime` aware ném `TypeError` (còn may), nhưng so sánh hai mốc lệch múi giờ
thì **chạy êm và trả kết quả sai**. Ép mọi thứ qua một cửa duy nhất ở đây.

Đặt tên `clock.py` chứ không `time.py` để không che module chuẩn `time`.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Thời điểm hiện tại, tz-aware, UTC. Dùng thay cho `datetime.now()`."""
    return datetime.now(UTC)


def ensure_utc(value: datetime | None) -> datetime | None:
    """Chuẩn hoá về UTC tz-aware. `None` đi qua nguyên vẹn.

    `None` KHÔNG bị coi là lỗi: trong mô hình bi-temporal nó mang nghĩa
    **vô hạn** (`valid_to IS NULL` = ngoài đời vẫn đang đúng), không phải
    *thiếu dữ liệu*. Xem §7.5 quy tắc 4.

    Datetime naive bị TỪ CHỐI thay vì đoán múi giờ — đoán là cách sinh ra
    lỗi lệch giờ chạy êm.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError(
            f"datetime naive không được chấp nhận: {value!r}. "
            "Nguồn phải nói rõ múi giờ; brain không đoán."
        )
    return value.astimezone(UTC)


def require_utc(value: datetime) -> datetime:
    """Như `ensure_utc` nhưng cấm `None`. Dùng cho cột NOT NULL (vd `valid_from`)."""
    result = ensure_utc(value)
    if result is None:  # pragma: no cover - chỉ xảy ra nếu gọi sai kiểu
        raise ValueError("giá trị bắt buộc nhưng nhận None")
    return result
