"""Twilion webhookien allekirjoituksen tarkistus (X-Twilio-Signature).

Ilman tata kuka tahansa ngrok-osoitteen tietava voisi syottaa puheluun "vastauksia".
Allekirjoitus lasketaan julkisesta osoitteesta, jota Twilio kutsui, ei localhostista.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from twilio.request_validator import RequestValidator

from app.config import Settings, hae_asetukset
from app.puhelu.soittaja import SoittoEiKaytossa


def julkinen_url(asetukset: Settings) -> str:
    if not asetukset.julkinen_url:
        raise SoittoEiKaytossa("Puuttuu .env:sta: JULKINEN_URL (ngrokin https-osoite)")
    return asetukset.julkinen_url.rstrip("/")


async def twilion_lomake(
    request: Request, asetukset: Annotated[Settings, Depends(hae_asetukset)]
) -> dict[str, str]:
    """FastAPI-dependency: palauttaa Twilion lomakekentat, jos allekirjoitus on oikein."""
    if not asetukset.twilio_auth_token:
        raise SoittoEiKaytossa("Puuttuu .env:sta: TWILIO_AUTH_TOKEN")

    lomake = await request.form()
    kentat = {avain: str(arvo) for avain, arvo in lomake.items()}
    kysely = f"?{request.url.query}" if request.url.query else ""
    kutsuttu_url = f"{julkinen_url(asetukset)}{request.url.path}{kysely}"
    allekirjoitus = request.headers.get("X-Twilio-Signature", "")

    validaattori = RequestValidator(asetukset.twilio_auth_token)
    if not validaattori.validate(kutsuttu_url, kentat, allekirjoitus):
        raise HTTPException(status_code=403, detail="Virheellinen Twilio-allekirjoitus")
    return kentat
