import mongoengine
from lazy.registrationModels import UserRegistration


class User(mongoengine.Document):
    userReg = mongoengine.ReferenceField(UserRegistration, required=True)


class Product(mongoengine.Document):
    sku = mongoengine.StringField(required=True)
    consistency_lock = mongoengine.BooleanField(default=False, required=True)
    metaData = mongoengine.DictField(default=dict)

    @property
    def stockRecord(self):
        # TODO: Maybe a big bad?
        from .stock.models import StockRecord
        return StockRecord.objects(product=self).first()


class ProductPlatform(mongoengine.Document):
    persistentIdentifier = mongoengine.StringField(required=True)


