from .. import productDBLogger
from . import models
from . import interfaces
from . import consistency
from operator import attrgetter
from typing import List, Tuple
from threading import Thread
from time import sleep


class StockManager:
    def __init__(self, productDBServiceInstance):
        self._running = False
        self.syncStock = False
        self.productDBServiceInstance = productDBServiceInstance
        self.consistencyCheckManager = consistency.ConsistencyCheckManager(self)
        self.stockProcessorThreadInstance = Thread(target=self.stockProcessorThreadMethod, args=(), daemon=True)

    def start(self):
        self.syncStock = True
        self.consistencyCheckManager.start()
        productDBLogger.info("Consistency Checking Thread Started!")
        self.stockProcessorThreadInstance.start()
        productDBLogger.info("Stock Processor Started!")

    def stop(self):
        self.syncStock = False
        while self.stockProcessorThreadInstance.is_alive() or self._running:
            pass

    def stockProcessorThreadMethod(self):
        sleep(5)

        productDBLogger.info("Stock Processing Loop Began Safely!")
        while self.syncStock:
            self._running = True
            productDBLogger.debug("Processing received stock changes")
            self.processPlatformStockChanges()
            productDBLogger.debug("Processing pending stock transactions")
            pendingStockTransactions = self.getPendingStockTransactions()
            for pendingStockTransaction in pendingStockTransactions:
                self.processStockTransaction(pendingStockTransaction)
            sleep(1)
        self.consistencyCheckManager.stop()
        self._running = False

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
                    productDBLogger.debug(f"Applied Stock Action: change:{stockAction.sentChangeFormat()}")
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
                productDBLogger.debug(f"Stock Transaction Applied!")
                stockTransactionInstance.unlock()
                stockTransactionInstance.save()
            else:
                productDBLogger.error(f"Transaction failed to apply: id: {stockTransactionInstance.id}")
                stockTransactionInstance.unlock()
                stockTransactionInstance.save()
