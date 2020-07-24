from ..baseAPI import BasePlatformAPI
from ... import productDBLogger
from ...models import Product
from ...stock.interfaces import ReceivedPlatformStockChange, SentPlatformStockChange, StockCount
from square.client import Client
import datetime
import rfc3339  # for date object -> date string
import iso8601  # for date string -> date object
from typing import Union
from uuid import uuid4


class FailedRequest(BaseException):
    pass


def get_date_object(date_string):
    return iso8601.parse_date(date_string)


def get_date_string(date_object):
    return rfc3339.rfc3339(date_object)


class SquareAPI(BasePlatformAPI):
    persistent_identifier = "square"
    webhook_enabled = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.APIClient = Client(
            access_token="EAAAEDZZRU_aZnhAtZf4AtvDFIBHhLXsqN3kGBSq5eBKIyhPT4foUN93H8NaDQ5T",
            environment="sandbox"
        )
        self.locationID = "MA9MJNAKD1WDG"
        self.signatureKey = ""
        self.changeQueue = list()
        self.lastInventoryRequest = datetime.datetime.now()

    def webhook(self):
        return "Recieved"

    def getLatestChanges(self):
        self._fetchLatestChanges()
        oldChangeQueue = self.changeQueue.copy()
        self.changeQueue = list()
        return oldChangeQueue

    def _convertSquareCountToStandard(self, squareCount: dict):
        productCatalogID = squareCount.get("catalog_object_id")
        countState = squareCount.get("state")
        if countState == "WASTE":
            return None
        else:
            productObject = self._getProductObjectFromCatalogID(productCatalogID)
            if productObject is None:
                productObject = Product(sku=self._requestProductSKUFromCatalogID(productCatalogID))
                productObject.save()
                newChange = ReceivedPlatformStockChange(
                    productSKU=productObject.sku,
                    action="set",
                    value=float(squareCount.get("quantity")),
                    timeOccurred=datetime.datetime.now(),
                    platformChangeID=f"square-consistency-count-{uuid4()}",
                    platformIdentity=self.persistent_identifier
                )
                self._addChangeToQueue(newChange)
            return StockCount(
                product=productObject,
                value=float(squareCount.get("quantity")),
                platformIdentity=self.persistent_identifier
            )

    def getAllStockCounts(self):
        squareCountList = self._bulkStockCount()
        countList = list()
        for squareCount in squareCountList:
            convertedStockCount = self._convertSquareCountToStandard(squareCount)
            if convertedStockCount is not None:
                countList.append(convertedStockCount)
            else:
                pass
        return countList

    def getProductStockCount(self, productObject: Product) -> Union[StockCount, None]:
        productCatalogID = self._getCatalogIDFromProductSKU(productObject.sku)
        squareResponse = self.APIClient.inventory.retrieve_inventory_count(catalog_object_id=productCatalogID, location_ids=[self.locationID])
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
            productDBLogger.warn(f"Failed to complete square change as product not registered on square! Change: {stockChange}")
            return False
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
        if squareProductID is None:
            productDBLogger.warn(f"Failed to complete square change as product not registered on square! Change: {stockChange}")
            return False
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
        responseJSON = self.APIClient.inventory.batch_change_inventory(requestBody)
        if responseJSON.body.get("errors") is not None:
            self.blacklistStockChange(changeID)
            return True
        else:
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
                changeValue = adjustmentJSON.get("quantity")
            else:
                changeValue = 0 - float(adjustmentJSON.get("quantity"))
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
        if responseJSON.body.get("object").get("type") == "ITEM_VARIATION":
            sku = responseJSON.body.get("object").get("item_variation_data").get("sku")
            return sku
        else:
            productDBLogger.warn(f"Unrecognized Catalog Object Type! : {responseJSON.get('object').get('type')} CatalogID: {catalogID}")
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
