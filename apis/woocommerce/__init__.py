from .interfaces import WooCommerceProduct
from ..baseAPI import BasePlatformAPI
from ... import productDBLogger
from ...models import Product
from ...stock.interfaces import StockCount, ReceivedPlatformStockChange, SentPlatformStockChange
from woocommerce import API
import datetime
import dateutil.parser
from uuid import uuid4
from flask import request
import json


def pp_json(json_thing, sort=True, indents=4):
    if type(json_thing) is str:
        print(json.dumps(json.loads(json_thing), sort_keys=sort, indent=indents))
    else:
        print(json.dumps(json_thing, sort_keys=sort, indent=indents))
    return None


class WooCommerceAPI(BasePlatformAPI):
    persistent_identifier = "woo-commerce"
    webhook_enabled = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.lastOrderCheck = datetime.datetime.now()
        self.latestChanges = list()
        self.oneTimeBlacklist = list()
        self.APIClient = API(
            url="https://dottodotstudio.co.uk",
            consumer_key="ck_d9b41a1b849878a05becd4819de3679143561e4f",
            consumer_secret="cs_2d07f8d355855145d0afb30fae628aad6ba5f0a9",
            version="wc/v3",
            query_string_auth=True
        )

    # Stock Count & Consistency Check Handlers
    ###########################################
    def getAllStockCounts(self):
        pass

    # Latest Change and Reporting Handlers
    #######################################
    def _logChange(self, changeObj: ReceivedPlatformStockChange):
        self.latestChanges.append(changeObj)

    def getLatestChanges(self):
        latestChanges = self.latestChanges
        self.latestChanges = list()
        return latestChanges

    def _addToOneTimeBlacklist(self, productSKU: str):
        self.oneTimeBlacklist.append(productSKU)

    def _productSKUInOneTimeBlacklist(self, productSKU: str):
        exists = (productSKU in self.oneTimeBlacklist)
        if exists:
            self.oneTimeBlacklist.remove(productSKU)
        return exists

    # Webhook product modified handlers
    #####################################
    def webhook(self):
        # TODO: Clean up this if tree.
        if not request.is_json:
            print(request.data)
            return "JSON Expected!", 200
        else:
            changeObj = self._convertProductDataIntoReceivedChange(request.json)
            if changeObj is None:
                pass
            else:
                if self._willOwnChangeHaveAnyEffectOnStockRecord(changeObj):
                    if not self._productSKUInOneTimeBlacklist(changeObj.productSKU):
                        self._logChange(changeObj)
                    else:
                        pass
                else:
                    pass
            return "Received!", 200

    # Sent Change Handlers
    #######################
    def applyChange(self, change):
        if change.action == "set":
            return self._setStock(change)
        elif change.action == "change":
            return self._changeStock(change)
        else:
            productDBLogger.warn(f"WC API Encountered Unexpected action type: {change.action}")
            return False

    def _setStock(self, change: SentPlatformStockChange):
        # TODO: Implement ID caching, similar to Etsy
        # Must do this for changeStock methods too?
        productObject = change.product
        WCProductObject = WooCommerceProduct.fetchBySKU(self.APIClient, productObject.sku)
        if WCProductObject is None:
            productDBLogger.warn("WooCommerce was sent a change for a non-existent Product")
            return False
        else:
            if WCProductObject.type == "simple":
                return self._setStockSimpleProduct(WCProductObject, change.value)
            elif WCProductObject.type == "variation":
                return self._setStockVariationProduct(WCProductObject, change.value)
            else:
                productDBLogger.warn(f"WCAPI was asked to set the stock of a product of unexpected type: {WCProductObject.type}")
                return False

    def _changeStock(self, change: SentPlatformStockChange):
        productObject = change.product
        WCProductObject = WooCommerceProduct.fetchBySKU(self.APIClient, productObject.sku)
        if WCProductObject is None:
            productDBLogger.warn("WooCommerce was sent a change for a non-existent Product")
            return False
        else:
            # Pre-calculate new stock values...
            if WCProductObject.manage_stock:
                newStock = WCProductObject.stock_quantity + change.value
            else:
                newStock = productObject.stockRecord.value + change.value

            # Hand-off to set handlers.
            if WCProductObject.type == "simple":
                return self._setStockSimpleProduct(WCProductObject, newStock)
            elif WCProductObject.type == "variation":
                return self._setStockVariationProduct(WCProductObject, newStock)
            else:
                productDBLogger.warn(f"WooCommerceAPI was asked to set the stock of a product of unexpected type: {WCProductObject.type}")
                return False

    def _setStockSimpleProduct(self, WCProductObject: WooCommerceProduct, newStock: float):
        requestData = {
            "manage_stock": True,
            "stock_quantity": str(newStock)
        }
        responseObject = self.APIClient.put(f"products/{WCProductObject.ID}", requestData)
        if responseObject.status_code == 200:
            return True
        else:
            productDBLogger.error(f"Failed to set stock of a simple product: ErrorMSG: {responseObject.content}")
            return False

    def _setStockVariationProduct(self, WCProductObject: WooCommerceProduct, newStock: float):
        requestData = {
            "manage_stock": True,
            "stock_quantity": str(newStock)
        }
        responseObject = self.APIClient.put(f"products/{WCProductObject.parentID}/variations/{WCProductObject.ID}", requestData)
        if responseObject.status_code == 200:
            return True
        else:
            productDBLogger.error(f"Failed to set stock of a variation product: ErrorMSG: {responseObject.content}")
            return False

    # Product Data Converters and Request Methods
    ###########################################
    def _convertProductDataIntoReceivedChange(self, productData: dict):
        productSKU = productData.get("sku")
        productType = productData.get("type")
        stockManaged = productData.get("manage_stock")
        if (not stockManaged) or (productSKU is None) or (productType == "variable"):
            return None
        # If passes checks...
        return ReceivedPlatformStockChange(
            productSKU=productSKU,
            action="set",
            value=float(productData.get("stock_quantity")),
            timeOccurred=dateutil.parser.parse(productData.get("date_modified_gmt")),
            platformChangeID=f"dummy-platform-change-id-{uuid4()}",
            platformIdentity=self.persistent_identifier
        )

    # TODO: Tidy this method up...
    # maybe incorporate its functionality into the base stock manager to prevent data conflict
    @staticmethod
    def _willOwnChangeHaveAnyEffectOnStockRecord(changeObj: ReceivedPlatformStockChange):
        if changeObj.action == "change":
            if changeObj.value == 0:
                return False
            else:
                return True
        elif changeObj.action == "set":
            productObj = Product.objects(sku=changeObj.productSKU)
            if productObj is None:
                return True
            else:
                productObjStockRecord = productObj.stockRecord
                if productObjStockRecord is None:
                    return True
                else:
                    if changeObj.value == productObjStockRecord.value:
                        return False
                    else:
                        return True
        else:
            productDBLogger.warn(f"WC Api encountered an unexpected change action: {changeObj.action}, applied to product: {changeObj.productSKU}")
            return False