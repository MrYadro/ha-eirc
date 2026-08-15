from datetime import date

from custom_components.eirc_spb.notifications import NotificationDetector
from custom_components.eirc_spb.models import Account


def make_account(**kw) -> Account:
    defaults = dict(
        account_id="a1",
        number="71000000001",
        address="",
        current_bill_id=None,
        reading_deadline_day=None,
        reading_period_name=None,
    )
    defaults.update(kw)
    return Account(**defaults)


def test_new_bill_notification_once():
    det = NotificationDetector(deadline_days=3)
    first = det.feed(make_account(current_bill_id="26071000000001"))
    assert first == []
    second = det.feed(make_account(current_bill_id="26071000000002"))
    assert len(second) == 1
    n = second[0]
    assert n["type"] == "reading_deadline" or n["type"] == "new_bill"
    bills = [n for n in second if n["type"] == "new_bill"]
    assert len(bills) == 1
    assert bills[0]["bill_id"] == "26071000000002"
    third = det.feed(make_account(current_bill_id="26071000000002"))
    assert [n for n in third if n["type"] == "new_bill"] == []


def test_deadline_window_and_month_wrap():
    det = NotificationDetector(deadline_days=3, today=date(2026, 8, 30))
    det.feed(make_account(reading_deadline_day=11, reading_period_name="Август 2026"))
    assert det._notified_deadlines == set()
    det2 = NotificationDetector(deadline_days=3, today=date(2026, 9, 9))
    out = det2.feed(
        make_account(reading_deadline_day=11, reading_period_name="Сентябрь 2026")
    )
    deadlines = [n for n in out if n["type"] == "reading_deadline"]
    assert len(deadlines) == 1
    assert deadlines[0]["days_left"] == 2
    again = det2.feed(
        make_account(reading_deadline_day=11, reading_period_name="Сентябрь 2026")
    )
    assert [n for n in again if n["type"] == "reading_deadline"] == []


def test_deadline_notified_outside_window():
    det = NotificationDetector(deadline_days=3, today=date(2026, 8, 1))
    out = det.feed(
        make_account(reading_deadline_day=11, reading_period_name="Август 2026")
    )
    assert [n for n in out if n["type"] == "reading_deadline"] == []


def test_native_notifications_deduped_by_id():
    det = NotificationDetector(deadline_days=3)
    items = [
        {
            "id": "57295301",
            "type": "BELL",
            "title": "Новый счет доступен для оплаты",
            "message": "<p><span>Счет за июль 2026 г.</span></p>",
            "timestamp": "11.08.2026 15:35",
        }
    ]
    first = det.native(items)
    assert len(first) == 1
    assert first[0]["title"] == "Новый счет доступен для оплаты"
    assert first[0]["message"] == "Счет за июль 2026 г."
    assert first[0]["native_id"] == "57295301"
    second = det.native(items)
    assert second == []


def test_html_stripped_from_native_message():
    det = NotificationDetector(deadline_days=3)
    out = det.native(
        [
            {
                "id": "1",
                "type": "BELL",
                "title": "T",
                "message": "<p><span style=\"x\">A &amp; B</span><br/>C</p>",
                "timestamp": "t",
            }
        ]
    )
    assert out[0]["message"] == "A & B C"
