import json
from datetime import UTC, datetime

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


def test_paattynyt_puhelu_tallentaa_yhteenvedon_ja_triage_paivityksen(
    sessiotehdas: sessionmaker[Session],
) -> None:
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
                konteksti={},
            )
        )
        sessio.commit()

    def tallenna(tulos: PuhelunTulos) -> None:
        with sessiotehdas() as sessio:
            tallenna_puhelun_tulos(sessio, tulos)

    istunnot = Istuntovarasto()
    triage = Triagetyontekija(FeikkiAnalysoija(), tallenna=tallenna)
    istunnot.aseta_triage(triage)
    istunto = Puheluistunto(
        istunto_id="istunto-demo",
        tehtava_id="tehtava-demo",
        kysymykset=[Kysymys(tunniste="vointi", teksti="Miten voit?")],
        konteksti=Puhelukonteksti(ika=80, aktiivisia_laakkeita=1, muutokset=[]),
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
    istunnot.lisaa(istunto)

    with sessiotehdas() as sessio:
        assert istunnot.paata(istunto, lambda tulos: tallenna_puhelun_tulos(sessio, tulos))
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
