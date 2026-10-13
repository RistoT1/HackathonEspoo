const ilmoitus = document.querySelector("[data-ilmoitus]");
const tilanmuutosPainikkeet = document.querySelectorAll("[data-tilanmuutos]");

tilanmuutosPainikkeet.forEach((painike) => {
  painike.addEventListener("click", async () => {
    if (!(painike instanceof HTMLButtonElement) || !(ilmoitus instanceof HTMLElement)) {
      return;
    }

    const { tehtava, tila } = painike.dataset;
    if (!tehtava || !tila) {
      naytaIlmoitus("Tehtävän tilapäivityksestä puuttuu tieto.", ilmoitus);
      return;
    }

    const vahvistus = tila === "PERUTTU"
      ? window.confirm("Perutaanko tämä soittotehtävä?")
      : true;
    if (!vahvistus) {
      return;
    }

    tilanmuutosPainikkeet.forEach((toiminto) => {
      toiminto.disabled = true;
    });

    try {
      const vastaus = await fetch(`/puhelutehtavat/${encodeURIComponent(tehtava)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tila }),
      });

      const tulos = await vastaus.json();
      if (!vastaus.ok) {
        const selitys = typeof tulos.detail === "string"
          ? tulos.detail
          : "Tilapäivitys epäonnistui.";
        throw new Error(selitys);
      }

      naytaIlmoitus("Tehtävän tila päivitettiin.", ilmoitus);
      window.setTimeout(() => window.location.reload(), 450);
    } catch (virhe) {
      const viesti = virhe instanceof Error
        ? virhe.message
        : "Tilapäivitys epäonnistui. Yritä uudelleen.";
      naytaIlmoitus(viesti, ilmoitus);
      tilanmuutosPainikkeet.forEach((toiminto) => {
        toiminto.disabled = false;
      });
    }
  });
});

function naytaIlmoitus(teksti, elementti) {
  elementti.textContent = teksti;
  elementti.classList.add("nakyvissa");
}

// Jonon automaattinen päivitys: puhelun arvio ilmestyy jonoon ilman sivun latausta.
// Vain jonosivu: tehtävänäkymää ei pollata, koska jokainen katselu kirjataan audit-lokiin.
const JONON_PAIVITYSVALI_MS = 5000;
const ILMOITUKSEN_KESTO_MS = 5000;

if (document.querySelector("[data-paivittyva]")) {
  window.setInterval(paivitaJono, JONON_PAIVITYSVALI_MS);
}

async function paivitaJono() {
  if (document.hidden) {
    return;
  }

  let uusiSivu;
  try {
    const vastaus = await fetch(window.location.href, { cache: "no-store" });
    if (!vastaus.ok) {
      return;
    }
    uusiSivu = new DOMParser().parseFromString(await vastaus.text(), "text/html");
  } catch {
    return; // verkkokatko: yritetään seuraavalla kierroksella
  }

  const vanhatTasot = rivienTasot(document);
  document.querySelectorAll("[data-paivittyva]").forEach((alue) => {
    const uusi = uusiSivu.querySelector(`[data-paivittyva="${alue.dataset.paivittyva}"]`);
    if (uusi && uusi.innerHTML !== alue.innerHTML) {
      alue.innerHTML = uusi.innerHTML;
    }
  });

  const uudetTasot = [];
  rivienTasot(document).forEach((taso, tehtava) => {
    const arvioMuuttui = taso !== "" && taso !== vanhatTasot.get(tehtava);
    if (!arvioMuuttui) {
      return;
    }
    uudetTasot.push(taso);
    document
      .querySelector(`tr[data-tehtava="${CSS.escape(tehtava)}"]`)
      ?.classList.add("rivi-uusi-arvio");
  });

  if (uudetTasot.length > 0 && ilmoitus instanceof HTMLElement) {
    const teksti = uudetTasot.length === 1
      ? `Uusi puhelun arvio jonossa: ${uudetTasot[0]}`
      : `${uudetTasot.length} uutta puhelun arviota jonossa`;
    naytaIlmoitus(teksti, ilmoitus);
    window.setTimeout(() => ilmoitus.classList.remove("nakyvissa"), ILMOITUKSEN_KESTO_MS);
  }
}

function rivienTasot(juuri) {
  const tasot = new Map();
  juuri.querySelectorAll("tr[data-tehtava]").forEach((rivi) => {
    tasot.set(rivi.dataset.tehtava, rivi.dataset.taso ?? "");
  });
  return tasot;
}
