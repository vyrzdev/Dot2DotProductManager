from . import baseAPI
from lazy.logger import rootLogger, SubLogger
systemProductDBLogger = SubLogger(
    "systemProductPlatform",
    parent=rootLogger
)
from ..models import Product
from ..stock.models import StockRecord
from ..stock.interfaces import ReceivedPlatformStockChange, SentPlatformStockChange, StockCount
import datetime
from typing import List
from uuid import uuid4


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
                value=float(stockRecord.value),
                platformIdentity=self.persistent_identifier
            )

    def getAllStockCounts(self):
        stockCounts: List[StockCount] = list()
        stockRecord: StockRecord
        for stockRecord in StockRecord.objects().all():
            stockCounts.append(
                StockCount(
                    product=stockRecord.product,
                    value=float(stockRecord.value),
                    platformIdentity=self.persistent_identifier
                )
            )
        return stockCounts

    def setStock(self, productSKU: str, value: float):
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

    def changeStock(self, productSKU, value: float):
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
    def _setStock(productReg, value):
        stockRecord = StockRecord.objects(product=productReg).first()
        if stockRecord is None:
            stockRecord = StockRecord(product=productReg)
            stockRecord.save()
        newValue = value
        stockRecord.update(value=newValue)
        stockRecord = StockRecord.objects(product=productReg).first()
        if stockRecord.value == newValue:
            return True
        else:
            return False

    @staticmethod
    def _changeStock(productReg, value):
        stockRecord = StockRecord.objects(product=productReg).first()
        if stockRecord is None:
            stockRecord = StockRecord(product=productReg)
            stockRecord.save()
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
        # self.changeQueue.append(
        #     ReceivedPlatformStockChange(
        #         productSKU="00100001",
        #         value=10,
        #         action="change",
        #         timeOccurred=datetime.datetime.now(),
        #         platformChangeID=str(uuid4()),
        #         platformIdentity=self.persistent_identifier
        #     )
        # )
        return oldChangeQueue

    def applyChange(self, change: SentPlatformStockChange):
        if change.action == "set":
            return self._setStock(change.product, change.value)
        elif change.action == "change":
            return self._changeStock(change.product, change.value)
        else:
            systemProductDBLogger.critical(f"Alert! Unsupported action in change application attempt! Platform:{self.persistent_identifier}, change:{change}")
            return False