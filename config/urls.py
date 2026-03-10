"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from push.views import service_worker
from alerts import views as alert_views
from push import views as push_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path("api/", include("locations.urls")),
    path("api/", include("alerts.urls")),
    path("api/", include("groups.urls")),
    path("api/", include("push.urls")),
    path("answer/", push_views.answer_page, name="answer_page"),

    # SW / pages
    path("sw.js", service_worker, name="service_worker"),
    path("respond/", alert_views.respond_page, name="respond_page"),

    # ops
    path("ops/", include("alerts.ops_urls")),
]
