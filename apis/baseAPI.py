from ..stock.interfaces import SentPlatformStockChange
from .. import productDBLogger
from ..stock import StockManager
from typing import Union
import redis


class BasePlatformAPI:
    persistent_identifier = None
    webhook_enabled = False
    schedule_task = False, None  # Schedule a new task bool, int seconds between running.

    def __init__(self, redisClient: redis.Redis = None):
        self.redisClient = redisClient
        self.stockManagerInstance: Union[None, StockManager] = None

    def webhook(self):
        productDBLogger.warn(f"Service: {self.persistent_identifier} has no webhook function defined, and yet it was called!")
        return "Error!"

    def task(self):
        pass

    def register_stock_manager_instance(self, stockManagerInstance: StockManager):
        self.stockManagerInstance = stockManagerInstance

    def getLatestChanges(self):
        productDBLogger.critical(f"{self.__class__.persistent_identifier} has no getLatestOrders method!!!!")

    def applyChange(self, change):
        productDBLogger.critical(f"{self.__class__.persistent_identifier} has no applyChange method!!!!")
        return False
