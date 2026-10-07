/* ===== облик интерфейса ===== */
const SKINS = [
  {k:"aurora",  n:"Аврора",  d:"тёмное стекло и неон",        sw:"linear-gradient(140deg,#0b1426,#16233d 45%,#22c8f5)"},
  {k:"dawn",    n:"Рассвет", d:"светлое стекло, тёплый свет", sw:"linear-gradient(140deg,#fff6ea,#f1ece4 45%,#00b7e8)"},
  {k:"contour", n:"Контур",  d:"плотный тёмно-синий",         sw:"linear-gradient(140deg,#0e1929,#12233a 45%,#00b3e3)"}
];

function applySkin(k){
  document.documentElement.setAttribute("data-skin", k);
  store.set("skin", k);
  renderSkinMenu();
}
function renderSkinMenu(){
  const box = $("#skinMenu");
  const cur = document.documentElement.getAttribute("data-skin") || "aurora";
  box.textContent = "";
  SKINS.forEach(s => {
    const b = el("button","skinitem");
    b.type = "button";
    b.setAttribute("aria-pressed", String(s.k === cur));
    const sw = el("span","swatch");
    sw.style.background = s.sw;
    const t = el("div");
    t.append(el("b",null,s.n), el("span",null,s.d));
    b.append(sw, t);
    b.onclick = ev => { ev.stopPropagation(); applySkin(s.k); };
    box.append(b);
  });
}
$("#themeBtn").onclick = e => {
  e.stopPropagation();
  const box = $("#skinMenu");
  box.hidden = !box.hidden;
  if(!box.hidden){ closeCal(); renderSkinMenu(); }
};
document.addEventListener("click", e => {
  const box = $("#skinMenu");
  if(!box.hidden && !box.contains(e.target)) box.hidden = true;
});
/* Первый заход подстраивается под системную тему, дальше решает оператор */
applySkin(store.get("skin", null) ||
  (matchMedia("(prefers-color-scheme: light)").matches ? "dawn" : "aurora"));
$("#skinMenu").hidden = true;
