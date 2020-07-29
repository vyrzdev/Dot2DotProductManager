from woocommerce import API
from ...models import Product
from decimal import Decimal
from ...decimalHandling import dround


class WooCommerceProduct:
    def __init__(self, raw: dict):
        self.type = raw.get("type")
        self.ID = raw.get("id")
        self.sku = raw.get("sku")
        self.manage_stock = raw.get("manage_stock")
        if self.manage_stock:
            self.stock_quantity = dround(Decimal(raw.get("stock_quantity")), 6)
        else:
            self.stock_quantity = None
        if self.type == "simple":
            self.parentID = None
        elif self.type == "variation":
            self.parentID = raw.get("parent_id")
        else:
            pass

    # Constructors
    @classmethod
    def fetchBySKU(cls, APIClient: API, productSKU: str):
        responseObject = APIClient.get("products", params={
            "sku": productSKU
        })
        if responseObject.status_code == 200:
            responseJSON = responseObject.json()
            if len(responseJSON) < 1:
                return None
            else:
                productJSON = responseJSON[0]
                return cls(productJSON)
        else:
            return None