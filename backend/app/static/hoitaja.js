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
