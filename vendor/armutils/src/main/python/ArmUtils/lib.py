import httpx
import logging
import xml.etree.ElementTree as ET

from .ArmSetting import ArmSetting

logger = logging.getLogger(__name__)

class ArmUtils:
    
    ctx = None
    app_name = ""
    url = ""

    arm_settings: list[ArmSetting] = None
    
    
    def __init__(
        self, 
        ctx, 
        app_name, 
        url = "https://was-test.zpiz.si:9443/SFARSS002000/rest/v1/settings"
    ):
        self.ctx = ctx
        self.app_name = app_name
        self.url = url
        
        
        
    def  getArmSettings(self) -> list[ArmSetting]:
        settings_dict = []
        settings = []
        params = {
            "sfObjekt": self.app_name
        }
        
        logger.info("[ArmUtils]: Pridobivanje ARM nastavitev ...")
        try:
            with httpx.Client(verify=self.ctx, params=params, timeout=10, http2=False) as c:
                response = c.get(self.url)
                response.raise_for_status()
                
            xml_string = response.text
            root = ET.fromstring(xml_string)

            for child in root:
                setting = {}
                for c in child:
                    setting[c.tag] = c.text
                settings_dict.append(setting)
                
            for s in settings_dict:
                arm_setting = ArmSetting(**s)
                settings.append(arm_setting)
            
            logger.info("[ArmUtils]: ARM nastavitve pridobljene.")
            self.arm_settings = settings
            return settings
        except Exception as e:
            logger.info(f"[ArmUtils]: Napaka pri pridobivanju ARM nastavitev: {e}")
            return None
        
    
    def getSetting(self, key: str) -> str:
        if (self.arm_settings == None):
            logger.warning("Seznam ARM nastavitev je prazen!")
            return None
        logger.info(f"[ArmUtils]: Pridobivam vrednost nastavitve {key}")
        for s in self.arm_settings:
            if s.SF_NASTAVITEV == key:
                return s.SF_NASTAVITEV_VREDNOST
        return None

    def getSettingObject(self, key: str) -> ArmSetting:
        for s in self.arm_settings:
            if s.SF_NASTAVITEV == key:
                return s
        return None
    
    
    def checkPrivileges(self, guid, SF_OBJEKT, SF_APLIKACIJA, SF_AKCIJA) -> bool:
    
        # https://was-razv.zpiz.si/SFARSS001000/rest/v1/checkPrivileges?guid=123&sfObjekt=asd&sfAplikacija=asd&sfAkcija=asd&sfUporabnik=asd
        
        
        # Najprej klicemo loggedUser, preverimo odgovor
        # Nato z loggedUser (sfUporabnik) klicemo checkPrivileges
        
        settingObject = self.getSettingObject("LOGGED_USER_URL")
        
        loggedUser_url = settingObject.SF_NASTAVITEV_VREDNOST
        loggedUser_params = {
            "guid": guid
        }
        
        try:
            with httpx.Client(verify=self.ctx, params=loggedUser_params, timeout=10, http2=False) as c:
                loggedUser_response = c.get(loggedUser_url)
                loggedUser_response.raise_for_status()
                
            loggedUser = loggedUser_response.text
            if loggedUser_response.status_code != 200:
                return None
            logger.info(f"Uporabnik: {loggedUser}")
        except Exception as e:
            logger.info(f"NAPAKA pri logged user: {e}")
            return None
        
        url = self.getSetting("CHECK_PRIVILEGES_URL")
        params = {
            "guid": guid,
            "sfObjekt": SF_OBJEKT,
            "sfAplikacija": SF_APLIKACIJA,
            "sfAkcija": SF_AKCIJA,
            "sfUporabnik": loggedUser
        }
        
        try:
            logger.info("Preverjanje pooblastil ...")
            with httpx.Client(verify=self.ctx, params=params, timeout=10, http2=False) as c:
                response = c.get(url)
                response.raise_for_status()
            pooblastila = response.text
            logger.info(f"Pooblastila: {response.text}")
            if pooblastila == "1":
                logger.info("Pooblastila ok.")
                return True
            else:
                logger.warning(f"Uporabnik nima pooblastil.")
                return False
        except Exception as e:
            logger.error(f"NAPAKA pri checkPrivileges: {e}")
            return False