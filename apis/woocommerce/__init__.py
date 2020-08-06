from .interfaces import WooCommerceProduct
from ..baseAPI import BasePlatformAPI
from ... import productDBLogger
from ...models import Product
from ...decimalHandling import dround
from ...stock.interfaces import StockCount, ReceivedPlatformStockChange, SentPlatformStockChange
from ...stock.models import StockRecord
from woocommerce import API
import datetime
import dateutil.parser
from uuid import uuid4
from flask import request
import json
from decimal import Decimal
from requests.exceptions import ReadTimeout


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
        wpProductDataList = self._bulkStockCount()

        stdCountList = list()
        count = 1
        total = len(wpProductDataList)
        productDBLogger.info("WcAPI Converting/Parsing Simple Product Stock Counts...")
        for wpRawProductData in wpProductDataList:
            stdCount = self._convertProductDataIntoStockCount(wpRawProductData)
            if stdCount is not None:
                stdCountList.append(stdCount)
            else:
                pass
            count = count + 1
        return stdCountList

    def getProductStockCount(self, productObject: Product):
        productSKU = productObject.sku
        responseObject = self.APIClient.get("products", params={
            "sku": productSKU
        })
        if responseObject.status_code == 200:
            responseJSON = responseObject.json()
            if len(responseJSON) > 0:
                productJSON = responseJSON[0]
                stockCount = self._convertProductDataIntoStockCount(productJSON)
                return stockCount
            else:
                print(f"Length of JSON response was below 0... product had sku:{productObject.sku}")
                return None
        else:
            productDBLogger.warn(f"WcAPI encountered an error when fetching stock record explicitly for product with SKU: {productSKU}")
            return None

    def _bulkStockCount(self):
        bulkStockCountList = list()
        bulkStockCountList = bulkStockCountList + self._bulkSimpleStockCount() + self._bulkVariationStockCount()
        return bulkStockCountList

    def _bulkSimpleStockCount(self):
        productDBLogger.info("WcAPI Getting Simple Product Stock Counts...")
        page = 1
        finishedReading = False
        rawStockCountList = list()
        failedAttempts = 0
        while (not finishedReading) and (failedAttempts < 4):
            try:
                requestResponse = self.APIClient.get("products", params={
                    "page": page,
                    "per_page": 50,
                    "type": "simple"
                })
                if requestResponse.status_code == 200:
                    failedAttempts = 0
                    responseJSON = requestResponse.json()
                    rawStockCountList = rawStockCountList + responseJSON
                    totalPages = int(requestResponse.headers["X-WP-TotalPages"])
                    if page >= totalPages:
                        finishedReading = True
                    else:
                        finishedReading = False
                        page = page + 1
                else:
                    print(requestResponse.content)
                    failedAttempts = failedAttempts + 1
                    finishedReading = False
            except ReadTimeout:
                productDBLogger.warn("WcAPI Bulk Stock Count got a read timeout, retrying!")
                failedAttempts = failedAttempts + 1
                finishedReading = False
        if failedAttempts == 4:
            productDBLogger.error("WpApi BulkStockCount Encountered 4 errors in a row in simple product fetch and was forced to quit early!!!")
        return rawStockCountList

    def _bulkVariationStockCount(self):
        productDBLogger.info("WcAPI Fetching Variable Product Listings")
        page = 1
        finishedReading = False
        variableProductIDList = list()
        failedAttempts = 0
        while (not finishedReading) and (failedAttempts < 4):
            requestResponse = self.APIClient.get("products", params={
                "page": page,
                "per_page": 50,
                "type": "variable"
            })
            if requestResponse.status_code == 200:
                failedAttempts = 0
                responseJSON = requestResponse.json()
                for variableProductJSON in responseJSON:
                    variableProductID = variableProductJSON.get("id")
                    variableProductIDList.append(variableProductID)
                totalPages = int(requestResponse.headers["X-WP-TotalPages"])
                if page >= totalPages:
                    finishedReading = True
                else:
                    finishedReading = False
                    page = page + 1
            else:
                print(requestResponse.content)
                failedAttempts = failedAttempts + 1
                finishedReading = False
        if failedAttempts == 4:
            productDBLogger.error("WpApi BulkStockCount Encountered 4 errors in a row in simple product fetch and was forced to quit early!!!")

        productDBLogger.info("WcAPI Fetching Variation Stock Counts ")
        rawStockCountList = list()
        count = 1
        total = len(variableProductIDList)
        for variableProductID in variableProductIDList:
            page = 1
            finishedReading = False
            failedAttempts = 0
            while (not finishedReading) and (failedAttempts < 4):
                requestResponse = self.APIClient.get(f"products/{variableProductID}/variations", params={
                    "page": page,
                    "per_page": 50
                })
                if requestResponse.status_code == 200:
                    failedAttempts = 0
                    responseJSON = requestResponse.json()
                    rawStockCountList = rawStockCountList + responseJSON
                    totalPages = int(requestResponse.headers["X-WP-TotalPages"])
                    if page >= totalPages:
                        page = page + 1
                        finishedReading = True
                    else:
                        page = page + 1
                        finishedReading = False
                else:
                    print(requestResponse.content)
                    failedAttempts = failedAttempts + 1
                    finishedReading = False
            if failedAttempts == 4:
                productDBLogger.error(f"WC API forced to abort fetching variation data for variable product with WooCommerce ID: {variableProductID}")
            count = count + 1
        return rawStockCountList

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
            productDBLogger.warn("WooCommerce was sent a change for a non-existent Product ~ Un registering WC from product's services.")
            productDBLogger.critical("Exceptions need to be implemented. This will be a large refactor but is very important.")
            productObject.unregister_service(self.persistent_identifier)
            return True
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
            productDBLogger.warn("WooCommerce was sent a change for a non-existent Product ~ Un registering WC from product's services.")
            productDBLogger.critical("Exceptions need to be implemented. This will be a large refactor but is very important.")
            productDBLogger.critical("I mean heck! This function just returned True!!!! This means my system thinks the change occurred sucessfully!")
            productObject.unregister_service(self.persistent_identifier)
            return True
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

    def _setStockSimpleProduct(self, WCProductObject: WooCommerceProduct, newStock: Decimal):
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

    def _setStockVariationProduct(self, WCProductObject: WooCommerceProduct, newStock: Decimal):
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
            value=dround(Decimal(productData.get("stock_quantity")), 6),
            timeOccurred=dateutil.parser.parse(productData.get("date_modified_gmt")),
            platformChangeID=f"dummy-platform-change-id-{uuid4()}",
            platformIdentity=self.persistent_identifier
        )

    def _convertProductDataIntoStockCount(self, productData: dict):
        # Unpack Product SKU, ensure it isn't none, e.g. that an sku has been inputted.
        productSKU = productData.get("sku")
        if (productSKU is None) or (productSKU == ""):
            return None
        else:
            # Ensure that WC is managing stock for this product
            manageStock = productData.get("manage_stock")
            if manageStock:
                # Get WC stored stock value
                stockQuantity = productData.get("stock_quantity")
                # Ensure stock value actually exists.
                # TODO: Maybe some other behaviour should occur here.
                # e.g. Stock updates with the latest stock value for the product.
                if stockQuantity is None:
                    return None

                stockQuantity = dround(Decimal(stockQuantity), 6)

                # Get productObject, for WC product.
                # This product may not exist in DB yet however, and as such must be created.
                productObject = Product.objects(sku=productSKU).first()
                if productObject is None:
                    # When productObject doesn't exist, build a new product.
                    productObject = Product(sku=productSKU)

                    # Create a new stockRecord, defaulting the value to that reported by this platform...
                    # as product doesn't yet exist for other platforms.
                    newStockRecord = StockRecord(product=productObject, value=stockQuantity)

                    # Save these objects.
                    productObject.save()
                    newStockRecord.save()

                    # Register that product exists on this service... (product has to be saved)
                    productObject.register_service(self.persistent_identifier)

                # If the productObject doesn't have a record of existing on this service, register it.
                if not productObject.is_registered_on_service(self.persistent_identifier):
                    productObject.register_service(self.persistent_identifier)
                    productObject.save()

                # Create and return the stock count.
                stdStockCount = StockCount(
                    product=productObject,
                    value=stockQuantity,
                    platformIdentity=self.persistent_identifier
                )
                return stdStockCount
            else:
                return None

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
            productObj = Product.objects(sku=changeObj.productSKU).first()
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
