from dataclasses import dataclass
from datetime import datetime
from ..models import Product, ProductPlatform


@dataclass
class ReceivedPlatformStockChange:
    productSKU: str
    action: str  # set, change
    value: float
    timeOccurred: datetime
    platformChangeID: str
    platformIdentity: str


@dataclass
class SentPlatformStockChange:
    product: Product
    action: str  # set, change
    value: float
    timeInitiated: datetime
    platform: ProductPlatform


@dataclass
class StockCount:
    product: Product
    value: float
    platformIdentity: str
