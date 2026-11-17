from dataclasses import dataclass

@dataclass
class ArmSetting:
    ID_NASTAVITEV: str = None
    ID_OBJEKT: str = None
    SF_NASTAVITEV: str = None
    SF_NASTAVITEV_VREDNOST: str = None
    TX_NASTAVITEV: str = None
    DT_DATUM_OD: str = None
    SY_UPORABNIK: str = None
    SY_VPIS: str = None
    SY_VELJAOD: str = None
    SY_TISP: str = None
    SY_TRAN: str = None