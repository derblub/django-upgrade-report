from rest_framework import viewsets

from .models import Product


class Products(viewsets.ModelViewSet):
    queryset = Product.objects.all()


def search(request):
    if request.is_ajax():
        return None
