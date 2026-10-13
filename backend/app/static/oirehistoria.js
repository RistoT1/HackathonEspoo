// Tapaussivun "Oireet puheluissa" (ECharts). Data tulee sivulle upotettuna JSONina
// (Oirehistoria-DTO), joten erillistä hakua ei ole. Tyyli Vipusen kaavioista: vähän
// mustetta, luku tai teksti suoraan merkissä (ei selitettä), yksi sävy voimakkuudelle.

// Oireet puheluittain -taulukon sivutus kahdella tasolla: kuukausi ja kuukauden sisällä
// (yli 10 puhelua). Kaikki sivut on renderöity palvelimella (uusimmat näkyvissä); tämä vain
// vaihtaa näkyvää sivua, joten uutta hakua tai audit-lokiriviä ei synny. Kuukautta vaihdettaessa
// avautuu sen uusimmat puhelut, ja taulukko vieritetään oikeaan reunaan.
(() => {
  const alue = document.querySelector("[data-kuukausisivut]");
  if (!alue) {
    return;
  }
  const kuukaudet = [...alue.querySelectorAll("[data-kuukausi]")];
  const kuukausiNimi = alue.querySelector("[data-kuukausi-nimi]");
  const kuukausiSiirrot = [...alue.querySelectorAll("[data-kuukausi-siirto]")];
  const sisasivutus = alue.querySelector("[data-sisasivutus]");
  const sivuNimi = alue.querySelector("[data-sivu-nimi]");
  const sivuSiirrot = [...alue.querySelectorAll("[data-sivu-siirto]")];
  if (kuukaudet.length === 0) {
    return;
  }

  let kuukausi = kuukaudet.length - 1;
  const sivutKuussa = (indeksi) => [...kuukaudet[indeksi].querySelectorAll("[data-kuukausisivu]")];
  let sivu = sivutKuussa(kuukausi).length - 1;

  function asetaPainikkeet(painikkeet, nykyinen, maara, avain) {
    painikkeet.forEach((painike) => {
      const kohde = nykyinen + Number(painike.dataset[avain]);
      painike.disabled = kohde < 0 || kohde >= maara;
    });
  }

  function paivita() {
    kuukaudet.forEach((elementti, i) => {
      elementti.hidden = i !== kuukausi;
    });
    const sivut = sivutKuussa(kuukausi);
    sivut.forEach((elementti, i) => {
      elementti.hidden = i !== sivu;
    });
    if (kuukausiNimi) {
      kuukausiNimi.textContent = kuukaudet[kuukausi].dataset.nimi;
    }
    asetaPainikkeet(kuukausiSiirrot, kuukausi, kuukaudet.length, "kuukausiSiirto");
    if (sisasivutus) {
      sisasivutus.hidden = sivut.length < 2;
      if (sivuNimi && sivut[sivu]) {
        sivuNimi.textContent = sivut[sivu].dataset.nimi;
      }
      asetaPainikkeet(sivuSiirrot, sivu, sivut.length, "sivuSiirto");
    }
    const taulukko = sivut[sivu]?.querySelector(".oirehistoria-taulukko");
    if (taulukko) {
      taulukko.scrollLeft = taulukko.scrollWidth;
    }
  }

  kuukausiSiirrot.forEach((painike) => {
    painike.addEventListener("click", () => {
      const kohde = kuukausi + Number(painike.dataset.kuukausiSiirto);
      if (kohde < 0 || kohde >= kuukaudet.length) {
        return;
      }
      kuukausi = kohde;
      sivu = sivutKuussa(kuukausi).length - 1;
      paivita();
    });
  });
  sivuSiirrot.forEach((painike) => {
    painike.addEventListener("click", () => {
      const kohde = sivu + Number(painike.dataset.sivuSiirto);
      if (kohde < 0 || kohde >= sivutKuussa(kuukausi).length) {
        return;
      }
      sivu = kohde;
      paivita();
    });
  });
  paivita();
})();

(() => {
  const lahde = document.getElementById("oirehistoria-data");
  if (!lahde || typeof echarts === "undefined") {
    return;
  }
  const data = JSON.parse(lahde.textContent);
  if (!data || data.oireet.length === 0) {
    return;
  }

  const FONTTI = 'Inter, "Segoe UI", Arial, sans-serif';
  const TEKSTI = "#334155";
  const VAIMEA = "#64748b";
  const PALKKI = "#3b82f6";
  const TYHJA = "#f8fafc";
  // Voimakkuus yhdellä sävyllä vaaleasta tummaan; poistunut vihreällä tekstillä (hyvä uutinen).
  const SOLUT = {
    lievä: { tausta: "#dbeafe", teksti: "#1e3a8a", nimi: "lievä" },
    kohtalainen: { tausta: "#93c5fd", teksti: "#1e3a8a", nimi: "kohtalainen" },
    voimakas: { tausta: "#2563eb", teksti: "#ffffff", nimi: "voimakas" },
    "ei tiedossa": { tausta: "#f1f5f9", teksti: "#475569", nimi: "mainittu" },
    poistunut: { tausta: "#ecfdf5", teksti: "#047857", nimi: "poistunut" },
  };
  const MAX_PALKKEJA = 5;
  const MAX_RIVEJA = 6;
  const MAX_SARAKKEITA = 6;
  // Pitkä nimi ("ruokahaluttomuus ja juominen") kahdelle riville, ei vie kaavion leveyttä.
  const NIMEN_LEVEYS = 128;
  const PALKIN_RIVI = 40;
  const SOLUN_RIVI = 46;

  function esc(teksti) {
    return String(teksti)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function paiva(puhelu) {
    return puhelu.nimi.split(" ")[0];
  }

  function viimeisinMerkinta(oire) {
    for (let i = data.puhelut.length - 1; i >= 0; i -= 1) {
      const merkinta = data.puhelut[i].oireet.find((m) => m.oire === oire);
      if (merkinta) {
        return { merkinta, puhelu: data.puhelut[i] };
      }
    }
    return null;
  }

  const tooltipPerus = {
    trigger: "item",
    confine: true,
    backgroundColor: "#ffffff",
    borderColor: "#e2e8f0",
    textStyle: { color: TEKSTI, fontSize: 14 },
    extraCssText: "box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08); line-height: 1.45;",
  };

  function piirra(elementti, option, korkeus) {
    elementti.style.height = `${korkeus}px`;
    const kaavio = echarts.init(elementti, null, { renderer: "svg" });
    kaavio.setOption({ textStyle: { fontFamily: FONTTI, color: TEKSTI }, animationDuration: 300, ...option });
    new ResizeObserver(() => kaavio.resize()).observe(elementti);
  }

  // --- Useimmin kerrotut oireet: vaakapalkit, luku palkin päässä ---

  const yleisyys = document.querySelector('[data-oirekaavio="yleisyys"]');
  if (yleisyys) {
    const oireet = data.oireet.slice(0, MAX_PALKKEJA);
    piirra(
      yleisyys,
      {
        grid: { left: 4, right: 36, top: 4, bottom: 4, containLabel: true },
        tooltip: {
          ...tooltipPerus,
          formatter: (p) => {
            const viimeisin = viimeisinMerkinta(p.name);
            const lisa = viimeisin
              ? `<br>Viimeksi ${esc(paiva(viimeisin.puhelu))}: ${esc(viimeisin.merkinta.voimakkuus)}`
              : "";
            return `<b>${esc(p.name)}</b><br>${p.value} puhelussa${lisa}`;
          },
        },
        xAxis: { type: "value", show: false, minInterval: 1 },
        yAxis: {
          type: "category",
          data: oireet,
          inverse: true,
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { fontSize: 14, color: TEKSTI, width: NIMEN_LEVEYS, overflow: "break" },
        },
        series: [
          {
            type: "bar",
            data: oireet.map((oire) => data.mainintoja[oire] ?? 0),
            barMaxWidth: 18,
            itemStyle: { color: PALKKI, borderRadius: [0, 4, 4, 0] },
            label: { show: true, position: "right", fontSize: 14, color: TEKSTI },
            emphasis: { disabled: true },
          },
        ],
      },
      oireet.length * PALKIN_RIVI + 8,
    );
  }

  // --- Voimakkuus puheluittain: korostustaulukko, teksti solussa ---

  const voimakkuus = document.querySelector('[data-oirekaavio="voimakkuus"]');
  const puhelut = data.puhelut.filter((puhelu) => puhelu.tavoitettu).slice(-MAX_SARAKKEITA);
  // Rivit = näkyvien puhelujen oireet: yleisin ensin, tasapelissä tuoreimmin mainittu ensin,
  // jotta uusimman puhelun oire ei jää pois eikä sarake tyhjäksi.
  function taulukonOireet() {
    const viimeksi = new Map();
    puhelut.forEach((puhelu, indeksi) => {
      puhelu.oireet.forEach((m) => viimeksi.set(m.oire, indeksi));
    });
    return [...viimeksi.keys()]
      .sort(
        (a, b) =>
          (data.mainintoja[b] ?? 0) - (data.mainintoja[a] ?? 0) || viimeksi.get(b) - viimeksi.get(a),
      )
      .slice(0, MAX_RIVEJA);
  }

  if (voimakkuus && puhelut.length > 0) {
    const oireet = taulukonOireet();
    const solut = [];
    puhelut.forEach((puhelu, sarake) => {
      oireet.forEach((oire, rivi) => {
        const merkinta = puhelu.oireet.find((m) => m.oire === oire);
        const tyyli = merkinta ? (SOLUT[merkinta.voimakkuus] ?? SOLUT["ei tiedossa"]) : null;
        solut.push({
          value: [sarake, rivi, 1],
          merkinta,
          puhelu,
          oire,
          itemStyle: { color: tyyli ? tyyli.tausta : TYHJA },
          label: tyyli ? { show: true, color: tyyli.teksti, formatter: tyyli.nimi } : { show: false },
        });
      });
    });

    piirra(
      voimakkuus,
      {
        grid: { left: 4, right: 4, top: 4, bottom: 4, containLabel: true },
        tooltip: {
          ...tooltipPerus,
          formatter: (p) => {
            const { merkinta, puhelu, oire } = p.data;
            const otsikko = `<b>${esc(oire)}</b><br>${esc(puhelu.nimi)}`;
            if (!merkinta) {
              return `${otsikko}<br>ei mainittu`;
            }
            const sanat = merkinta.kuvaukset.filter((k) => k.toLowerCase() !== oire);
            const kerrottu = sanat.length ? `<br>Kerrottu: ”${esc(sanat.join("”, ”"))}”` : "";
            return `${otsikko}<br>Voimakkuus: ${esc(merkinta.voimakkuus)}${kerrottu}`;
          },
        },
        xAxis: {
          type: "category",
          data: puhelut.map((puhelu) => {
            const [pv, , kello] = puhelu.nimi.split(" ");
            return `${pv}\n{kello|${kello ?? ""}}`;
          }),
          position: "bottom",
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: {
            interval: 0,
            fontSize: 13,
            color: TEKSTI,
            lineHeight: 17,
            rich: { kello: { fontSize: 12, color: VAIMEA } },
          },
        },
        yAxis: {
          type: "category",
          data: oireet,
          inverse: true,
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { fontSize: 14, color: TEKSTI, width: NIMEN_LEVEYS, overflow: "break" },
        },
        series: [
          {
            type: "heatmap",
            data: solut,
            itemStyle: { borderColor: "#ffffff", borderWidth: 3, borderRadius: 6 },
            label: { fontSize: 12 },
            emphasis: { disabled: true },
          },
        ],
      },
      oireet.length * SOLUN_RIVI + 44,
    );
  }
})();
