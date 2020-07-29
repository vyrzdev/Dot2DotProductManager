from decimal import Decimal


def dround(decimal_number, decimal_places):
    return decimal_number.quantize(Decimal(10) ** -decimal_places).normalize()
