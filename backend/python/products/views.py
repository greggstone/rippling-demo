"""HTTP endpoints for the product inventory."""

import json
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .repository import ProductNotFound, ProductRepository
from .serializers import ValidationError, validate_product

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _repository() -> ProductRepository:
    return ProductRepository()


def _parse_body(request: HttpRequest) -> Any:
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        raise ValidationError({"body": "expected valid JSON"})


def _positive_int(raw: str | None, default: int, maximum: int | None = None) -> int:
    try:
        value = int(raw) if raw is not None else default
    except ValueError:
        return default
    if value < 1:
        return default
    if maximum is not None:
        return min(value, maximum)
    return value


@csrf_exempt
@require_http_methods(["GET", "POST"])
def products(request: HttpRequest) -> HttpResponse:
    repository = _repository()
    if request.method == "POST":
        try:
            product = validate_product(_parse_body(request))
        except ValidationError as error:
            return JsonResponse({"errors": error.errors}, status=400)
        return JsonResponse(repository.create(product), status=201)

    page = _positive_int(request.GET.get("page"), 1)
    page_size = _positive_int(
        request.GET.get("page_size"), DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
    )
    category = request.GET.get("category") or None
    items, total = repository.list(page=page, page_size=page_size, category=category)
    return JsonResponse(
        {"results": items, "total": total, "page": page, "page_size": page_size}
    )


@csrf_exempt
@require_http_methods(["GET", "PUT", "DELETE"])
def product_detail(request: HttpRequest, product_id: str) -> HttpResponse:
    repository = _repository()
    try:
        if request.method == "GET":
            return JsonResponse(repository.get(product_id))
        if request.method == "PUT":
            try:
                product = validate_product(_parse_body(request))
            except ValidationError as error:
                return JsonResponse({"errors": error.errors}, status=400)
            return JsonResponse(repository.update(product_id, product))
        repository.delete(product_id)
        return HttpResponse(status=204)
    except ProductNotFound:
        return JsonResponse({"errors": {"id": "product not found"}}, status=404)


@require_http_methods(["GET"])
def categories(request: HttpRequest) -> HttpResponse:
    return JsonResponse({"results": _repository().categories()})
