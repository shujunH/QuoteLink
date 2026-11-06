from django.urls import path
from . import views

urlpatterns = [
    path("workspace/", views.workspace),
    path("chat/", views.chat),
    path("inquiries/<uuid:pk>/", views.inquiry_detail),
    path("inquiries/<uuid:pk>/confirm/", views.confirm),
]
