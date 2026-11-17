# ArmUtils

## Python knjiznica

### Uporaba

ArmUtils lahko uporabljamo v Python projektih, ki potrebujejo komunikacijo z ARM.

Sestavljena je iz glavnega razreda ArmUtils, ki omogoca razlicne metode za pridobivanje nastavitev in preverjanje uporabniskih pooblastil.

Uvoz knjiznice v projekt:
```
from ArmUtils.lib import ArmUtils
```

Opcijsko lahko uvozimo tudi razred ArmSetting, ce ne zelimo funkcionalnosti razreda ArmUtils:
```
from ArmUtils import ArmSetting
```

### Inicializacija razreda ArmUtils

Za inicializacijo razreda potrebujemo:
    - ctx (ssl certifikat)
    - ime aplikacije
    - url do ARM (opcijsko - default je usmerjen na was-test)

```
from ArmUtils.lib import ArmUtils

arm = ArmUtils(ctx, "moja_aplikacija") 
```

### Implementiranje metode

Razred ArmUtils ima naslednje metode:

    - getArmSettings() -> metoda vrne seznam nastavitev (list[ArmSetting]) glede na ime aplikacije, ki smo ga dolocili pri inicializaciji
    
    - getSetting(ime_nastavitve) -> metoda vrne vrednost nastavitve

    - getSettingObject(ime_nastavitve) -> metoda vrne celoten objekt nastavitve

    - checkPrivileges(guid, SF_OBJEKT, SF_APLIKACIJA, SF_AKCIJA) -> metoda vrne true/false glede na to ali ima uporabnik pooblastila

### Nalaganje nastavitev v okolje aplikacije

```python
from ArmUtils.environment import load_environment
load_environment("ime_aplikacije")  # pred branjem nastavitev in uvozi aplikacije
```

`ARM_APP_NAME` po potrebi nadomesti podano ime; `ARM_SETTINGS_URL` nadomesti
privzeti URL knjižnice. Dodatek uporabi `ArmUtils.getArmSettings()` in naloži
`SF_NASTAVITEV` / `SF_NASTAVITEV_VREDNOST` v okolje procesa. ARM ima prednost;
manjkajoči ključi obdržijo okolje, ob napaki ali praznem odgovoru pa okolje ostane
nespremenjeno. Rezultat se hrani do konca procesa. `None` v polju vrednosti pomeni
prazen niz. Dvojniki in neveljavni ključi/vrednosti zavrnejo celoten odgovor.

TLS je privzeto preverjen. Podprte so bootstrap okoljske spremenljivke
`ARM_TLS_VERIFY`, `ARM_CA_FILE`, `ARM_CA_PATH`, `ARM_CLIENT_CERT_FILE`,
`ARM_CLIENT_KEY_FILE`, `ARM_CLIENT_KEY_PASSWORD`. Njihove datoteke morajo biti
berljive aplikaciji. Teh začetnih povezovalnih nastavitev ni mogoče dobiti iz ARM
pred prvim klicem. Dodatek ne nalaga `.env` datotek; uporabi okolje procesa.
