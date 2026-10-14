from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    # .env on repon juuressa (sama kuin docker composella); backend/.env ohittaa sen tarvittaessa.
    # Absoluuttiset polut: toimii mista tahansa kaynnistettyna (repon juuri, IDE, backend/).
    model_config = SettingsConfigDict(
        env_file=(_BACKEND.parent / ".env", _BACKEND / ".env"), extra="ignore"
    )

    database_url: str
    # Sallii /dev/-reitit, jotka kirjoittavat demodataan. Vain paikallisesti, ei tuotantoon.
    dev_tila: bool = False
    # Soitettavien skannauksen (laakemuutosten tunnistus -> soittojono) vali minuutteina.
    # Ensimmainen ajo heti kaynnistyksessa. 0 = pois (ajetaan kasin: POST /puhelutehtavat/tunnista).
    tunnistus_vali_minuutit: int = 15
    # Twilioon vahvistettu demonumero (E.164), jos vanhukselle ei ole omaa numeroa.
    # Synthea-datassa ei ole puhelinnumeroita. Ei koskaan repoon.
    demo_puhelinnumero: str | None = None

    # Puhelun LLM: OpenAI-yhteensopiva rajapinta. Demossa paikallinen Ollama, tuotannossa
    # EU-hostattu malli pelkalla asetusmuutoksella. Ei kuluttaja-API:a (ks. SAANTELY.md).
    llm_base_url: str = "http://localhost:11434/v1"
    llm_malli: str = ""  # Ollaman mallin nimi, esim. tulos komennosta `ollama list`
    llm_api_avain: str = "ollama"  # Ollama ei tarkista; EU-palvelussa oikea avain .env:iin
    llm_aikakatkaisu_s: float = 30.0
    # Puhelun vuorossa LLM:lla on tama aika; Twilio odottaa webhookia ~15 s. Ylitys -> kiintea
    # kuittaus, puhelu jatkuu. Mitattu 11.10.: M2 + swap 3-14 s. Pidempi kuin Twilion 15 s
    # yhden pyynnön raja, koska välivastaus jakaa vuoron kahteen pyyntöön (ks. alla).
    llm_vuoron_aikaraja_s: float = 16.0
    # Välivastaus: ellei vuoro valmistu tässä ajassa, sanotaan "Hetkinen..." ja Twilio hakee
    # vastauksen uudelleen (<Redirect>), joka odottaa enintään puhelu_odotus_s. Yhteensä
    # aikaa ~19 s > llm_vuoron_aikaraja_s, joten malli ehtii aina valmiiksi tai varatoimintaan.
    puhelu_valivastaus_s: float = 6.0
    puhelu_odotus_s: float = 13.0

    # Puhelun tapa: "gather" (webhookit, puhe luetaan loppuun mutta ei kuunnella samalla) tai
    # "relay" (Twilio ConversationRelay, websocket: AI puhuu lauseen loppuun JA henkilön puhe
    # kuullaan samalla). Relay vaatii Twilio Consolessa ConversationRelayn käyttöönoton.
    puhelu_tapa: str = "gather"
    relay_tts: str = "ElevenLabs"  # Amazon | Google | ElevenLabs
    # Christoffer Weiss: suomalainen miesääni; nopeus 0,8, rauhallinen ja selkeä puhelu.
    relay_aani: str = "3OArekHEkHv5XvmZirVD-flash_v2_5-0.8_0.75_0.8"
    relay_stt: str = "Google"  # Google | Deepgram
    relay_stt_malli: str | None = None  # tyhjä = palveluntarjoajan oletus
    # Googlen oletusmalli (long) ei tue hints-vihjeitä suomeksi (Twilio 64101, 11.10.).
    relay_vihjeet: bool = False
    # Hiljaisuus (ms), jonka jälkeen puhe on valmis (600-5000). Ikäihmiset pitävät taukoja.
    relay_puheen_tauko_ms: int = 1500
    # Osittaiset tunnistukset (partialPrompts): relay näkee, milloin henkilö ALKOI puhua, eikä
    # lue kysymystä edeltänyttä puhetta vastaukseksi. Lisätty 11.10. ilta, EI vielä testattu
    # oikealla puhelulla: jos puhelu kaatuu Twilio-virheeseen (vrt. hints 64101), false ->
    # puheen alku arvioidaan tekstin pituudesta.
    relay_osittaiset: bool = True
    # Ollaman gemma "ajattelee" oletuksena (hidas). True lahettaa reasoning_effort="none".
    # Tuotannon EU-mallissa voi joutua kytkemaan pois, jos rajapinta ei tunne parametria.
    llm_ajattelu_pois: bool = True
    # Ollama: keep_alive=-1 jokaisessa pyynnössä pitää mallin ja välimuistin muistissa.
    # Oletus (5 min) pudotti mallin testisoittojen välissä: lämmitys ei ehtinyt ja
    # ensimmäinen vastaus ohitettiin (11.10.). Pyyntö ilman sitä palauttaa 5 min, joten
    # kaikki kutsut lähettävät sen. EU-mallissa pois, jos rajapinta ei tunne parametria.
    llm_pida_muistissa: bool = True
    # Puhelun lämpötila (Jere 0,6). Matalampi = johdonmukaisempi, vertaa arviointityökalulla
    # (scripts/arvioi_keskustelut.py).
    llm_lampotila: float = 0.6
    # Puheluvastaus luetaan ääneen ja on enintään muutama lause. Pieni raja estää mallia
    # jatkamasta turhaan sekä lyhentää pahimpia vuoroviiveitä 16 Gt:n demokoneella.
    llm_vastauksen_max_tokenit: int = 120
    # Kylmä 12B-malli voi 16 Gt:n koneella tarvita yli kaksi minuuttia ensimmäisen pitkän
    # järjestelmäohjeen käsittelyyn. Vuorojen aikaraja on erikseen paljon lyhyempi.
    llm_lammitys_aikaraja_s: float = 300.0
    # Puhelun jälkeisen analyysin ajattelu: tarkempi, mutta varaa mallin minuuteiksi (Gemma 12B
    # M2:lla puhelun vuorossa 121 s), jolloin seuraavan puhelun vuorot jonottavat. Pois oletuksena.
    llm_analyysi_ajattelu: bool = False
    # Lämmitysvahti (puhelu/lammitys.py): malli ja välimuisti lämpimäksi käynnistyksessä ja
    # tämän välein, kun puheluita ei ole. 0 = pois. 12.10.: Ollaman uudelleenkäynnistyksen
    # jälkeen kaksi puhelua alkoi kylmällä mallilla ja kulki varatoiminnalla.
    llm_lammitysvali_s: float = 60.0
    # Käynnistys odottaa, kunnes malli on lämmin, ja ajaa simuloidun puhelun (kylmänä pari
    # minuuttia). false = nopea käynnistys, esim. --reload-kehityksessä ilman soittoja.
    llm_lammita_kaynnistyksessa: bool = True

    # Twilio (puhelu). Arvot Consolesta .env:iin, ei koskaan repoon. None = soitto pois kaytosta.
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_numero: str | None = None  # Twilion numero (E.164), josta soitetaan
    # Julkinen https-osoite, jota Twilio kutsuu (devissa ngrok). Tarvitaan vasta webhookeihin.
    julkinen_url: str | None = None
    # Twilion <Say>-aani. Suomeksi vain: Polly.Suvi-Neural, Google.fi-FI-Wavenet-B,
    # Google.fi-FI-Standard-B (pelkka "Polly.Suvi" kaataa puhelun virheeseen 13520).
    puhe_aani: str = "Polly.Suvi-Neural"

    # Puheentunnistus (Twilion <Gather>). Vertaile kaikutestilla: POST /dev/puhelu/kaiku.
    # Malli: tyhja = Twilion oletus. Suomi vahvistettu: experimental_conversations,
    # experimental_utterances; kokeiltavia: googlev2_telephony, googlev2_chirp_3, deepgram_nova-3.
    stt_malli: str | None = None
    # Hiljaisuus (s), jonka jalkeen vastaus katsotaan valmiiksi. Iso = hidas puhuja ehtii.
    puheen_tauko_s: int = 2
    # Kauanko odotetaan vastauksen alkamista (s) ennen kuin vuoro on hiljainen.
    kuuntelun_odotus_s: int = 6
    # Alle taman varmuuden (0..1) vastaus pyydetaan toistamaan ja merkitaan epavarmaksi.
    matala_varmuus: float = 0.6
    # Lisavihjeet puheentunnistukselle pilkuilla eroteltuna (perussanasto: vihjesanat.json).
    stt_lisavihjeet: str = ""

    # Puhelun joustavuus. Kierros maksaa vain sekunteja puheluaikaa (~0,044 $/min); raja on
    # vanhuksen jaksamista varten. Kesto = Twilion time_limit: puhelu katkaistaan siihen.
    puhelu_max_kesto_s: int = 900

    # Voikko (suomen oikolukija) mallin puheen tarkistukseen: libvoikko-kirjaston hakemisto.
    # Macilla brew install libvoikko -> /opt/homebrew/lib. None = järjestelmän oletus.
    # Ilman Voikkoa tarkistetaan vain tyyli (sinuttelu), ks. kielentarkistus.py.
    voikko_kirjasto: str | None = "/opt/homebrew/lib"

    # Muisti: agentti kysyy edellisessä puhelussa kerrotusta oireesta, jos puhelu päättyi
    # enintään näin monta päivää sitten (säilytysraja, datan minimointi). 0 = pois.
    muisti_paivat: int = 30

    # VAIN DEV: puhelun vuorokohtainen loki (sisaltaa litteroinnin). Vaatii myos DEV_TILA=true.
    puhelu_loki: bool = False
    puhelu_lokikansio: str = str(_BACKEND.parent / "lokit" / "puhelut")


@lru_cache
def hae_asetukset() -> Settings:
    return Settings()
