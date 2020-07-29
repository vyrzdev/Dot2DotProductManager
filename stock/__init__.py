from .. import productDBLogger
from . import models
from . import interfaces
from operator import attrgetter
from typing import List, Tuple
from threading import Thread
from time import sleep

class StockManager:
    def __init__(self, productDBServiceInstance):
        self.productDBServiceInstance = productDBServiceInstance
        self.syncStock = False
        self._running = False
        self.secondOpinionConsistencyStore = list()
        self.stockProcessorThreadInstance = Thread(target=self.stockProcessorThreadMethod, args=(), daemon=True)

    def startSync(self):
        self.syncStock = True
        self.stockProcessorThreadInstance.start()

    def stopSync(self):
        self.syncStock = False

    def stockProcessorThreadMethod(self):
        sleep(10)
        productDBLogger.info("Stock Processor Started!")
        # Initial Stock Consistency Assurance (Locks products that are inconsistent)
        self.runConsistencyCheck()
        productDBLogger.info("Initial Consistency Check Complete!")
        productDBLogger.info("Stock Processing Loop Began Safely!")
        while self.syncStock:
            self._running = True
            self.processPlatformStockChanges()
            pendingStockTransactions = self.getPendingStockTransactions()
            for pendingStockTransaction in pendingStockTransactions:
                self.processStockTransaction(pendingStockTransaction)
            self.runConsistencyCheck()

    def runConsistencyCheck(self):
        stillInconsistentRecords = self.getStillInconsistentRecordsFromSecondOpinionStore()
        self._generateConflictCases(stillInconsistentRecords)
        stockCounts = self._getAllPlatformStockCounts()
        inconsistentStockCountRecords = self._findInconsistentStockCounts(stockCounts)
        if len(inconsistentStockCountRecords) > 0:
            consistent = False
        else:
            consistent = True
        self.addRecordsForSecondOpinion(inconsistentStockCountRecords)
        return consistent

    ####################################################################################
    def isProductStockInconsistent(self, productObject: models.Product) -> Tuple[bool, List[interfaces.StockCount]]:
        productStockRecords = list()
        for platformRegistration in productObject.registered_services:
            platformAPI = self.productDBServiceInstance.productPlatformInstances.get(platformRegistration.persistentIdentifier)
            if platformAPI is None:
                productDBLogger.error("A service was removed!")
                pass
            else:
                stockRecord = platformAPI.getProductStockCount(productObject)
                if stockRecord is not None:
                    productStockRecords.append(stockRecord)
                else:
                    pass
        if len(productStockRecords) == 0:
            print("Empty list of stock records???!!!?!?!")
            print(f"This is for product with SKU: {productObject.sku}")
            print(f"This product claims to be registered on: {productObject.registered_services}")
        else:
            print(productStockRecords)
            firstStockRecord = productStockRecords.pop(0)
            print(productStockRecords)
            productStockValue = firstStockRecord.value
            for productStockRecord in productStockRecords:
                if productStockRecord.value != productStockValue:
                    return True, productStockRecords
                else:
                    pass
        return False, productStockRecords

    def getProductStockCountFromPlatform(self, platformIdentity: str, productObject: models.Product):
        platformAPIInstance = self.productDBServiceInstance.productPlatformInstances.get(platformIdentity)
        if platformAPIInstance is None:
            return None
        else:
            return platformAPIInstance.getProductStockCount(productObject)

    def _getAllPlatformStockCounts(self) -> List[interfaces.StockCount]:
        allCounts = list()
        for platformInstance in list(self.productDBServiceInstance.productPlatformInstances.values()):
            allCounts = allCounts + platformInstance.getAllStockCounts()
        return allCounts

    @staticmethod
    def _findInconsistentStockCounts(stockCountList: List[interfaces.StockCount]) -> List[dict]:
        stockCountDict = dict()
        inconsistentProducts = list()
        for stockCount in stockCountList:
            if stockCount.product.consistency_lock:
                pass
            else:
                sortedRecord = stockCountDict.get(stockCount.product)
                if sortedRecord is None:
                    stockCountDict[stockCount.product] = {
                        "value": stockCount.value,
                        "inconsistent": False,
                        "product": stockCount.product,
                        "counts": [
                            stockCount
                        ]
                    }
                else:
                    if sortedRecord.get("value") != stockCount.value:
                        stockCountDict[stockCount.product]["inconsistent"] = True
                        inconsistentProducts.append(stockCount.product)
                    else:
                        pass
                    stockCountDict[stockCount.product]["counts"].append(stockCount)
        inconsistentStockRecords = list()
        for product in inconsistentProducts:
            inconsistentStockRecords.append(stockCountDict.get(product))
        return inconsistentStockRecords

    def addRecordsForSecondOpinion(self, inconsistentStockRecords: List[dict]) -> None:
        for inconsistentStockRecord in inconsistentStockRecords:
            self.secondOpinionConsistencyStore.append(inconsistentStockRecord)

    def getStillInconsistentRecordsFromSecondOpinionStore(self) -> List[dict]:
        stillInconsistentRecords = list()
        for inconsistentRecord in self.secondOpinionConsistencyStore:
            inconsistent, stockCounts = self.isProductStockInconsistent(inconsistentRecord.get("product"))
            if inconsistent:
                stillInconsistentRecords.append({
                    "product": inconsistentRecord.get("product"),
                    "inconsistent": True,
                    "counts": stockCounts
                })
            else:
                pass
        self.secondOpinionConsistencyStore = list()
        return stillInconsistentRecords

    @staticmethod
    def _generateConflictCases(inconsistentStockRecords: List[dict]) -> None:
        for inconsistentStockRecord in inconsistentStockRecords:
            toSave = list()
            inconsistentProduct = inconsistentStockRecord.get("product")
            if inconsistentProduct.consistency_lock:
                pass
            else:
                inconsistentProduct.consistency_lock = True
                toSave.append(inconsistentProduct)
                newConsistencyCase = models.ConsistencyConflict(product=inconsistentProduct)
                toSave.append(newConsistencyCase)
                count: interfaces.StockCount
                for count in inconsistentStockRecord.get("counts"):
                    consistencyStockCount = models.ConsistencyStockCount(
                        platform=models.ProductPlatform.objects(persistentIdentifier=count.platformIdentity).first(),
                        conflict=newConsistencyCase,
                        value=count.value
                    )
                    toSave.append(consistencyStockCount)
                [obj.save() for obj in toSave]
                productDBLogger.warn(f"Conflict Case!!! Stock is inconsistent for productSKU: {inconsistentProduct.sku}")

    ####################################################################################
    def getServiceChanges(self, service):
        return self.productDBServiceInstance.productPlatformInstances.get(service).getLatestChanges()

    def getAllLatestChanges(self) -> List[interfaces.ReceivedPlatformStockChange]:
        collectedChanges = list()
        for serviceInstance in list(self.productDBServiceInstance.productPlatformInstances.values()):
            collectedChanges = collectedChanges + serviceInstance.getLatestChanges()
        sortedCollectedChanges = sorted(collectedChanges, key=attrgetter("timeOccurred"))
        return sortedCollectedChanges

    def processPlatformStockChanges(self) -> None:
        # Get all changes
        changes: List[interfaces.ReceivedPlatformStockChange] = self.getAllLatestChanges()

        for change in changes:
            # Get the product registration for the product referred to in this change.
            productReg: models.Product = models.Product.objects(sku=change.productSKU).first()

            # If change is for a product that hasn't been databased...
            # We need to ignore the change, and wait for the new product to be picked up in a stock count.
            # TODO: Figure this shit out.
            if productReg is None:
                pass
            else:
                # Create a new stockTransaction for this product.
                newStockTransaction: models.StockTransaction = models.StockTransaction(product=productReg, timeOccurred=change.timeOccurred, state="pending")

                # Start building a list of actions to bind to this transaction.
                newStockActions: List[models.StockAction] = list()

                # Save the origin platform's registration.
                originPlatformReg = models.ProductPlatform.objects(persistentIdentifier=change.platformIdentity).first()

                # Get the platform registrations for which the product exists.
                platformReg: models.ProductPlatform
                for platformReg in productReg.registered_services:
                    # If platform where action is being generated is the origin, don't generate an action.
                    # otherwise, do generate an action, and add it to the list of actions to bind.
                    if platformReg == originPlatformReg:
                        pass
                    else:
                        newStockAction: models.StockAction = models.StockAction(
                            transaction=newStockTransaction,
                            originPlatformChangeID=change.platformChangeID,
                            state="pending",
                            action=change.action,
                            value=change.value,
                            origin=originPlatformReg,
                            target=platformReg
                        )
                        newStockActions.append(newStockAction)

                # Save and undo locks on products.
                newStockTransaction.save()
                for stockAction in newStockActions:
                    stockAction.save()
                newStockTransaction.unlock()
                newStockTransaction.save()

    ##################################################################################
    @staticmethod
    def getPendingStockTransactions() -> List[models.StockTransaction]:
        return models.StockTransaction.objects(locked=False, state="pending").order_by("timeOccurred").all()

    @staticmethod
    def getNextPendingStockTransaction() -> models.StockTransaction:
        return models.StockTransaction.objects(locked=False, state="pending").order_by("timeOccurred").first()

    def sendPlatformStockChange(self, stockChange: interfaces.SentPlatformStockChange):
        platformIdentity = stockChange.platform.persistentIdentifier
        platformAPIInstance = self.productDBServiceInstance.productPlatformInstances.get(platformIdentity)
        if platformAPIInstance is None:
            productDBLogger.error("Targeted Platform not initialised!")
            return False
        else:
            return platformAPIInstance.applyChange(stockChange)

    def processStockTransaction(self, stockTransactionInstance: models.StockTransaction):
        if stockTransactionInstance.product.consistency_lock:
            pass
        else:
            stockTransactionInstance.lock()
            for stockAction in stockTransactionInstance.actions(state="pending"):
                applied = self.sendPlatformStockChange(stockAction.sentChangeFormat())
                if applied:
                    productDBLogger.debug(f"Applied Stock Transaction: change:{stockAction.sentChangeFormat()}")
                    stockAction.state = "applied"
                    stockAction.save()
                else:
                    productDBLogger.error(f"Failed to apply stock action!!!! ProductSKU: {stockAction.transaction.product.sku}, platform: {stockAction.target.persistentIdentifier}")
                    pass

            # Check all actions are applied
            transactionApplied = True
            for action in stockTransactionInstance.actions():
                if action.state != "applied":
                    transactionApplied = False
                else:
                    pass
            if transactionApplied:
                stockTransactionInstance.state = "applied"
                stockTransactionInstance.unlock()
                stockTransactionInstance.save()
            else:
                productDBLogger.error(f"Transaction failed to apply: id: {stockTransactionInstance.id}")
                stockTransactionInstance.unlock()
                stockTransactionInstance.save()
