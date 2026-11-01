from django.urls import path

from apps.imports.views import UploadView

urlpatterns = [
    path("", UploadView.as_view(), name="upload"),
]
