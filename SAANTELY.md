# Sääntely, tietoturva ja arviointi

Tämä on projektin vastuullisuus- ja sääntelyperusta. CLAUDE.md viittaa tähän ("toimi näin").
Käytä tätä myös Junction-palautuksen itsearvioinnin pohjana.

---

## Arviointikriteerit ja painotukset

Kaikki tiimit arvioidaan asteikolla 1–5. Palkinto edellyttää painotettua keskiarvoa ≥ 3,0.
Tasapelin ratkaisee vaikuttavuus. Esitystapa/pitchin laatu EI ole kriteeri, se vaikuttaa vain
siihen, miten hyvin saatte arvioitavat asiat näkyviin.

| Kriteeri | Paino | Ydinkysymys | Miten osumme |
|---|---|---|---|
| Vaikuttavuus | 30 % | Ratkaiseeko oikeasti haasteen ongelman, kuinka suuri hyöty? | Reaktiivisesta ennakoivaan: data laukaisee yhteydenoton ennen kriisiä. Kustannuslaskelma (säästyvät hoitajan soitot, vältetyt päivystyskäynnit). |
| Toteutettavuus | 25 % | Onko demo toimiva ja uskottava, voiko jatkokehittää? | Toimiva puhelu lavalla päästä päähän. FHIR/Kanta-yhteensopiva adapteri. Realistinen tuotantopolku. |
| Tekoälyn hyödyntäminen | 15 % | Onko tekoäly aidosti tarpeellinen osa? | LLM tarpeen vapaaseen suomenkieliseen keskusteluun, mihin kiinteä valikkopuhelin ei taivu. LLM vain siellä missä tarpeen, muu deterministinen. |
| Vastuullisuus | 15 % | Tietosuoja, saavutettavuus, luotettavuus, ihmisen rooli? | Tämä dokumentti. Ihminen päättää, AI kertoo olevansa AI, sääntöpohjaiset punaiset liput, EU-hosting, datan minimointi. |
| Innovatiivisuus | 15 % | Uusi näkökulma vs. ilmeiset ratkaisut? | Datalähtöinen laukaisu (ei ajastettu, ei hoitajan käynnistämä) + hiljaiset signaalit. |

Vaikuttavuus + toteutettavuus = 55 %: toimiva demo ja todellinen hyöty ratkaisevat. Suuntaa
viimeiset tunnit sinne.

---

## Lähtökohta

Hackathonissa käsitellään VAIN synteettistä dataa, ei oikeiden ihmisten tietoja. Siksi GDPR ei
tiukasti sido itse tapahtumassa. Arviointi koskee sitä, onko arkkitehtuuri uskottavasti
tuotantokelpoinen. Älä väitä olevasi compliant — osoita, että valinnat tekevät tuotannon
mahdolliseksi. Tunnista heikkoudet rehellisesti; se tekee sote-tuomaristoon paremman vaikutuksen
kuin täydellisyyden esittäminen.

---

## Työkalukohtainen analyysi — toimi näin

### FastAPI / Vue / Uvicorn / SQLite / Postgres — GDPR-neutraaleja
Avoimen lähdekoodin kehyksiä omassa ympäristössä; mikään data ei vuoda kolmannelle vain näitä
käyttämällä. Compliance riippuu deploymentista: EU-alue, salaus, pääsynhallinta. Tuotannon
Postgres = Azure Database for PostgreSQL, Sweden Central.

### Azure Speech + Azure OpenAI — tämä on vahvuus, korosta sitä
- Microsoft on käsittelijä (processor), tarjoaa DPA:n.
- Asiakkaan dataa EI käytetä perustusmallien koulutukseen.
- EU-datasijainti Azure OpenAI EU Data Zone -deploymentilla (EU:n sisällä) / EU-dataraja.
- Väärinkäytösvalvonnan prompteja säilytetään 30 pv; säätyisille työkuormille voi hakea
  nollasäilytyksen poikkeusta — hae tämä terveysdatalle.
- Azure OpenAI on HIPAA-kelpoinen palvelu (kertoo terveyssektorin kypsyydestä).
- TOIMI NÄIN: Speech + OpenAI Sweden Centralissa, DPA, nollasäilytyspyyntö.

### Twilio — heikko lenkki, älä piilota sitä
- Yhdysvaltalainen käsittelijä. Vaatii DPA:n; EU-US DPF + SCC:t siirtoihin.
- EU-datasijainti mahdollinen (Voice Irlannin datakeskukseen).
- MUTTA: PSTN-puhelu ei ole päästä päähän salattu, ja US-käsittelijä terveyskontekstissa on aina
  keskustelunaihe.
- EU-natiivit vaihtoehdot tuotantoon: 46elks (SE), Sinch (SE), Telnyx (EU-alue).
- TOIMI NÄIN: hackathonissa Twilio (nopeus); kerro pitchissä että tuotannossa harkitsisitte
  EU-natiivia operaattoria. Puhelussa ei henkilötunnusta/diagnooseja, ei äänitallenteita.

### Runtime-LLM — vain Azure OpenAI EU Data Zonen kautta
Keskustelua käyvä LLM käsittelee terveyssisältöä → EU + DPA. ÄLÄ reititä kuluttaja-OpenAI:n,
suoran Geminin tai suoran Clauden kautta. Kehitystyökalut (Claude Code, Codex, Gemini) käsittelevät
koodia ja synteettistä dataa → ne ovat kunnossa.

---

## GDPR — rakenteelliset vaatimukset (tuotanto)

- Käsittelyperuste: hyvinvointialueella lakisääteinen tehtävä + terveydenhuolto (art. 6(1)(e) ja
  9(2)(h)); terveystiedot ovat erityisiä henkilötietoja (art. 9).
- DPIA (art. 35) pakollinen: laaja terveysdata, haavoittuva ryhmä, automaattinen käsittely.
- Datan minimointi (art. 5): vain terveyden kannalta olennainen yhteenveto, ei rupattelua.
- Käsittelijäsopimukset (art. 28): Twilio, Microsoft, LLM-toimittaja.
- Siirrot (art. 44–49): pysy EU:ssa; jos US-käsittelijä jää, SCC + DPF.
- Rekisteröidyn oikeudet, läpinäkyvyys (art. 13–14), tietoturva (art. 32).

---

## Tietoturva — toimi näin

- TLS kaikessa; Twilion websocket PAKKO olla `wss`, ei `ws`.
- Pseudonymisoi: erota henkilöllisyys (nimi, puhelinnumero) terveystiedosta, linkitä tunnuksella.
- Hoitajan näkymä: MFA + roolipohjainen pääsy; jokainen katselu audit-lokiin.
- Salaisuudet tuotannossa Azure Key Vaultiin; devissä `.env` (gitignore). Ei repoon.
- Minimoi PII LLM-prompteissa: älä lähetä nimeä/henkilötunnusta mallille, vain mitä kysymys vaatii.
- Ei äänitallenteiden säilytystä.

---

## AI Act — päivitetty aikataulu (Digital Omnibus)

Aikataulu MUUTTUI; moni lähde on vanhentunut:
- Kielletyt käytännöt voimassa 2.2.2025.
- 2.8.2026 voimaan: läpinäkyvyysvelvoite, viranomaisvalvonta, AI-lukutaidon kehys.
- Annex III -itsenäiset korkean riskin järjestelmät: siirtyi 2.12.2027:ään.
- Lääkinnällisiin laitteisiin upotettu korkean riskin AI (art. 6(1)): siirtyi 2.8.2028:aan.

Mitä tämä tarkoittaa:
- **Läpinäkyvyys (art. 50) koskee jo nyt**: puhelun alussa kerrotaan että soittaja on tekoäly.
  Sisäänrakennettu.
- Korkean riskin velvoitteet osuvat vasta tuotteistuksessa lääkinnälliseksi laitteeksi, ja silloin
  2028. Lääketieteellinen AI on korkean riskin, kun se on MDR-laite ja vaatii ilmoitetun laitoksen
  arvioinnin (luokka IIa+).
- Suunnittele korkean riskin vaatimusten mukaisesti; tee luokitteluargumentti arkkitehtuurin
  vahvuudesta.
- Haavoittuva ryhmä (vanhukset): art. 5 kieltää haavoittuvuuksien hyväksikäytön.
- Jos mielialan/yksinäisyyden seuranta: tunteiden tunnistuksella on AI Actissa rajoituksia, joissa
  terveydenhoidollinen peruste on poikkeus.
- Ihmisen valvonta (art. 14): hoitaja päättää — sisäänrakennettu.

---

## MDR (terveysteknologian tuotesääntely)

Oireiden vakavuutta arvioiva ja hoitoa priorisoiva ohjelmisto on todennäköisesti lääkinnällinen
laite; sääntö 11 → vähintään luokka IIa. Valvoja Suomessa: Fimea. Hackathonin POC:tä ei saateta
markkinoille → ei vielä laite. Kuvaa tuotteistuspolku: käyttötarkoitus, kliininen arviointi,
ilmoitetun laitoksen arviointi.

---

## EHDS

FHIR-pohjaisuus ja yhteentoimivuus Kannan kanssa ovat oikea suunta. Kansalaisen oikeus omiin
tietoihinsa huomioitava. Asetus etenee vaiheittain vuodesta 2027.

---

## Junction-palautuksen itsearviointi — vastuullisuus (max 200 sanaa)

Runko: ihminen päättää (hoitaja tekee hoitopäätöksen, AI on päätöksen tuki) · läpinäkyvyys (AI
kertoo olevansa AI, AI Act art. 50) · tietosuoja (vain synteettinen data, datan minimointi,
EU-hosting, pseudonymisointi) · saavutettavuus (tavallinen puhelu ilman älypuhelinta/sovellusta,
hidas selkokielinen puhe) · rehelliset heikkoudet (PSTN ei E2E-salattu → ei henkilötunnuksia
puhelussa; US-käsittelijä Twilio → tuotannossa EU-natiivi operaattori; korkean riskin AI Act -
velvoitteet osuisivat tuotteistuksessa 2028).
