from django.urls import path

from . import views

urlpatterns = [
    path("role-changes/", views.create_role_change),
    path("runs/<int:run_id>/", views.run_detail),
    path("runs/<int:run_id>/resume/", views.resume_run),
]
