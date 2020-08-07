from ..baseAPI import BasePlatformAPI
from ... import productDBLogger
from ...decimalHandling import dround
from ...models import Product
from ...stock.models import StockRecord
from ...stock.interfaces import ReceivedPlatformStockChange, SentPlatformStockChange, StockCount
from square.client import Client
import datetime
import rfc3339  # for date object -> date string
import iso8601  # for date string -> date object
from typing import Union, List
from uuid import uuid4
from flask import request
from decimal import Decimal


class FailedRequest(BaseException):
    pass


def get_date_object(date_string):
    return iso8601.parse_date(date_string)


# TODO: Figure out what the fuck
# Dates should just go die!
def get_date_string(date_object):
    return rfc3339.rfc3339(date_object, utc=True, use_system_timezone=False)


class SquareAPI(BasePlatformAPI):
    persistent_identifier = "square"
    webhook_enabled = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.APIClient = Client(
            access_token="EAAAEDZZRU_aZnhAtZf4AtvDFIBHhLXsqN3kGBSq5eBKIyhPT4foUN93H8NaDQ5T",
            environment="sandbox"
        )
        self.locationID = "MA9MJNAKD1WDG"
        self.signatureKey = ""
        self.changeQueue = list()
        self.lastInventoryRequest = datetime.datetime.utcnow()

    def webhook(self):
        print(request.data)
        return "Recieved"

    # Get the latest changes, and wipe the list.
    def getLatestChanges(self):
        self._fetchLatestChanges()
        oldChangeQueue = self.changeQueue.copy()
        self.changeQueue = list()
        return oldChangeQueue

    # Parsing the Square Stock Count JSON, converting it to a standard interface: StockCount
    def _convertSquareCountToStandard(self, squareCount: dict):
        # Get product's catalog ID
        productCatalogID = squareCount.get("catalog_object_id")

        # Check the state of the inventory reported by the count.
        countState = squareCount.get("state")

        # If state is WASTE, we ignore that stock, otherwise...
        if countState == "WASTE":
            return None
        else:
            # Fetch the productObject from the square product's catalog ID.
            productObject = self._getProductObjectFromCatalogID(productCatalogID)

            # Get the value reported by square for the stock count.
            platformValue = dround(Decimal(squareCount.get("quantity")), 6)

            # If productObject doesn't exist yet...
            if productObject is None:
                # Create a new productObject, (request the SKU, as get method will query DB, as such returns none.)
                productSKU = self._requestProductSKUFromCatalogID(productCatalogID)
                if productSKU is None:
                    return None
                else:
                    productObject = Product(sku=productSKU)

                # Create a new stock record, as product doesn't exist yet, we set the starting record value to the count reported by Square.
                newStockRecord = StockRecord(product=productObject, value=platformValue)

                # Save them.
                productObject.save()
                newStockRecord.save()

                # Register that product exists on Square... (product has to be saved)
                productObject.register_service(self.persistent_identifier)

            # If product is not registered as existing on Square, but the product clearly does as we just recieved a count...
            # register it!
            if not productObject.is_registered_on_service(self.persistent_identifier):
                productObject.register_service(self.persistent_identifier)
                productObject.save()

            # Return standard stock counts.
            return StockCount(
                product=productObject,
                value=platformValue,
                platformIdentity=self.persistent_identifier
            )

    # Get all the stock counts available on the platform.
    #   | Converts them to standard again.
    def getAllStockCounts(self) -> List[StockCount]:
        squareCountList = self._bulkStockCount()
        countList = list()
        for squareCount in squareCountList:
            convertedStockCount = self._convertSquareCountToStandard(squareCount)
            if convertedStockCount is not None:
                countList.append(convertedStockCount)
            else:
                pass
        return countList

    # Fetches Stock Count for a Specific Product.
    def getProductStockCount(self, productObject: Product) -> Union[StockCount, None]:
        productCatalogID = self._getCatalogIDFromProductSKU(productObject.sku)
        # TODO: Vet this change
        if productCatalogID is None:
            return None

        squareResponse = self.APIClient.inventory.retrieve_inventory_count(catalog_object_id=productCatalogID, location_ids=[self.locationID])
        if squareResponse.is_success():
            squareCountList = squareResponse.body.get("counts")
            if squareCountList is None:
                return None
            for squareCount in squareCountList:
                convertedStockCount = self._convertSquareCountToStandard(squareCount)
                if convertedStockCount is not None:
                    return convertedStockCount
                else:
                    pass
            return None
        else:
            # BUG: Loops infinitely if fails.
            productDBLogger.warn(f"Square was unable to get inventory count of product: {productObject.sku}")
            return None

    def _bulkStockCount(self):
        cursor = None
        finishedReading = False
        rawStockCountList = list()
        while not finishedReading:
            requestBody = {
                "cursor": cursor,
                "location_ids": [
                    self.locationID
                ]
            }
            responseJSON = self.APIClient.inventory.batch_retrieve_inventory_counts(requestBody)
            if responseJSON.body.get("errors") is None:
                cursor = responseJSON.body.get("cursor")
                if cursor is None:
                    finishedReading = True
                else:
                    finishedReading = False
                rawStockCountList = rawStockCountList + responseJSON.body.get("counts")
            else:
                raise FailedRequest
        return rawStockCountList

    def blacklistStockChange(self, squareChangeID):
        self.redisClient.sadd("squareBlacklist", squareChangeID)

    def isChangeInBlacklist(self, squareChangeID) -> bool:
        return self.redisClient.sismember("squareBlacklist", squareChangeID)

    def applyChange(self, change: SentPlatformStockChange):
        if change.action == "set":
            success = self._setStock(change)
        elif change.action == "change":
            success = self._changeStock(change)
        else:
            productDBLogger.warn(f"Unexpected change type! Change: {change}")
            return False
        return success

    def _changeStock(self, stockChange: SentPlatformStockChange):
        squareProductID = self._getCatalogIDFromProductSKU(stockChange.product.sku)
        if squareProductID is None:
            productDBLogger.warn("Square was sent a change for a non-existent Product ~ Un registering WC from product's services.")
            productDBLogger.critical("Exceptions need to be implemented. This will be a large refactor but is very important.")
            productDBLogger.critical("I mean heck! This function just returned True!!!! This means my system thinks the change occurred sucessfully!")
            stockChange.product.unregister_service(self.persistent_identifier)
            return True

        if stockChange.value < 0:
            fromState, toState = "IN_STOCK", "NONE"
        elif stockChange.value > 0:
            fromState, toState = "NONE", "IN_STOCK"
        else:
            return True
        changeID = str(uuid4())
        requestBody = {
            "idempotency_key": str(uuid4()),
            "changes": [
                {
                    "type": "ADJUSTMENT",
                    "adjustment": {
                        "reference_id": changeID,
                        "location_id": self.locationID,
                        "from_state": fromState,
                        "to_state": toState,
                        "catalog_object_id": squareProductID,
                        "occurred_at": get_date_string(stockChange.timeInitiated),
                        "quantity": str(stockChange.value)
                    }
                }
            ]
        }
        # TODO: Maybe somewhere to put a consistency check?
        responseJSON = self.APIClient.inventory.batch_change_inventory(requestBody)
        if responseJSON.body.get("errors") is None:
            self.blacklistStockChange(changeID)
            return True
        else:
            return False

    def _setStock(self, stockChange: SentPlatformStockChange):
        squareProductID = self._getCatalogIDFromProductSKU(stockChange.product.sku)
        print(squareProductID)
        print(stockChange.timeInitiated)
        if squareProductID is None:
            productDBLogger.warn("Square was sent a change for a non-existent Product ~ Un registering Square from product's services.")
            productDBLogger.critical("Exceptions need to be implemented. This will be a large refactor but is very important.")
            productDBLogger.critical("I mean heck! This function just returned True!!!! This means my system thinks the change occurred sucessfully!")
            stockChange.product.unregister_service(self.persistent_identifier)
            return True

        changeID = str(uuid4())
        requestBody = {
            "idempotency_key": str(uuid4()),
            "changes": [
                {
                    "type": "PHYSICAL_COUNT",
                    "physical_count": {
                        "reference_id": changeID,
                        "location_id": self.locationID,
                        "state": "IN_STOCK",
                        "catalog_object_id": squareProductID,
                        "occurred_at": get_date_string(stockChange.timeInitiated),
                        "quantity": str(stockChange.value)
                    }
                }
            ]
        }
        print(requestBody)
        responseJSON = self.APIClient.inventory.batch_change_inventory(requestBody)
        if responseJSON.is_success():
            self.blacklistStockChange(changeID)
            print(responseJSON.body)
            print(responseJSON.errors)
            print(self.getProductStockCount(Product.objects(sku="test").first()))
            return True
        else:
            print(responseJSON.body)
            print(responseJSON.errors)
            print(responseJSON.text)
            print(responseJSON.status_code)
            print(responseJSON.reason_phrase)
            print(responseJSON.body.get("errors"))
            return False

    def _convertSquareChangeToStandard(self, squareChangeJSON: dict) -> Union[None, ReceivedPlatformStockChange]:
        actionLookupTable = {
            "ADJUSTMENT": "change",
            "PHYSICAL_COUNT": "set",
        }
        changeAction = actionLookupTable.get(squareChangeJSON.get("type"))
        changePlatformIdentity = self.persistent_identifier

        if squareChangeJSON.get("type") == "ADJUSTMENT":
            adjustmentJSON = squareChangeJSON.get("adjustment")
            if (adjustmentJSON.get("reference_id") is not None) and (self.isChangeInBlacklist(adjustmentJSON.get("reference_id"))):
                return None
            changePlatformChangeID = adjustmentJSON.get("id")
            changeTimeOccurred = get_date_object(adjustmentJSON.get("occurred_at"))
            fromState = adjustmentJSON.get("from_state")
            toState = adjustmentJSON.get("to_state")
            if (fromState == "NONE") and (toState == "IN_STOCK"):
                quantityPositive = True
            elif (fromState == "IN_STOCK") and (toState in ["WASTE", "SOLD", "NONE"]):
                quantityPositive = False
            elif (fromState == "UNLINKED_RETURN") and (toState == "IN_STOCK"):
                quantityPositive = True
            elif (fromState == "UNLINKED_RETURN") and (toState == "WASTE"):
                return None
            else:
                productDBLogger.warn(f"There was an unexpected stock adjustment from Square! Unrecognized state transition {fromState} to {toState}. SquareAdjustmentID: {changePlatformChangeID}")
                return None

            if quantityPositive:
                changeValue = dround(Decimal(adjustmentJSON.get("quantity")), 6)
            else:
                changeValue = 0 - dround(Decimal(adjustmentJSON.get("quantity")), 6)
            changeProductSKU = self._getProductSKUFromCatalogID(adjustmentJSON.get("catalog_object_id"))
        elif squareChangeJSON.get("type") == "PHYSICAL_COUNT":
            physicalCountJSON = squareChangeJSON.get("physical_count")
            if (physicalCountJSON.get("reference_id") is not None) and (self.isChangeInBlacklist(physicalCountJSON.get("reference_id"))):
                return None
            changePlatformChangeID = physicalCountJSON.get("id")
            changeTimeOccurred = get_date_object(physicalCountJSON.get("occurred_at"))
            toState = physicalCountJSON.get("state")
            if toState == "IN_STOCK":
                changeValue = physicalCountJSON.get("quantity")
                changeProductSKU = self._getProductSKUFromCatalogID(physicalCountJSON.get("catalog_object_id"))
            else:
                productDBLogger.warn(f"Square API Unexpected State at physical_count. State: {toState} CountID: {changePlatformChangeID}")
                return None
        else:
            productDBLogger.warn(f"Unexpected Inventory Change Type at SquareAPI, Type: {squareChangeJSON.get('type')}")
            return None
        if changeProductSKU is None:
            return None
        else:
            newChange = ReceivedPlatformStockChange(
                action=changeAction,
                platformChangeID=changePlatformChangeID,
                platformIdentity=changePlatformIdentity,
                productSKU=changeProductSKU,
                value=changeValue,
                timeOccurred=changeTimeOccurred
            )
            return newChange

    def _requestProductSKUFromCatalogID(self, catalogID):
        responseJSON = self.APIClient.catalog.retrieve_catalog_object(catalogID)
        if responseJSON.is_success():
            if responseJSON.body.get("object").get("type") == "ITEM_VARIATION":
                sku = responseJSON.body.get("object").get("item_variation_data").get("sku")
                return sku
            else:
                productDBLogger.warn(f"Unrecognized Catalog Object Type! : {responseJSON.get('object').get('type')} CatalogID: {catalogID}")
                return None
        else:
            print(responseJSON.body)
            return None

    def _getProductSKUFromCatalogID(self, catalogID):
        return self._getProductObjectFromCatalogID(catalogID).sku

    def _getCatalogIDFromProductSKU(self, productSKU):
        productObj: Product = Product.objects(sku=productSKU).first()
        if productObj is not None:
            catalogID = productObj.metaData.get("squareCatalogID")
            if catalogID is not None:
                return catalogID
            else:
                pass
        else:
            pass
        requestBody = {
            "query": {
                "exact_query": {
                    "attribute_name": "sku",
                    "attribute_value": productSKU
                }
            }
        }
        responseJSON = self.APIClient.catalog.search_catalog_objects(requestBody)
        if responseJSON.body.get("errors") is not None:
            productJSON = responseJSON.body.get("objects")[0]
            return productJSON.get("id")
        else:
            print(responseJSON.body)
            productDBLogger.warn(f"Failed to get SquareID for product: {productSKU}")
            return None

    def _getProductObjectFromCatalogID(self, catalogID):
        productObject = Product.objects(metaData__squareCatalogID=catalogID).first()
        if productObject is None:
            productObject = Product.objects(sku=self._requestProductSKUFromCatalogID(catalogID)).first()
            if productObject is None:
                return None
            else:
                productObject.metaData["squareCatalogID"] = catalogID
                productObject.save()
                return productObject
        else:
            return productObject

    def _addChangeToQueue(self, changeInterface: ReceivedPlatformStockChange):
        self.changeQueue.append(changeInterface)

    def _fetchLatestChanges(self):
        finishedReading = False
        cursor = None
        updatedAfter = get_date_string(self.lastInventoryRequest - datetime.timedelta(seconds=60))
        self.lastInventoryRequest = datetime.datetime.utcnow()
        while not finishedReading:
            requestBody = {
                "location_ids": [self.locationID],
                "updated_after": updatedAfter,
                "cursor": cursor
            }
            requestResponse = self.APIClient.inventory.batch_retrieve_inventory_changes(requestBody)
            if not (requestResponse.errors is None):
                productDBLogger.critical(f"API SQUARE REQUEST HAD ERRORS!!!! Response body: {requestResponse.body}")
                finishedReading = True
            elif requestResponse.body.get("changes") is None:
                finishedReading = True
            else:
                for change in requestResponse.body.get("changes"):
                    changeInterface = self._convertSquareChangeToStandard(change)
                    if changeInterface is None:
                        pass
                    else:
                        if self.isChangeInBlacklist(changeInterface.platformChangeID):
                            pass
                        else:
                            self.blacklistStockChange(changeInterface.platformChangeID)
                            self._addChangeToQueue(changeInterface)
                if requestResponse.cursor is None:
                    finishedReading = True
                else:
                    finishedReading = False
                    cursor = requestResponse.cursor
