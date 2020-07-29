import mongoengine
from .interfaces import SentPlatformStockChange
from decimal import Decimal
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


class ConsistencyConflict(mongoengine.Document):
    product = mongoengine.ReferenceField(Product, required=True)
    state = mongoengine.StringField(required=True, default="pending") # pending, resolved

    @property
    def counts(self):
        return ConsistencyStockCount.objects(conflict=self).all()

    def resolve(self, resolvedValue: Decimal):
        self.state = "resolved"
        self.product.consistency_lock = False
        pendingTransactions = StockTransaction()
        self.save()


class ConsistencyStockCount(mongoengine.Document):
    platform = mongoengine.ReferenceField(ProductPlatform, required=True)
    conflict = mongoengine.ReferenceField(ConsistencyConflict, required=True)
    value = mongoengine.DecimalField(required=True)

