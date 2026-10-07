const kasittelylomake = document.querySelector("[data-kasittely-lomake]");
const ilmoitus = document.querySelector("[data-ilmoitus]");

if (kasittelylomake instanceof HTMLFormElement && ilmoitus instanceof HTMLElement) {
  kasittelylomake.addEventListener("submit", async (tapahtuma) => {
    tapahtuma.preventDefault();

    const submitPainike = tapahtuma.submitter;
    if (!(submitPainike instanceof HTMLButtonElement)) {
      naytaIlmoitus("Valitse käsittelytoiminto ennen tallennusta.", ilmoitus);
      return;
    }

    const painikkeet = kasittelylomake.querySelectorAll("button");
    painikkeet.forEach((painike) => {
      painike.disabled = true;
    });

    try {
      const vastaus = await fetch(kasittelylomake.action, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tila: submitPainike.value,
          muistiinpano: new FormData(kasittelylomake).get("muistiinpano") ?? "",
        }),
      });

      const tulos = await vastaus.json();
      if (!vastaus.ok) {
        throw new Error(tulos.detail ?? "Tallennus epäonnistui.");
      }

      naytaIlmoitus(tulos.viesti, ilmoitus);
      window.setTimeout(() => window.location.reload(), 650);
    } catch (virhe) {
      const viesti = virhe instanceof Error ? virhe.message : "Tallennus epäonnistui.";
      naytaIlmoitus(viesti, ilmoitus);
      painikkeet.forEach((painike) => {
        painike.disabled = false;
      });
    }
  });
}

function naytaIlmoitus(teksti, elementti) {
  elementti.textContent = teksti;
  elementti.classList.add("nakyvissa");
}
