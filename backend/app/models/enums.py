import enum


class PropertyType(str, enum.Enum):
    APARTMENT = "apartment"
    HOUSE = "house"
    PH = "ph"
    LAND = "land"
    COMMERCIAL = "commercial"
    OFFICE = "office"
    GARAGE = "garage"
    WAREHOUSE = "warehouse"


class ListingType(str, enum.Enum):
    SALE = "sale"
    RENT = "rent"
    TEMPORARY_RENT = "temporary_rent"


class CurrencyType(str, enum.Enum):
    ARS = "ARS"
    USD = "USD"


class SourceType(str, enum.Enum):
    ZONAPROP = "zonaprop"
    ARGENPROP = "argenprop"
    MERCADOLIBRE = "mercadolibre"
    INMOCLICK = "inmoclick"
    GUZMAN_GUZMAN = "guzman_guzman"
    TUCUMANPROPIEDADES = "tucumanpropiedades"
    GARCIA_PINTO = "garcia_pinto"
    LIMA_INMOBILIARIA = "lima_inmobiliaria"
