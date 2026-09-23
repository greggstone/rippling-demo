from django.contrib import admin
from django.urls import include, path
from django.http import HttpResponse

def hello_world(request):
    return HttpResponse("Hello, world! This is our interneers-lab Django server.")

urlpatterns = [
    path('admin/', admin.site.urls),
    path('hello/', hello_world),
    path('products/', include('products.urls')),
]
