// Show the page's front-matter description as a muted subtitle under its title.
document$.subscribe(function () {
  var h1 = document.querySelector(".md-content__inner > h1");
  var meta = document.querySelector('meta[name="description"]');
  if (!h1 || !meta || (h1.nextElementSibling && h1.nextElementSibling.classList.contains("pt-lede"))) return;
  var p = document.createElement("p");
  p.className = "pt-lede";
  p.textContent = meta.getAttribute("content");
  h1.insertAdjacentElement("afterend", p);
});
