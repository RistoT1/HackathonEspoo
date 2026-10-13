import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.mallit import (
    Kiireellisyys,
    KysymyksenVastaus,
    Kysymys,
    Puhelukonteksti,
    PuhelunLoppu,
    PuhelunTulos,
    Saantoloydokset,
    Yhteenveto,
)
from app.puhelu.istunnot import Istuntovarasto
from app.puhelu.keskustelu import Puheluistunto
from app.puhelu.palvelu import kasittele_tila
from app.tietokanta import (
    PuhelunTulosRivi,
    PuhelutehtavaRivi,
    tallenna_puhelun_tulos,
)
from app.triage.tyontekija import Triagetyontekija


class FeikkiAnalysoija:
    def analysoi(self, litterointi: str, saanto: Saantoloydokset) -> Yhteenveto:
        assert "Minulla on vatsakipu" in litterointi
        return Yhteenveto(
            taso=Kiireellisyys.KELTAINEN,
            saanto=saanto,
            mallin_taso=Kiireellisyys.KELTAINEN,
            analyysi_valmis=True,
            yleisvointi="Vointi on hieman huono.",
        )


KONTEKSTI = Puhelukonteksti(ika=80, aktiivisia_laakkeita=1, muutokset=[])


def lisaa_soitettava_tehtava(sessiotehdas: sessionmaker[Session]) -> None:
    with sessiotehdas() as sessio:
        sessio.add(
            PuhelutehtavaRivi(
                id="tehtava-demo",
                vanhus_id="vanhus-demo",
                tila="SOITETAAN",
                prioriteetti=0,
                perusteet=[],
                ika=80,
                aktiivisia_laakkeita=1,
                viitepaiva=datetime.now(UTC),
                yritykset=1,
                kysymykset=[],
                konteksti=KONTEKSTI.model_dump(mode="json"),
            )
        )
        sessio.commit()


def kantaan(sessiotehdas: sessionmaker[Session]) -> Callable[[PuhelunTulos], None]:
    """Kuten main._tallenna_tulos_kantaan, mutta testin SQLite-kantaan."""

    def tallenna(tulos: PuhelunTulos) -> None:
        with sessiotehdas() as sessio:
            tallenna_puhelun_tulos(sessio, tulos)

    return tallenna


def paattynyt_istunto() -> Puheluistunto:
    istunto = Puheluistunto(
        istunto_id="istunto-demo",
        tehtava_id="tehtava-demo",
        kysymykset=[Kysymys(tunniste="vointi", teksti="Miten voit?")],
        konteksti=KONTEKSTI,
        paivita_jono=True,
    )
    istunto.loppu = PuhelunLoppu.VALMIS
    istunto.paattyi = datetime.now(UTC)
    istunto.vastaukset.append(
        KysymyksenVastaus(
            tunniste="vointi",
            kysymys="Miten voit?",
            vastaus="Minulla on vatsakipu",
        )
    )
    istunto.historia.append({"role": "user", "content": "Minulla on vatsakipu"})
    return istunto


class KaatuvaTallennus:
    """Kanta ei käytettävissä ensimmäisellä kerralla."""

    def __init__(self) -> None:
        self.kaatuu = True
        self.tallennetut: list[PuhelunTulos] = []

    def __call__(self, tulos: PuhelunTulos) -> None:
        if self.kaatuu:
            raise ConnectionError("kanta ei vastaa")
        self.tallennetut.append(tulos)


class KirjaavaTriage:
    def __init__(self) -> None:
        self.lisatyt: list[str] = []

    def lisaa(self, tulos: PuhelunTulos, litterointi: str) -> None:
        self.lisatyt.append(tulos.istunto_id)


def test_kaatunut_tallennus_jattaa_puhelun_kayntiin_uudelleenyritysta_varten():
    tallennus = KaatuvaTallennus()
    triage = KirjaavaTriage()
    istunnot = Istuntovarasto()
    istunnot.aseta_tallennus(tallennus)
    istunnot.aseta_triage(triage)
    istunto = paattynyt_istunto()
    istunnot.lisaa(istunto)

    with pytest.raises(ConnectionError):
        istunnot.paata(istunto)

    # Ei julkaistu: istunto yhä käynnissä, ei tulosta muistissa eikä triagelle.
    assert istunnot.hae("istunto-demo") is istunto
    assert istunnot.puheluita_kaynnissa()
    assert istunnot.tulokset() == []
    assert triage.lisatyt == []

    tallennus.kaatuu = False
    assert istunnot.paata(istunto)
    assert not istunnot.paata(istunto)  # jo päätetty: ei toista tallennusta

    assert [tulos.istunto_id for tulos in tallennus.tallennetut] == ["istunto-demo"]
    assert istunnot.hae("istunto-demo") is None
    assert [tulos.istunto_id for tulos in istunnot.tulokset()] == ["istunto-demo"]
    assert triage.lisatyt == ["istunto-demo"]


def test_ilman_tallennusta_tulos_jaa_vain_muistiin():
    """Testit ja dev ilman käynnistystä (main.elinkaari asettaa kantaan tallennuksen)."""
    istunnot = Istuntovarasto()
    istunto = paattynyt_istunto()
    istunnot.lisaa(istunto)

    assert istunnot.paata(istunto)
    assert [tulos.loppu for tulos in istunnot.tulokset()] == [PuhelunLoppu.VALMIS]


def test_paattynyt_puhelu_tallentaa_yhteenvedon_ja_triage_paivityksen(
    sessiotehdas: sessionmaker[Session],
) -> None:
    lisaa_soitettava_tehtava(sessiotehdas)
    istunnot = Istuntovarasto()
    istunnot.aseta_tallennus(kantaan(sessiotehdas))
    triage = Triagetyontekija(FeikkiAnalysoija(), istunnot.puheluita_kaynnissa, istunnot.tallenna)
    istunnot.aseta_triage(triage)
    istunto = paattynyt_istunto()
    istunnot.lisaa(istunto)

    assert istunnot.paata(istunto)
    triage.odota_tyhjaksi()

    with sessiotehdas() as sessio:
        tallennettu = sessio.get(PuhelunTulosRivi, "istunto-demo")
        assert tallennettu is not None
        assert tallennettu.tehtava_id == "tehtava-demo"
        assert tallennettu.loppu == PuhelunLoppu.VALMIS.value
        assert tallennettu.yhteenveto["analyysi_valmis"] is True
        assert tallennettu.yhteenveto["yleisvointi"] == "Vointi on hieman huono."
        tallennettu_json = json.dumps(tallennettu.yhteenveto, ensure_ascii=False)
        assert "Minulla on vatsakipu" not in tallennettu_json
        assert "vastaukset" not in tallennettu.yhteenveto


def test_twilion_lopputila_tallentaa_asetetulla_tallennuksella_ja_paivittaa_jonon(
    sessiotehdas: sessionmaker[Session],
) -> None:
    """palvelu._paata: tulos kantaan käynnistyksessä asetetulla tallennuksella, jonon tila
    pyynnön sessiolla."""
    lisaa_soitettava_tehtava(sessiotehdas)
    istunnot = Istuntovarasto()
    istunnot.aseta_tallennus(kantaan(sessiotehdas))
    istunto = paattynyt_istunto()
    istunnot.lisaa(istunto)

    with sessiotehdas() as sessio:
        kasittele_tila("istunto-demo", "completed", istunnot, sessio)
        kasittele_tila("istunto-demo", "completed", istunnot, sessio)  # myöhästynyt kutsu

    with sessiotehdas() as sessio:
        tallennettu = sessio.get(PuhelunTulosRivi, "istunto-demo")
        tehtava = sessio.get(PuhelutehtavaRivi, "tehtava-demo")
        assert tallennettu is not None
        assert tallennettu.loppu == PuhelunLoppu.VALMIS.value
        assert tallennettu.yhteenveto["taso"] == istunto.tulos().yhteenveto.taso.value
        assert tehtava.tila == "VALMIS"
    assert not istunnot.puheluita_kaynnissa()
