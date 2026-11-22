"""The application: one data directory, its services, and the request API."""

from shop.api import errors, router
from shop.services import ShopError
from shop.services.catalog import Catalog
from shop.services.discounts import Discounts
from shop.services.ordering import Ordering
from shop.services.reports import Reports
from shop.storage import DiscountRepository, JsonStore, OrderRepository, ProductRepository


class App:
    def __init__(self, data_dir):
        store = JsonStore(data_dir)
        self.catalog = Catalog(ProductRepository(store))
        self.discounts = Discounts(DiscountRepository(store))
        self.ordering = Ordering(OrderRepository(store), self.catalog, self.discounts)
        self.reports = Reports(self.ordering, self.discounts)

    def handle(self, method, path, query=None, body=None):
        """Return (status, payload) for one request; docs/api.md lists the routes."""
        handler, params = router.resolve(method, path)
        if handler is None:
            return errors.no_route(method, path)
        try:
            return handler(self, params, query, body)
        except ShopError as error:
            return errors.error_response(error)
