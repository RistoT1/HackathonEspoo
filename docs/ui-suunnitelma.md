# Hoitajan näkymän visuaalinen suunnitelma

## Tavoite

Hoitaja näkee heti, mihin tapauksiin pitää reagoida. Näkymä painottaa kiireellisyyttä,
selkeää yhteenvetoa ja ihmisen tekemää päätöstä. AI:n arvio näytetään tukitietona, ei
automaattisena hoitopäätöksenä.

## Päänäkymä: takaisinsoittojono

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ Hyvinvointikumppani                         Hoitajan näkymä    [Kirjaudu] │
├──────────────────────────────────────────────────────────────────────────┤
│ Takaisinsoittojono                                                       │
│ Synteettinen demodata                                      Päivitä lista │
│                                                                          │
│ [Kaikki  8] [Soitettava tänään  3] [Käsitellyt  5]                       │
│                                                                          │
│ Kiireellisyys  Henkilö*  Syy / yhteenveto              Saapunut   Toiminto│
│ ──────────────────────────────────────────────────────────────────────── │
│ 🔴 Heti         Demo-014  Kertoi rintakivusta           12:41     [Avaa]  │
│ 🟡 Tänään       Demo-022  Uusi lääke; vointi muuttunut  12:35     [Avaa]  │
│ 🟡 Tänään       Demo-031  Ei ole aloittanut lääkettä    12:12     [Avaa]  │
│ 🟢 Seuranta     Demo-009  Ei uusia oireita              11:58     [Avaa]  │
│                                                                          │
│ *Synteettinen tunniste demossa; tuotannossa pseudonymisoitu tunniste.    │
└──────────────────────────────────────────────────────────────────────────┘
```

Kiireellisyys näytetään sekä värillä että tekstillä, jotta merkitys ei riipu värinäöstä.
Oletusjärjestys on kiireellisimmästä alkaen. Käyttäjä voi suodattaa listaa, mutta
suodattimen ei pidä piilottaa kiireellistä tapausta huomaamatta.

## Tapausnäkymä: yhteenveto ja hoitajan toimet

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ ← Takaisin jonoon                    Tapaus Demo-022                     │
├──────────────────────────────────────────────────────────────────────────┤
│ 🟡 HOITAJAN YHTEYDENOTTO TÄNÄÄN                                           │
│ AI:n ehdotus: keltainen · Sääntöpohjainen syy: voinnin muutos mainittu   │
│ AI tukee käsittelyä. Hoitaja arvioi tilanteen ja päättää jatkosta.       │
│                                                                          │
│ Laukaissut tapahtuma                                                     │
│ Uusi lääkemerkintä havaittu synteettisestä demodatasta                   │
│                                                                          │
│ Keskustelun yhteenveto                                                   │
│ “Aloitti lääkkeen. Kertoi voinnin muuttuneen; tarkempi selvitys puuttuu.”│
│                                                                          │
│ Hoitajan muistiinpano                                                    │
│ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │                                                                      │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│ [Kuittaa käsitellyksi]  [Merkitse takaisinsoitto tehdyksi]               │
└──────────────────────────────────────────────────────────────────────────┘
```

Näytä vain hoitajan työn kannalta tarpeellinen yhteenveto. Älä näytä raakaa puhelun
litterointia oletuksena äläkä tallenna ääntä. Jos litterointia tarvitaan demossa, käytä
vain synteettistä keskustelua ja rajaa näkyvyys selkeästi.

## Visuaalinen tyyli

- Rauhallinen, vaalea tausta ja korkean kontrastin teksti.
- Selkeä typografia ja riittävän suuret painikkeet.
- Kiireellisyys näkyy tekstinä, ikonina ja värinä.
- Punainen varataan välitöntä toimintaa vaativille tapauksille; keltainen hoitajan
  saman päivän yhteydenotolle; vihreä seurannalle.
- Selkeät tyhjät tilat: esimerkiksi “Ei avoimia takaisinsoittoja”.
- Virhetilat näkyvät ymmärrettävänä ilmoituksena, eivät tyhjänä listana.
- Tietosuojan kannalta tarpeettomia nimiä, puhelinnumeroita tai henkilötunnuksia ei
  näytetä listassa.

## Demossa näkyvä toiminta

1. Synteettinen lääkemuutostapahtuma tuottaa uuden jonorivin.
2. Rivi näkyy keltaisena ja kiireellisyys on myös kirjoitettu auki.
3. Hoitaja avaa tapauksen ja lukee yhteenvedon sekä AI:n ehdotuksen perusteluineen.
4. Hoitaja tekee itse jatkopäätöksen ja kuittaa tapauksen.

## MVP:n ulkopuolelle

Kirjautuminen/MFA, roolipohjaiset oikeudet ja katselujen auditointi kuuluvat tuotannon
vaatimuksiin. Demossa voidaan käyttää paikallista synteettistä dataa, mutta käyttöliittymää
ei pidä kuvata tuotantovalmiiksi ilman näitä kontrolleja. Trendikaaviot, monimutkaiset
suodattimet ja reaaliaikainen puhelun seuranta voidaan jättää myöhemmäksi.

## Esimerkkikansiorakenne

Käyttöliittymä tehdään Python-backendin yhteydessä: FastAPI palvelee Jinja2-pohjilla
muodostettuja HTML-sivuja. Erillistä Vue- tai Node-frontendia ei tarvita.

```text
backend/
└── app/
    ├── main.py
    ├── hoitaja/
    │   ├── reitit.py
    │   └── palvelu.py
    ├── templates/
    │   ├── perus.html
    │   ├── takaisinsoittojono.html
    │   └── tapauksen_tiedot.html
    └── static/
        ├── tyyli.css
        └── hoitaja.js
```

- `hoitaja/reitit.py` käsittelee sivupyynnöt ja palauttaa HTML-pohjat.
- `hoitaja/palvelu.py` hakee ja muokkaa näkymässä näytettävää tietoa.
- `templates/` sisältää jonon ja tapauksen HTML-pohjat; yhteinen rakenne on `perus.html`:ssä.
- `static/tyyli.css` sisältää näkymän tyylit ja `hoitaja.js` vain tarvittavan selainlogiikan.
- Lomakkeiden lähetys voi käyttää FastAPI-reittejä; erillistä API-asiakasta tai frontend-tilanhallintaa
  ei tarvita MVP:ssä.

Pienen demon voi aloittaa yhdellä jonon HTML-sivulla ja lisätä tapausnäkymän vasta, kun
jonon perustason toiminta on kunnossa.
