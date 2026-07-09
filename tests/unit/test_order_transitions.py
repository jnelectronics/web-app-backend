from app.db.enums import OrderStatus
from app.modules.orders.schemas import VALID_TRANSITIONS


def test_pending_can_be_confirmed_or_cancelled():
    assert VALID_TRANSITIONS[OrderStatus.PENDING] == {OrderStatus.CONFIRMED, OrderStatus.CANCELLED}


def test_delivered_is_terminal():
    assert VALID_TRANSITIONS[OrderStatus.DELIVERED] == set()


def test_cannot_skip_from_pending_to_delivered():
    assert OrderStatus.DELIVERED not in VALID_TRANSITIONS[OrderStatus.PENDING]
