import mongoengine
from lazy.registrationModels import UserRegistration
from typing import List


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
        stockRecord: StockRecord = StockRecord.objects(product=self).first()
        if stockRecord is None:
            stockRecord = StockRecord(product=self)
            stockRecord.save()
        return stockRecord

    # Checks if service registered.
    def is_registered_on_service(self, service_persistent_identifier):
        platformReg = ProductPlatform.objects(persistentIdentifier=service_persistent_identifier).first()
        if platformReg is None:
            return False

        platformProductStore = PlatformProductStore.objects(product=self, platform=platformReg).first()
        if platformProductStore is None:
            return False
        else:
            return True

    # Add service to product's registered list.
    def register_service(self, service_persistent_identifier):
        if self.is_registered_on_service(service_persistent_identifier):
            pass
        else:
            platformReg = ProductPlatform.objects(persistentIdentifier=service_persistent_identifier).first()
            if platformReg is None:
                pass
            else:
                platformProductStore = PlatformProductStore(product=self, platform=platformReg)
                platformProductStore.save()

    # Removes service from product's registered list.
    def unregister_service(self, service_persistent_identifier):
        platformReg = ProductPlatform.objects(persistentIdentifier=service_persistent_identifier).first()
        if platformReg is None:
            pass
        else:
            platformProductStore = PlatformProductStore.objects(product=self, platform=platformReg).first()
            if platformProductStore is None:
                print("SCUFFED-LOG: Product was told to unregister a non-existent service. Something is clearly going wrong, with a high danger of entire system corruption!")
                pass
            else:
                platformProductStore.delete()

    # Gets all services the product is reportedly registered on.
    @property
    def registered_services(self):
        productPlatforms: List[ProductPlatform] = [platformProductStore.platform for platformProductStore in PlatformProductStore.objects(product=self).all()]
        return productPlatforms


class ProductPlatform(mongoengine.Document):
    persistentIdentifier = mongoengine.StringField(required=True)


class PlatformProductStore(mongoengine.Document):
    platform = mongoengine.ReferenceField(ProductPlatform, required=True)
    product = mongoengine.ReferenceField(Product, required=True)
