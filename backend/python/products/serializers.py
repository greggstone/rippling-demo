"""Validation and serialization for product payloads."""

from decimal import Decimal, InvalidOperation
from typing import Any

MAX_NAME_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 1000
MAX_BRAND_LENGTH = 80
MAX_CATEGORY_LENGTH = 80
MAX_PRICE = Decimal("1000000000")
MAX_QUANTITY = 2**31 - 1


class ValidationError(Exception):
    """Raised when a product payload is not acceptable."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("invalid product payload")
        self.errors = errors


def _clean_string(
    payload: dict[str, Any],
    field: str,
    errors: dict[str, str],
    *,
    required: bool,
    max_length: int,
    default: str = "",
) -> str:
    value = payload.get(field, default if not required else None)
    if value is None:
        errors[field] = "this field is required"
        return ""
    if not isinstance(value, str):
        errors[field] = "must be a string"
        return ""
    value = value.strip()
    if required and not value:
        errors[field] = "must not be empty"
    elif len(value) > max_length:
        errors[field] = f"must be at most {max_length} characters"
    return value


def _clean_price(payload: dict[str, Any], errors: dict[str, str]) -> float:
    value = payload.get("price")
    if value is None:
        errors["price"] = "this field is required"
        return 0.0
    if isinstance(value, bool):
        errors["price"] = "must be a number"
        return 0.0
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError):
        errors["price"] = "must be a number"
        return 0.0
    if not price.is_finite():
        errors["price"] = "must be a number"
        return 0.0
    if price < 0:
        errors["price"] = "must not be negative"
    elif price > MAX_PRICE:
        errors["price"] = f"must be at most {MAX_PRICE}"
    elif price.as_tuple().exponent < -2:
        errors["price"] = "must have at most 2 decimal places"
    return float(price)


def _clean_quantity(payload: dict[str, Any], errors: dict[str, str]) -> int:
    value = payload.get("quantity", 0)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        errors["quantity"] = "must be an integer"
        return 0
    try:
        quantity = int(value)
    except ValueError:
        errors["quantity"] = "must be an integer"
        return 0
    if quantity < 0:
        errors["quantity"] = "must not be negative"
    elif quantity > MAX_QUANTITY:
        errors["quantity"] = f"must be at most {MAX_QUANTITY}"
    return quantity


def validate_product(payload: Any) -> dict[str, Any]:
    """Return a normalized product document or raise ValidationError."""
    if not isinstance(payload, dict):
        raise ValidationError({"body": "expected a JSON object"})

    errors: dict[str, str] = {}
    product = {
        "name": _clean_string(
            payload, "name", errors, required=True, max_length=MAX_NAME_LENGTH
        ),
        "description": _clean_string(
            payload,
            "description",
            errors,
            required=False,
            max_length=MAX_DESCRIPTION_LENGTH,
        ),
        "category": _clean_string(
            payload, "category", errors, required=True, max_length=MAX_CATEGORY_LENGTH
        ),
        "brand": _clean_string(
            payload, "brand", errors, required=False, max_length=MAX_BRAND_LENGTH
        ),
        "price": _clean_price(payload, errors),
        "quantity": _clean_quantity(payload, errors),
    }
    if errors:
        raise ValidationError(errors)
    return product
