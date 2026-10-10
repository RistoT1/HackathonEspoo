"""Reuna Twilioon: ainoa paikka joka soittaa oikeasti. Testit korvaavat Soittajan feikilla."""

from typing import Annotated, Protocol

from fastapi import Depends
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from app.config import Settings, hae_asetukset


class SoittoEiKaytossa(RuntimeError):
    """Twilion asetukset puuttuvat .env:sta."""


class SoittoEpaonnistui(RuntimeError):
    """Twilio hylkasi soiton (esim. numeroa ei vahvistettu, maa ei sallittu)."""


# Puhelun enimmaiskesto (Twilion time_limit). Pitaa puhelun lyhyena myos jos jokin jumittaa.
MAX_KESTO_S = 300


class Soittaja(Protocol):
    def soita(self, numero: str, twiml: str) -> str:
        """Kaynnistaa soiton valmiilla TwiML:lla ja palauttaa Twilion CallSidin."""
        ...

    def soita_webhookilla(self, numero: str, url: str, tila_url: str | None) -> str:
        """Soitto, jonka kulun backend ohjaa: Twilio kutsuu url:ia vastattaessa ja jokaisen
        vuoron jalkeen, tila_url:ia kun puhelu paattyy (myos ei vastattu / varattu)."""
        ...


class TwilioSoittaja:
    def __init__(self, account_sid: str, auth_token: str, lahettaja: str) -> None:
        self._client = Client(account_sid, auth_token)
        self._lahettaja = lahettaja

    def soita(self, numero: str, twiml: str) -> str:
        try:
            puhelu = self._client.calls.create(to=numero, from_=self._lahettaja, twiml=twiml)
        except TwilioRestException as virhe:
            raise SoittoEpaonnistui(f"Twilio {virhe.code}: {virhe.msg}") from virhe
        return puhelu.sid

    def soita_webhookilla(self, numero: str, url: str, tila_url: str | None) -> str:
        lisat = {"status_callback": tila_url, "status_callback_method": "POST"} if tila_url else {}
        try:
            puhelu = self._client.calls.create(
                to=numero,
                from_=self._lahettaja,
                url=url,
                method="POST",
                time_limit=MAX_KESTO_S,
                **lisat,
            )
        except TwilioRestException as virhe:
            raise SoittoEpaonnistui(f"Twilio {virhe.code}: {virhe.msg}") from virhe
        return puhelu.sid


def hae_soittaja(asetukset: Annotated[Settings, Depends(hae_asetukset)]) -> Soittaja:
    """FastAPI-dependency. Testit korvaavat taman (app.dependency_overrides)."""
    puuttuvat = [
        nimi
        for nimi, arvo in (
            ("TWILIO_ACCOUNT_SID", asetukset.twilio_account_sid),
            ("TWILIO_AUTH_TOKEN", asetukset.twilio_auth_token),
            ("TWILIO_NUMERO", asetukset.twilio_numero),
        )
        if not arvo
    ]
    if puuttuvat:
        raise SoittoEiKaytossa(f"Puuttuu .env:sta: {', '.join(puuttuvat)}")
    return TwilioSoittaja(
        asetukset.twilio_account_sid, asetukset.twilio_auth_token, asetukset.twilio_numero
    )
