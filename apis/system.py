from . import baseAPI
from lazy.logger import rootLogger, SubLogger
systemProductDBLogger = SubLogger(
    "systemProductPlatform",
    parent=rootLogger
)
from ..decimalHandling import dround
from ..models import Product
from ..stock.models import StockRecord
from ..stock.interfaces import ReceivedPlatformStockChange, SentPlatformStockChange, StockCount
import datetime
from typing import List
from uuid import uuid4
from decimal import Decimal


class SystemAPI(baseAPI.BasePlatformAPI):
    persistent_identifier = "system"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.changeQueue = list()

    @staticmethod
    def getProductReg(productSKU):
        return Product.objects(sku=productSKU).first()

    def getProductStockCount(self, productObject: Product):
        stockRecord = productObject.stockRecord
        if stockRecord is None:
            return None
        else:
            return StockCount(
                product=productObject,
                value=dround(Decimal(stockRecord.value), 6),
                platformIdentity=self.persistent_identifier
            )

    def getAllStockCounts(self):
        stockCounts: List[StockCount] = list()
        stockRecord: StockRecord
        for stockRecord in StockRecord.objects().all():
            stockRecord: StockRecord
            if not stockRecord.product.is_registered_on_service(self.persistent_identifier):
                stockRecord.product.register_service(self.persistent_identifier)
                stockRecord.save()

            stockCounts.append(
                StockCount(
                    product=stockRecord.product,
                    value=dround(Decimal(stockRecord.value), 6),
                    platformIdentity=self.persistent_identifier
                )
            )
        return stockCounts

    def setStock(self, productSKU: str, value: Decimal):
        succeeded = self._setStock(self.getProductReg(productSKU), value)
        self.changeQueue.append(ReceivedPlatformStockChange(
            productSKU=productSKU,
            action="set",
            value=value,
            timeOccurred=datetime.datetime.now(),
            platformChangeID=str(uuid4()),
            platformIdentity=self.persistent_identifier
        ))
        return succeeded

    def changeStock(self, productSKU, value: Decimal):
        succeeded = self._changeStock(self.getProductReg(productSKU), value)
        self.changeQueue.append(ReceivedPlatformStockChange(
            productSKU=productSKU,
            action="change",
            value=value,
            timeOccurred=datetime.datetime.now(),
            platformChangeID=str(uuid4()),
            platformIdentity=self.persistent_identifier
        ))
        return succeeded

    @staticmethod
    def _setStock(productReg: Product, value: Decimal):
        stockRecord = productReg.stockRecord
        newValue = value
        stockRecord.update(value=newValue)
        stockRecord = StockRecord.objects(product=productReg).first()
        if stockRecord.value == newValue:
            return True
        else:
            return False

    @staticmethod
    def _changeStock(productReg: Product, value: Decimal):
        stockRecord = productReg.stockRecord
        newValue = stockRecord.value + value
        stockRecord.update(value=newValue)
        stockRecord = StockRecord.objects(product=productReg).first()
        if stockRecord.value == newValue:
            return True
        else:
            return False

    def getLatestChanges(self):
        oldChangeQueue = self.changeQueue.copy()
        self.changeQueue = list()
        return oldChangeQueue

    def applyChange(self, change: SentPlatformStockChange):
        if change.action == "set":
            if change.product.stockRecord is None:
                newStockRecord = StockRecord(product=change.product, value=change.value)
                newStockRecord.save()
            return self._setStock(change.product, change.value)
        elif change.action == "change":

            # This if shouldnt ever be triggered.
            if change.product.stockRecord is None:
                print("Huh... that's odd, an if that should never have been triggered in systemAPI has just been!")
                newStockRecord = StockRecord(
                    product=change.product,
                    value=self.stockManagerInstance.getProductStockCountFromPlatform(
                        change.platform.persistentIdentifier, change.product.sku
                    )
                )

                newStockRecord.save()
            return self._changeStock(change.product, change.value)
        else:
            systemProductDBLogger.critical(f"Alert! Unsupported action in change application attempt! Platform:{self.persistent_identifier}, change:{change}")
            return False
