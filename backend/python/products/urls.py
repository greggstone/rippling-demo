from django.urls import path

from . import views

urlpatterns = [
    path("", views.products),
    path("categories/", views.categories),
    path("<str:product_id>/", views.product_detail),
]
