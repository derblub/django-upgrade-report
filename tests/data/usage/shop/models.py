from django.db import models
from django.utils import timezone


class Product(models.Model):
    name = models.CharField(max_length=100)
    on_sale = models.NullBooleanField()
    created = models.DateTimeField(default=lambda: timezone.now().astimezone(timezone.utc))

    class Meta:
        index_together = [("name", "on_sale")]

    def order_by(self):  # a method of the same name as one Django changed: not a removal
        return self.name
