import mongoengine
from .interfaces import SentPlatformStockChange
from decimal import Decimal
from datetime import datetime
from ..models import Product, ProductPlatform


class StockRecord(mongoengine.Document):
    product = mongoengine.ReferenceField(Product, required=True)
    value = mongoengine.DecimalField(required=True, default=Decimal(0))


class StockTransaction(mongoengine.Document):
    product = mongoengine.ReferenceField(Product, required=True)
    state = mongoengine.StringField(required=True, default="pending")  # pending, in_progress, applied
    locked = mongoengine.BooleanField(default=False)
    timeOccurred = mongoengine.DateTimeField(required=True)

    def actions(self, **kwargs):
        return StockAction.objects(transaction=self, **kwargs).all()

    def lock(self):
        self.update(locked=True)
        self.locked = True

    def unlock(self):
        self.locked = False
        self.update(locked=False)


class StockAction(mongoengine.Document):
    state = mongoengine.StringField(required=True, default="pending")  # pending applied
    originPlatformChangeID = mongoengine.StringField(required=True)
    transaction = mongoengine.ReferenceField(StockTransaction, required=True)
    action = mongoengine.StringField(required=True)  # change, set
    origin = mongoengine.ReferenceField(ProductPlatform, required=True)
    target = mongoengine.ReferenceField(ProductPlatform, required=True)
    value = mongoengine.DecimalField(required=True)

    def sentChangeFormat(self):
        self.transaction: StockTransaction
        return SentPlatformStockChange(
            product=self.transaction.product,
            action=self.action,
            value=self.value,
            timeInitiated=self.transaction.timeOccurred,
            platform=self.target
        )


class InconsistencyRecord(mongoengine.Document):
    product = mongoengine.ReferenceField(Product, required=True)
    time = mongoengine.DateTimeField(required=True, default=datetime.now)

    @property
    def counts(self):
        return InconsistencyStockCount.objects(record=self).all()

    def safeDelete(self):
        InconsistencyStockCount.objects(record=self).delete()
        InconsistencyCase.objects(record=self).delete()
        self.delete()


class InconsistencyStockCount(mongoengine.Document):
    record = mongoengine.ReferenceField(InconsistencyRecord, required=True)
    platform = mongoengine.ReferenceField(ProductPlatform, required=True)
    value = mongoengine.DecimalField(required=True)


class InconsistencyCase(mongoengine.Document):
    record = mongoengine.ReferenceField(InconsistencyRecord, required=True)
    status = mongoengine.StringField(required=True, default="unresolved")
