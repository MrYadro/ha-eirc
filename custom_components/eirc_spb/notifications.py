import re
from datetime import date

from .models import Account

_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_RE = re.compile(r"&(?P<e>amp|lt|gt|quot|#39);")
_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "#39": "'"}


def strip_html(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw)
    text = _ENTITY_RE.sub(lambda m: _ENTITIES[m.group("e")], text)
    return " ".join(text.split())


class NotificationDetector:
    def __init__(self, deadline_days: int = 3, today: date | None = None) -> None:
        self._deadline_days = deadline_days
        self._today = today or date.today()
        self._last_bills: dict[str, str | None] = {}
        self._notified_deadlines: set[tuple[str, str]] = set()
        self._seen_native_ids: set[str] = set()

    def feed(self, account: Account) -> list[dict]:
        out: list[dict] = []
        last = self._last_bills.get(account.account_id, None)
        if (
            account.current_bill_id is not None
            and account.current_bill_id != last
            and last is not None
        ):
            out.append(
                {
                    "type": "new_bill",
                    "account_id": account.account_id,
                    "number": account.number,
                    "bill_id": account.current_bill_id,
                    "amount": account.current_bill_amount,
                    "timestamp": account.accruals_period,
                }
            )
        self._last_bills[account.account_id] = account.current_bill_id
        if (
            account.reading_deadline_day is not None
            and account.reading_period_name
        ):
            days_left = account.reading_deadline_day - self._today.day
            key = (account.account_id, account.reading_period_name)
            if 0 <= days_left <= self._deadline_days and key not in self._notified_deadlines:
                self._notified_deadlines.add(key)
                out.append(
                    {
                        "type": "reading_deadline",
                        "account_id": account.account_id,
                        "number": account.number,
                        "period": account.reading_period_name,
                        "days_left": days_left,
                        "deadline_day": account.reading_deadline_day,
                    }
                )
        return out

    def native(self, items: list[dict]) -> list[dict]:
        out: list[dict] = []
        for item in items:
            native_id = str(item.get("id", ""))
            if not native_id or native_id in self._seen_native_ids:
                continue
            self._seen_native_ids.add(native_id)
            out.append(
                {
                    "type": "native",
                    "native_type": item.get("type"),
                    "native_id": native_id,
                    "title": item.get("title") or "",
                    "message": strip_html(item.get("message") or ""),
                    "timestamp": item.get("timestamp"),
                }
            )
        return out
