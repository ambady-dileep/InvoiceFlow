from django.urls import path

from apps.imports.views import UploadErrorReportView, UploadStatusView, UploadView

urlpatterns = [
    path("", UploadView.as_view(), name="upload"),
    path("<int:pk>/status/", UploadStatusView.as_view(), name="upload-status"),
    path("<int:pk>/errors/", UploadErrorReportView.as_view(), name="upload-errors"),
]
