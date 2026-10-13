"""LLM-agentti feikatulla HTTP:lla: pyynnön muoto, vastaus tekstinä ja virheet."""

import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.puhelu.agentti import KiinteaAgentti, LlmAgentti, hae_agentti

VIESTIT = [
    {"role": "system", "content": "Ohje"},
    {"role": "assistant", "content": "Miten menee?"},
    {"role": "user", "content": "Hyvin."},
]


def agentti(sisalto: str, pyynnot: list | None = None, tila: int = 200) -> LlmAgentti:
    def kasittele(pyynto: httpx.Request) -> httpx.Response:
        if pyynnot is not None:
            pyynnot.append(pyynto)
        runko = {"choices": [{"message": {"role": "assistant", "content": sisalto}}]}
        return httpx.Response(tila, json=runko)

    return LlmAgentti(
        base_url="http://llm.test/v1",
        malli="testimalli",
        api_avain="avain",
        aikaraja_s=5,
        ajattelu_pois=True,
        transport=httpx.MockTransport(kasittele),
    )


def test_pyynto_on_openai_muotoinen_ja_vastaus_tekstia():
    pyynnot: list[httpx.Request] = []
    llm = agentti("Mukava kuulla.", pyynnot)

    vastaus = asyncio.run(llm.vastaa(VIESTIT))

    runko = json.loads(pyynnot[0].content)
    assert vastaus == "Mukava kuulla."
    assert pyynnot[0].url == "http://llm.test/v1/chat/completions"
    assert pyynnot[0].headers["Authorization"] == "Bearer avain"
    assert runko["model"] == "testimalli"
    assert runko["messages"] == VIESTIT
    assert runko["reasoning_effort"] == "none"
    assert runko["max_tokens"] == 120


def test_lammitys_lahettaa_jarjestelmaohjeen():
    pyynnot: list[httpx.Request] = []

    alku = [
        {"role": "system", "content": "Järjestelmäohje"},
        {"role": "assistant", "content": "Avaus"},
    ]
    asyncio.run(agentti("", pyynnot).lammita(alku))

    runko = json.loads(pyynnot[0].content)
    assert runko["messages"] == alku  # sama alku kuin ensimmäinen vuoro (välimuisti)
    assert runko["max_tokens"] == 1


def test_puhelun_lammitysvirhe_ei_kaada_puhelua():
    alku = VIESTIT[:2]

    asyncio.run(agentti("", tila=500).lammita(alku))


def test_mitattu_lammitys_palauttaa_keston_ja_nostaa_virheen():
    kesto = asyncio.run(agentti("").lammita_ja_mittaa(VIESTIT[:2]))

    assert kesto >= 0
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(agentti("", tila=500).lammita_ja_mittaa(VIESTIT[:2]))


def test_http_virhe_nostetaan_tilakoneelle():
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(agentti("", tila=500).vastaa(VIESTIT))


def test_kiintea_agentti_ei_vastaa():
    assert asyncio.run(KiinteaAgentti().vastaa(VIESTIT)) is None


def test_agentti_valitaan_asetuksista():
    ilman = hae_agentti(Settings(_env_file=None, database_url="sqlite://"))
    kanssa = hae_agentti(Settings(_env_file=None, database_url="sqlite://", llm_malli="poro"))

    assert isinstance(ilman, KiinteaAgentti)
    assert isinstance(kanssa, LlmAgentti)
