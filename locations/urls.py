from django.urls import path
from .views import create_location, set_watch_area

urlpatterns = [
    path("locations/", create_location, name="create_location"),
    path("locations/watch_area/", set_watch_area, name="set_watch_area"),
]