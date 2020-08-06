from .interfaces import StockCount
from .models import InconsistencyStockCount, InconsistencyRecord, InconsistencyCase, ProductPlatform
from typing import List
from datetime import datetime, timedelta
from time import sleep
from threading import Thread
from . import productDBLogger


class ConsistencyCheckManager:
    def __init__(self, stockManagerInstance):
        self._stop = True
        self.stockManager = stockManagerInstance
        self.consistencyFetchThreadInstance = Thread(target=self.consistencyFetchThreadMethod, args=(), daemon=True)
        self.caseSpawnerThreadInstance = Thread(target=self.caseSpawnerThreadMethod, args=(), daemon=True)

    def start(self):
        self._stop = False
        self.caseSpawnerThreadInstance.start()
        self.consistencyFetchThreadInstance.start()

    def stop(self):
        self._stop = True
        while self.caseSpawnerThreadInstance.is_alive() or self.consistencyFetchThreadInstance.is_alive():
            pass

    def restart(self):
        self.stop()
        self.start()

    def consistencyFetchThreadMethod(self):
        while not self._stop:
            self._platformConsistencyFetchAndCheck()
            # TODO: Configurable value
            sleep(120)

    def _platformConsistencyFetchAndCheck(self):
        stockCounts = self._getAllStockCounts()
        self._processStockCounts(stockCounts)

    # TODO: Concurrency for HTTP requests
    # Will require a mild rework on the PlatformAPI end as well.
    def _getAllStockCounts(self) -> List[StockCount]:
        allFormattedCounts = list()
        for platformInstance in self.stockManager.productDBServiceInstance.productPlatformInstances.values():
            allFormattedCounts += platformInstance.getAllStockCounts()
        return allFormattedCounts

    # TODO: Implement Exceptions
    def _getProductStockCountFromPlatform(self, platformIdentity, productObject):
        platformAPIInstance = self.stockManager.productDBServiceInstance.productPlatformInstances.get(platformIdentity)
        if platformAPIInstance is None:
            return None
        else:
            return platformAPIInstance.getProductStockCount(productObject)

    @staticmethod
    def _processStockCounts(stockCounts: List[StockCount]):
        interimStockCountCollectionStore = dict()
        # Iterate over every single stock count.
        for stockCount in stockCounts:
            # TODO: This may be removed soon.
            if stockCount.product.consistency_lock:
                pass
            else:
                # Get the existing stockCollection dict if it exists, if it doesnt, will return None
                stockCollection = interimStockCountCollectionStore.get(stockCount.product.sku)
                if stockCollection is None:
                    # Create a new stockCollection dict if none exists yet.
                    interimStockCountCollectionStore[stockCount.product.sku] = {
                        "value": stockCount.value,
                        "inconsistent": False,
                        "product": stockCount.product,
                        "counts": [
                            stockCount
                        ]
                    }
                else:
                    # If stockCollection dict has different value to this count, then stock must be inconsistent. As such, trip that flag.
                    if stockCollection.get("value") != stockCount.value:
                        interimStockCountCollectionStore[stockCount.product.sku]["inconsistent"] = True
                    else:
                        pass
                    # Add this stock count to the collections counts list.
                    interimStockCountCollectionStore[stockCount.product.sku]["counts"].append(stockCount)

        # Iterate over all the stock collections to generate and store inconsistency records, or delete them.
        for stockCollection in interimStockCountCollectionStore.values():
            stockCollectionInconsistent = stockCollection.get("inconsistent")
            stockCollectionProduct = stockCollection.get("product")
            if stockCollectionInconsistent:
                # If the stock is inconsistent, query existing inconsistencyRecords.
                inconsistencyRecord = InconsistencyRecord.objects(product=stockCollectionProduct).first()
                if inconsistencyRecord is None:
                    # If there is not an existing record, create and save a new one.
                    inconsistencyRecord = InconsistencyRecord(product=stockCollectionProduct)
                    inconsistencyCounts = list()
                    for count in stockCollection.get("counts"):
                        productPlatform = ProductPlatform.objects(persistentIdentifier=count.platformIdentity).first()
                        inconsistencyCounts.append(
                            InconsistencyStockCount(
                                record=inconsistencyRecord,
                                platform=productPlatform,
                                value=count.value
                            )
                        )
                    inconsistencyRecord.save()
                    [count.save() for count in inconsistencyCounts]
                else:
                    # Otherwise pass
                    pass
            else:
                # Since product's stock is demonstrably no longer inconsistent, delete the record.
                if InconsistencyRecord.objects(product=stockCollectionProduct).first() is not None:
                    InconsistencyRecord.objects(product=stockCollectionProduct).first().safeDelete()
                    productDBLogger.debug("Product Inconsistency Record Safe Deleted, as stock was no longer inconsistent.")
                else:
                    pass
        print("Finished Consistency Check")

    def caseSpawnerThreadMethod(self):
        while not self._stop:
            self._caseSpawner()
            sleep(5)

    @staticmethod
    def _caseSpawner():
        # TODO: Configurable parameter.
        tenMinutesAgo = datetime.now() - timedelta(minutes=10)
        recordsToBuildCasesFor = InconsistencyRecord.objects(time__lte=tenMinutesAgo).all()
        for record in recordsToBuildCasesFor:
            case = InconsistencyCase.objects(record=record).first()
            if case is None:
                case = InconsistencyCase(record=record)
                case.save()
            else:
                pass
