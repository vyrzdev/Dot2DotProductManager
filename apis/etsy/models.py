import mongoengine
import math
from etsy2 import Etsy
from decimal import Decimal
from .exceptions import FailedToUpdateInventory


class EtsyParityRecord(mongoengine.Document):
    value = mongoengine.DecimalField(required=True, default=Decimal(0))
    denomination = mongoengine.DecimalField(required=True, default=0.5)

    listingType = mongoengine.StringField(required=True)
    listingID = mongoengine.StringField(required=True)
    productID = mongoengine.StringField(required=False)

    @property
    def integerQuantity(self):
        return math.floor(self.value / self.denomination)

    def setValue(self, newValue: Decimal):
        oldQuantity = self.integerQuantity
        self.value = newValue
        newQuantity = self.integerQuantity
        self.save()
        if newQuantity != oldQuantity:
            self.pushQuantityToEtsy(newQuantity)
        else:
            pass

    def pushQuantityToEtsy(self, quantity: int, etsyClient: Etsy):
        listingJSON = self.getRawListingProductsJSON(etsyClient)
        productsJSON = listingJSON.get("products")
        if productsJSON is None:
            pass
        else:
            counter = 0
            for productJSON in productsJSON:
                if productJSON.get("product_id") == int(self.productID):
                    listingJSON["products"][counter]["offerings"][0]["quantity"] = quantity
                else:
                    counter = counter + 1
            try:
                responseJSON = etsyClient.updateInventory(listing_id=int(self.listingID), **listingJSON)
            except ValueError as err:
                print(err)
                raise FailedToUpdateInventory

    def getQuantityFromEtsy(self, quantity: int, etsyClient: Etsy):
        pass

    def getRawListingProductsJSON(self, etsyClient):
        return etsyClient.getInventory(listing_id=int(self.listingID), write_missing_inventory=True)
