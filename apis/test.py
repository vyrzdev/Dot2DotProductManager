import modules.productDB.stock.interfaces
from .. import interfaces
from . import baseAPI
from datetime import datetime


class TestAPI(baseAPI.BasePlatformAPI):
    persistent_identifier = "testAPI"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.changeQueue = list()

    def getLatestChanges(self):
        oldChangeQueue = self.changeQueue.copy()
        self.changeQueue = list()
        return oldChangeQueue

    def applyChange(self, change):
        print(f"<testAPI> Applied Change: {change}")
        return True

    def getAllStockCounts(self):
        return list()
