from dataclasses import dataclass
from datetime import datetime
from ..models import Product, ProductPlatform
from decimal import Decimal


@dataclass
class ReceivedPlatformStockChange:
    productSKU: str
    action: str  # set, change
    value: Decimal
    timeOccurred: datetime
    platformChangeID: str
    platformIdentity: str


@dataclass
class SentPlatformStockChange:
    product: Product
    action: str  # set, change
    value: Decimal
    timeInitiated: datetime
    platform: ProductPlatform


@dataclass
class StockCount:
    product: Product
    value: Decimal
    platformIdentity: str
