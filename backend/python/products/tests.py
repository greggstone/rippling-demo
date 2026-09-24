"""Tests for the product inventory API.

The repository is replaced with an in-memory fake so the suite runs without
MongoDB.
"""

import json
from typing import Any
from unittest.mock import patch

from django.test import Client, SimpleTestCase

from .repository import ProductNotFound
from .serializers import ValidationError, validate_product
from .views import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE


class FakeRepository:
    def __init__(self):
        self.items: list[dict[str, Any]] = []
        self._next_id = 1

    def list(self, *, page=1, page_size=20, category=None):
        items = [i for i in self.items if not category or i["category"] == category]
        start = (page - 1) * page_size
        return items[start : start + page_size], len(items)

    def get(self, product_id):
        for item in self.items:
            if item["id"] == product_id:
                return item
        raise ProductNotFound(product_id)

    def create(self, product):
        stored = {**product, "id": str(self._next_id)}
        self._next_id += 1
        self.items.insert(0, stored)
        return stored

    def update(self, product_id, product):
        stored = self.get(product_id)
        stored.update(product)
        return stored

    def delete(self, product_id):
        self.items.remove(self.get(product_id))

    def categories(self):
        return sorted({item["category"] for item in self.items})


VALID_PRODUCT = {
    "name": "Standing Desk",
    "description": "Height adjustable",
    "category": "Furniture",
    "brand": "Rippling",
    "price": 499.99,
    "quantity": 12,
}


class ProductSerializerTests(SimpleTestCase):
    def test_normalizes_optional_fields(self):
        product = validate_product(
            {"name": "  Laptop ", "category": "Devices", "price": "1200"}
        )
        self.assertEqual(product["name"], "Laptop")
        self.assertEqual(product["description"], "")
        self.assertEqual(product["brand"], "")
        self.assertEqual(product["price"], 1200.0)
        self.assertEqual(product["quantity"], 0)

    def test_requires_name_category_and_price(self):
        with self.assertRaises(ValidationError) as context:
            validate_product({"name": "   "})
        self.assertEqual(
            set(context.exception.errors), {"name", "category", "price"}
        )

    def test_rejects_negative_price_and_quantity(self):
        with self.assertRaises(ValidationError) as context:
            validate_product({**VALID_PRODUCT, "price": -1, "quantity": -5})
        self.assertEqual(
            set(context.exception.errors), {"price", "quantity"}
        )

    def test_rejects_prices_with_extra_decimals(self):
        with self.assertRaises(ValidationError) as context:
            validate_product({**VALID_PRODUCT, "price": "10.999"})
        self.assertIn("price", context.exception.errors)


class ProductApiTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()
        self.repository = FakeRepository()
        patcher = patch("products.views._repository", return_value=self.repository)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, payload):
        return self.client.post(
            "/products/", data=json.dumps(payload), content_type="application/json"
        )

    def test_create_then_list(self):
        response = self.post(VALID_PRODUCT)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["name"], "Standing Desk")

        listing = self.client.get("/products/").json()
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["page_size"], 20)

    def test_create_rejects_invalid_payload(self):
        response = self.post({"name": "", "price": "free"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.json()["errors"])

    def test_list_filters_by_category_and_paginates(self):
        self.post(VALID_PRODUCT)
        self.post({**VALID_PRODUCT, "name": "Monitor", "category": "Devices"})

        filtered = self.client.get("/products/?category=Devices").json()
        self.assertEqual([p["name"] for p in filtered["results"]], ["Monitor"])

        page_two = self.client.get("/products/?page=2&page_size=1").json()
        self.assertEqual(page_two["total"], 2)
        self.assertEqual(len(page_two["results"]), 1)

    def test_update_and_delete(self):
        product_id = self.post(VALID_PRODUCT).json()["id"]

        updated = self.client.put(
            f"/products/{product_id}/",
            data=json.dumps({**VALID_PRODUCT, "quantity": 3}),
            content_type="application/json",
        )
        self.assertEqual(updated.json()["quantity"], 3)

        self.assertEqual(self.client.delete(f"/products/{product_id}/").status_code, 204)
        self.assertEqual(self.client.get("/products/").json()["total"], 0)

    def test_unknown_product_returns_404(self):
        self.assertEqual(self.client.get("/products/missing/").status_code, 404)

    def test_categories_endpoint(self):
        self.post(VALID_PRODUCT)
        self.post({**VALID_PRODUCT, "category": "Devices"})
        self.assertEqual(
            self.client.get("/products/categories/").json()["results"],
            ["Devices", "Furniture"],
        )


class ProductRequestParsingTests(SimpleTestCase):
    """Covers how views translate raw HTTP input into repository calls."""

    def setUp(self):
        self.client = Client()
        self.repository = FakeRepository()
        patcher = patch("products.views._repository", return_value=self.repository)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_malformed_json_body_returns_400(self):
        response = self.client.post(
            "/products/", data="{not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("body", response.json()["errors"])

    def test_non_object_json_body_returns_400(self):
        response = self.client.post(
            "/products/", data="[]", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("body", response.json()["errors"])

    def test_unparsable_pagination_params_fall_back_to_defaults(self):
        listing = self.client.get("/products/?page=abc&page_size=0").json()
        self.assertEqual(listing["page"], 1)
        self.assertEqual(listing["page_size"], DEFAULT_PAGE_SIZE)

    def test_page_size_is_capped(self):
        listing = self.client.get("/products/?page_size=10000").json()
        self.assertEqual(listing["page_size"], MAX_PAGE_SIZE)

    def test_blank_category_is_not_treated_as_a_filter(self):
        self.client.post(
            "/products/",
            data=json.dumps(VALID_PRODUCT),
            content_type="application/json",
        )
        self.assertEqual(self.client.get("/products/?category=").json()["total"], 1)

    def test_unsupported_methods_are_rejected(self):
        self.assertEqual(self.client.delete("/products/").status_code, 405)
        self.assertEqual(self.client.post("/products/categories/").status_code, 405)

    def test_update_of_unknown_product_returns_404(self):
        response = self.client.put(
            "/products/missing/",
            data=json.dumps(VALID_PRODUCT),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_update_with_invalid_payload_returns_400(self):
        product_id = self.repository.create(VALID_PRODUCT)["id"]
        response = self.client.put(
            f"/products/{product_id}/",
            data=json.dumps({**VALID_PRODUCT, "name": ""}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.json()["errors"])
