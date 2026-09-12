from django.contrib import admin
from django.urls import path

from routes import views

urlpatterns = [
    path("", views.index, name="index"),
    path("api/route/", views.RouteFuelView.as_view(), name="route-fuel"),
    path("map/<str:token>/", views.map_view, name="route-map"),
    path("admin/", admin.site.urls),
]
