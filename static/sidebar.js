// Barra laterale condivisa: riduzione a icone su schermi larghi (preferenza ricordata nel
// browser) e pannello a scomparsa su telefono/tablet, chiuso da solo dopo una scelta.
(function () {
    const KEY = "adaptiq_sidebar_collapsed";
    const narrow = () => window.matchMedia("(max-width: 991.98px)").matches;

    function setCollapsed(v) {
        document.body.classList.toggle("sb-collapsed", v);
        try { localStorage.setItem(KEY, v ? "1" : "0"); } catch (e) {}
    }

    window.toggleSidebar = function () {
        if (narrow()) document.body.classList.toggle("sb-open");
        else setCollapsed(!document.body.classList.contains("sb-collapsed"));
    };
    window.closeSidebarOnMobile = function () { document.body.classList.remove("sb-open"); };

    let saved = null;
    try { saved = localStorage.getItem(KEY); } catch (e) {}
    if (saved === "1") document.body.classList.add("sb-collapsed");

    // La barra parte sotto l'intestazione: finché questa è visibile ne sottrae l'altezza,
    // così il fondo della barra (pulsante "Riduci menu") resta sempre a schermo.
    let ticking = false;
    function updateOffset() {
        ticking = false;
        const header = document.querySelector("header");
        const offset = header ? Math.max(0, header.getBoundingClientRect().bottom) : 0;
        document.documentElement.style.setProperty("--sb-offset", offset + "px");
    }
    const scheduleOffset = () => { if (!ticking) { ticking = true; requestAnimationFrame(updateOffset); } };
    window.addEventListener("scroll", scheduleOffset, { passive: true });
    window.addEventListener("resize", scheduleOffset);
    document.addEventListener("DOMContentLoaded", updateOffset);
    updateOffset();

    document.addEventListener("keydown", e => { if (e.key === "Escape") closeSidebarOnMobile(); });
    window.addEventListener("resize", () => { if (!narrow()) closeSidebarOnMobile(); });
})();
