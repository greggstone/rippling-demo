"""MongoDB-backed persistence for products."""

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument
from pymongo.collection import Collection

from django_app.mongo import get_database


class ProductNotFound(Exception):
    """Raised when a product id does not match any stored product."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def to_json(document: dict[str, Any]) -> dict[str, Any]:
    product = dict(document)
    product["id"] = str(product.pop("_id"))
    for field in ("created_at", "updated_at"):
        value = product.get(field)
        if isinstance(value, datetime):
            product[field] = value.isoformat()
    return product


class ProductRepository:
    """CRUD access to the products collection."""

    def __init__(self, collection: Collection | None = None):
        self._collection = (
            collection if collection is not None else get_database()["products"]
        )

    @staticmethod
    def _object_id(product_id: str) -> ObjectId:
        try:
            return ObjectId(product_id)
        except (InvalidId, TypeError):
            raise ProductNotFound(product_id)

    def list(
        self, *, page: int = 1, page_size: int = 20, category: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        query: dict[str, Any] = {}
        if category:
            query["category"] = category
        total = self._collection.count_documents(query)
        cursor = (
            self._collection.find(query)
            .sort("created_at", -1)
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        return [to_json(document) for document in cursor], total

    def get(self, product_id: str) -> dict[str, Any]:
        document = self._collection.find_one({"_id": self._object_id(product_id)})
        if document is None:
            raise ProductNotFound(product_id)
        return to_json(document)

    def create(self, product: dict[str, Any]) -> dict[str, Any]:
        document = dict(product)
        document["created_at"] = document["updated_at"] = _now()
        result = self._collection.insert_one(document)
        document["_id"] = result.inserted_id
        return to_json(document)

    def update(self, product_id: str, product: dict[str, Any]) -> dict[str, Any]:
        document = dict(product)
        document["updated_at"] = _now()
        updated = self._collection.find_one_and_update(
            {"_id": self._object_id(product_id)},
            {"$set": document},
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise ProductNotFound(product_id)
        return to_json(updated)

    def delete(self, product_id: str) -> None:
        result = self._collection.delete_one({"_id": self._object_id(product_id)})
        if result.deleted_count == 0:
            raise ProductNotFound(product_id)

    def categories(self) -> list[str]:
        return sorted(self._collection.distinct("category"))
