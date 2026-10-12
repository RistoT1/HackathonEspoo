"""VAIN DEV: puhelun tarkkuuden testipankki (synteettiset keskustelut ja vastaukset).

oireet.py        oirekirjasto: miten vanhus kertoo oireesta, kyllä/ei ja faktat
persoonat.py     puhetyylit, tapahtumat ja keskustelupohjien (persoonien) generointi
simulaattori.py  improvisoi vanhuksen vastauksen botin kysymykseen persoonan faktoista
pankki.py        generoi pankki.json: keskustelupohjat + yksittäisten vastausten pankki
tarkistus.py     automaattiset tarkistukset (sinuttelu, lista, oireen tarkennus, hätä ...)
aja.py           ajaa pankin oikeaa mallia vasten, raportti lokit/testipankki/

Kaikki data on synteettistä. Ei aitoja henkilötietoja.
"""
