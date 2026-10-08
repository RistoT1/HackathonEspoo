# Hoitajan näkymän suunnitelma

## Tavoite

Hoitaja näkee backendin muodostamat soittotehtävät, niiden järjestysprioriteetin,
tunnistetut lääkemuutokset ja kysymysrungon. Soittoprioriteetti on soittojonon
järjestämistä varten; se ei ole triage- tai kiireellisyysluokitus.

## Päänäkymä: soittojono

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ Soittotehtävät                                      DEMOYMÄRISTÖ         │
│ Tunnistetut lääkemuutokset ja niihin liittyvät tehtävät.                 │
│                                                                         │
│ Avoimet tehtävät     Odottaa soittoa       Päätetyt tehtävät             │
│        3                    2                      1                    │
│                                                                         │
│ Soittojono                                      [Tilan suodatus]        │
│ Järjestetty soittojärjestyksen mukaan                                    │
│ Prioriteetti  Pseudonyymi  Muutos             Luotu       Tila    Avaa  │
│ ──────────────────────────────────────────────────────────────────────  │
│       5       demo-01     UUSI LAAKE         08.10 08:15  Odottaa  →   │
│       3       demo-02     ANNOSMUUTOS        08.10 08:10  Puhelu   →   │
└─────────────────────────────────────────────────────────────────────────┘
```

Prioriteetin pistemäärä näytetään neutraalisti ja sen selite kertoo, ettei kyse ole
kiireellisyysarviosta. Tila näytetään värin lisäksi tekstinä. Lista ja suodatus käyttävät
`GET /puhelutehtavat` -rajapinnan tietoja.

## Tehtävänäkymä

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ Soittojono / a1b2c3d4                   Tila: Odottaa soittoa            │
│ Pseudonyymi tunniste: demo-01                                          │
│                                                                         │
│ Soittoprioriteetti 5 · 1 aktiivinen lääke · 0 soittoyritystä            │
│                                                                         │
│ MIKSI TEHTÄVÄ ON JONOSSA         TUNNISTETUT MUUTOKSET                  │
│ Ikä ja tunnistettu muutos         Lääke X · uusi lääkemerkintä            │
│                                  2 päivää sitten                        │
│                                                                         │
│ PUHELUN KYSYMYSRUNKO                                                    │
│ 01  Oletko aloittanut lääkkeen?                                         │
│ 02  Miten vointisi on muuttunut?                                        │
│                                                                         │
│ Vastauksia tai litterointia API ei tällä hetkellä palauta.              │
│                                                                         │
│ [Peru tehtävä]                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

Tehtävänäkymä käyttää `GET /puhelutehtavat/{tehtava_id}` -tietoja. Sallitut tilatoiminnot
määräytyvät backendin tilasiirtymäsääntöjen mukaan ja päivittyvät
`PATCH /puhelutehtavat/{tehtava_id}` -rajapinnan kautta. Puhelun vastauksia tai
litterointia ei esitetä, koska nykyinen API ei niitä palauta.

## Visuaalinen tyyli

- Rauhallinen, vaalea tausta ja korkean kontrastin teksti.
- Selkeä typografia ja riittävän suuret painikkeet.
- Tehtävän tila näkyy tekstinä ja värinä.
- Prioriteettipisteet esitetään neutraalisti; niitä ei nimetä kiireellisyydeksi.
- Selkeät tyhjät tilat: esimerkiksi “Ei avoimia takaisinsoittoja”.
- Virhetilat näkyvät ymmärrettävänä ilmoituksena, eivät tyhjänä listana.
- Tietosuojan kannalta tarpeettomia nimiä, puhelinnumeroita tai henkilötunnuksia ei
  näytetä listassa.

## Demossa näkyvä toiminta

1. Synteettinen lääkemuutostapahtuma luo soittotehtävän backendin jonoon.
2. Tehtävä näkyy pseudonyymillä, prioriteetilla, muutoksilla ja tilalla.
3. Hoitaja avaa tehtävän ja tarkastelee muutoksen tietoja sekä kysymysrunkoa.
4. Hoitaja tekee vain backendin sallimat tilapäivitykset.

## MVP:n ulkopuolelle

Puheluvastausten tallennus ja näyttäminen eivät kuulu nykyiseen API-sopimukseen.
Kirjautuminen/MFA, roolipohjaiset oikeudet ja katselujen auditointi kuuluvat tuotannon
vaatimuksiin; käyttöliittymää ei pidä kuvata tuotantovalmiiksi ilman näitä kontrolleja.
Trendikaaviot ja reaaliaikainen puhelun seuranta voidaan jättää myöhemmäksi.

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
        ├── hoitaja.css
        └── hoitaja.js
```

- `hoitaja/reitit.py` käsittelee sivupyynnöt ja palauttaa HTML-pohjat.
- `hoitaja/palvelu.py` hakee ja muokkaa näkymässä näytettävää tietoa.
- `templates/` sisältää jonon ja tapauksen HTML-pohjat; yhteinen rakenne on `perus.html`:ssä.
- `static/hoitaja.css` sisältää näkymän tyylit ja `hoitaja.js` tilapäivityksen selainlogiikan.
- Lomakkeiden lähetys voi käyttää FastAPI-reittejä; erillistä API-asiakasta tai frontend-tilanhallintaa
  ei tarvita MVP:ssä.

Pienen demon voi aloittaa yhdellä jonon HTML-sivulla ja lisätä tapausnäkymän vasta, kun
jonon perustason toiminta on kunnossa.
