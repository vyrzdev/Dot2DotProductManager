from lazy.service import BaseService
from lazy.logger import rootLogger, SubLogger
import schedule
import redis

productDBLogger = SubLogger(
    "ProductDB",
    parent=rootLogger
)
from . import models
from . import stock
from .apis.baseAPI import BasePlatformAPI
from .apis.system import SystemAPI
from .apis.etsy import EtsyAPI
from .apis.square import SquareAPI
from .apis.woocommerce import WooCommerceAPI


class ProductDatabase(BaseService):
    persistentUniqueIdentifier = "product_database"
    name = "product_database"
    pretty_name = "Product Database"
    url_prefix = "/products"

    def __init__(self, serviceName, serviceRegistration, serviceResourceRegistration, accessControlManagerInstance):
        super().__init__(serviceName, serviceRegistration, serviceResourceRegistration, accessControlManagerInstance)
        self.productPlatformInstances = dict()
        self.redisClient = redis.Redis()
        productDBLogger.info("Redis Connection Successful")
        productDBLogger.info("Initialising stock management service")
        self.stockManager = stock.StockManager(self)

        productDBLogger.info("Loading Product Platforms")
        self.loadProductPlatform(SystemAPI)
        self.loadProductPlatform(SquareAPI)
        self.loadProductPlatform(WooCommerceAPI)
        self.loadProductPlatform(EtsyAPI)
        self.stockManager.start()
        productDBLogger.info("ProductDB Started!")

    @classmethod
    def ClassInitialise(cls):
        cls.userDataClass = models.User
        cls.Product = models.Product

    def loadProductPlatform(self, platformClass):
        # Get the platforms databased registration.
        # TODO: Figure out whats happening with registrations.
        platformRegistration = stock.models.ProductPlatform.objects(persistentIdentifier=platformClass.persistent_identifier).first()
        if platformRegistration is None:
            platformRegistration = stock.models.ProductPlatform(persistentIdentifier=platformClass.persistent_identifier)
            platformRegistration.save()
        newPlatformInstance = platformClass(redisClient=self.redisClient)
        self.productPlatformInstances[platformClass.persistent_identifier] = newPlatformInstance
        schedule_bool, interval = platformClass.schedule_task
        if schedule_bool:
            schedule.every(interval).seconds.do(newPlatformInstance.task())
        if newPlatformInstance.webhook_enabled:
            webhookURL = f"/webhook/{platformRegistration.persistentIdentifier}"
            webhookFunction = newPlatformInstance.webhook
            self.flaskServiceInstance.add_url_rule(webhookURL, view_func=webhookFunction, methods=["GET", "POST"])
            productDBLogger.info(f"Product Platform registered a webhook endpoint: {self.url_prefix}{webhookURL}")

        newPlatformInstance.register_stock_manager_instance(self.stockManager)

    def loadEndpoints(self):
        pass
