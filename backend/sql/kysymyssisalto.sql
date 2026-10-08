-- Sisallon muokkaus: kysymykset ja laakeryhmat ATC-koodeineen (Postgres, esim. pgAdmin).
-- Aja vain tarvitsemasi lause; koko tiedoston ajo tekee myos esimerkkimuutokset.
--
-- Taulut luo ja alkusisallon lisaa Alembic-migraatio (ajetaan automaattisesti, kun backend
-- kaynnistyy, tai kasin: cd backend && uv run alembic upgrade head).
-- Muutokset vaikuttavat UUSIIN soittotehtaviin seuraavassa tunnistuksessa. Jo jonossa olevien
-- tehtavien kysymykset eivat muutu (rivi kertoo mita sille generoitiin).
--
-- Saannot teksteille: ei laakkeen nimea, sairautta eika epailtya haittavaikutusta (puhelu ei
-- ole salattu, ei johdattelua). Yksi asia kerrallaan, selkokielella.
-- Poisto kaytosta: on_aktiivinen = false (ei DELETE), jotta historia sailyy.


-- ===== Katselu =====

SELECT * FROM muutoskysymys ORDER BY muutos_tyyppi;
SELECT * FROM tarkennuskysymys ORDER BY aihe;
SELECT * FROM yleiskysymys ORDER BY jarjestys;

-- Laakeryhmat ATC-etuliitteineen, avainsanoineen ja tarkennuksineen
SELECT r.jarjestys, r.nimi, r.korkea_riski, r.on_aktiivinen,
       (SELECT string_agg(x.atc_etuliite, ', ' ORDER BY x.atc_etuliite)
          FROM laakeryhman_atc x WHERE x.laakeryhma = r.nimi) AS atc,
       (SELECT string_agg(a.avainsana, ', ' ORDER BY a.avainsana)
          FROM laakeryhman_avainsana a WHERE a.laakeryhma = r.nimi) AS avainsanat,
       (SELECT string_agg(t.aihe, ', ' ORDER BY t.jarjestys)
          FROM laakeryhman_tarkennus t WHERE t.laakeryhma = r.nimi) AS tarkennukset
  FROM laakeryhma r
 ORDER BY r.jarjestys;


-- ===== Kysymystekstin muokkaus =====

UPDATE muutoskysymys
   SET teksti = 'Tietojemme mukaan sinulle on merkitty uusi lääke. Miten sen kanssa on mennyt?'
 WHERE muutos_tyyppi = 'UUSI_LAAKE';

UPDATE tarkennuskysymys
   SET teksti = 'Onko sinua huimannut viime päivinä?'
 WHERE aihe = 'huimaus';


-- ===== Uusi tarkennuskysymys ja sen liittaminen laakeryhmaan =====

INSERT INTO tarkennuskysymys (aihe, teksti)
VALUES ('suun_kuivuminen', 'Onko suusi tuntunut tavallista kuivemmalta?')
ON CONFLICT (aihe) DO UPDATE SET teksti = EXCLUDED.teksti, on_aktiivinen = true;

-- jarjestys: pienin kysytaan ensin (puhelussa enintaan 2 tarkennusta)
INSERT INTO laakeryhman_tarkennus (laakeryhma, aihe, jarjestys)
VALUES ('opioidit', 'suun_kuivuminen', 40)
ON CONFLICT (laakeryhma, aihe) DO UPDATE SET jarjestys = EXCLUDED.jarjestys;


-- ===== Uusi laakeryhma =====
-- Ryhma tunnistetaan ENSISIJAISESTI ATC-koodista (laakeryhman_atc): pisin osuva etuliite
-- voittaa (esim. 'C01AA' sydanglykosidit ennen 'C01' sydanlaakkeet). Jos laakkeen ATC on
-- tiedossa mutta mikaan etuliite ei osu, ryhmaa ei ole.
-- Avainsanat (laakeryhman_avainsana) ovat varakeino VAIN kun ATC puuttuu: verrataan
-- laakkeen ainesosaan pienilla kirjaimilla, ja jarjestys (pienin ensin) ratkaisee.
-- korkea_riski nostaa soittojarjestysta (ei triage).

INSERT INTO laakeryhma (nimi, korkea_riski, jarjestys)
VALUES ('parkinsonlääkkeet', false, 210)
ON CONFLICT (nimi) DO UPDATE
   SET korkea_riski = EXCLUDED.korkea_riski, jarjestys = EXCLUDED.jarjestys, on_aktiivinen = true;

INSERT INTO laakeryhman_atc (atc_etuliite, laakeryhma)
VALUES ('N04', 'parkinsonlääkkeet')
ON CONFLICT (atc_etuliite) DO UPDATE SET laakeryhma = EXCLUDED.laakeryhma;

INSERT INTO laakeryhman_avainsana (avainsana, laakeryhma) VALUES
  ('levodopa', 'parkinsonlääkkeet'),
  ('pramipexole', 'parkinsonlääkkeet')
ON CONFLICT (avainsana) DO UPDATE SET laakeryhma = EXCLUDED.laakeryhma;

INSERT INTO laakeryhman_tarkennus (laakeryhma, aihe, jarjestys) VALUES
  ('parkinsonlääkkeet', 'huimaus', 10),
  ('parkinsonlääkkeet', 'sekavuus', 20)
ON CONFLICT (laakeryhma, aihe) DO UPDATE SET jarjestys = EXCLUDED.jarjestys;


-- ===== ATC-etuliite olemassa olevaan ryhmaan tai siirto toiseen ryhmaan =====
-- Tarkempi etuliite ohittaa yleisemman: esim. tulehduskipulaakkeet korkeaksi riskiksi
-- iakkaille omana ryhmanaan, tai yksittainen aine (7 merkkia) omaan ryhmaansa.

INSERT INTO laakeryhman_atc (atc_etuliite, laakeryhma)
VALUES ('B01AE', 'verenohennus')
ON CONFLICT (atc_etuliite) DO UPDATE SET laakeryhma = EXCLUDED.laakeryhma;


-- ===== Avainsana olemassa olevaan ryhmaan =====

INSERT INTO laakeryhman_avainsana (avainsana, laakeryhma)
VALUES ('edoxaban', 'verenohennus')
ON CONFLICT (avainsana) DO UPDATE SET laakeryhma = EXCLUDED.laakeryhma;


-- ===== Yleiskysymys (kysytaan joka puhelussa muutosten jalkeen) =====

INSERT INTO yleiskysymys (tunniste, teksti, jarjestys)
VALUES ('arki', 'Oletko pärjännyt arjen askareissa niin kuin ennenkin?', 20)
ON CONFLICT (tunniste) DO UPDATE SET teksti = EXCLUDED.teksti, jarjestys = EXCLUDED.jarjestys;


-- ===== Kaytosta poisto ja palautus =====

UPDATE laakeryhma SET on_aktiivinen = false WHERE nimi = 'antibiootit';
UPDATE tarkennuskysymys SET on_aktiivinen = false WHERE aihe = 'uni';
UPDATE yleiskysymys SET on_aktiivinen = false WHERE tunniste = 'arki';

UPDATE laakeryhma SET on_aktiivinen = true WHERE nimi = 'antibiootit';


-- ===== Jonon uudelleengenerointi uusilla kysymyksilla (VAIN DEV) =====
-- Tyhjenna jono (DELETE /dev/puhelutehtavat tai alla) ja aja tunnistus:
-- POST /puhelutehtavat/tunnista
-- DELETE FROM katseluloki; DELETE FROM hoitajan_tapaus;
-- DELETE FROM puhelutehtavan_muutos; DELETE FROM puhelutehtava;
