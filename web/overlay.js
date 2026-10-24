// Kitchen dashboard overlay for the voice assistant. Polls the daemon's state and shows a
// Claude logo while an exchange is in progress: breathing while it hears you speak, still while it
// waits for you, spinning while it thinks, glowing in and out while it talks, gone when idle. Loaded by one <script> tag in dashboard.html.
(function () {
  var base = (document.currentScript && document.currentScript.src.replace(/\/overlay\.js.*$/, "")) || "http://192.0.2.4:8766";
  var css = document.createElement("style");
  css.textContent =
    "#claude-voice{position:fixed;inset:0;display:none;align-items:center;justify-content:center;" +
    "background:rgba(0,0,0,.72);z-index:2147483647;transition:opacity .25s}" +
    "#claude-voice.on{display:flex}" +
    "#claude-voice img{width:34vmin;height:34vmin;filter:drop-shadow(0 0 24px rgba(217,119,87,.55));transform:scale(1)}" +
    "#claude-voice.hearing img{animation:claude-breathe 1.1s ease-in-out infinite}" +
    "#claude-voice.thinking img{animation:claude-spin 2.4s linear infinite}" +
    "#claude-voice.speaking img{animation:claude-talk .7s ease-in-out infinite}" +
    "@keyframes claude-talk{0%,100%{filter:drop-shadow(0 0 16px rgba(217,119,87,.45))}50%{filter:drop-shadow(0 0 48px rgba(217,119,87,1))}}" +
    "@keyframes claude-breathe{0%,100%{transform:scale(1)}50%{transform:scale(1.18)}}" +
    "@keyframes claude-spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}";
  document.head.appendChild(css);
  var box = document.createElement("div");
  box.id = "claude-voice";
  var img = document.createElement("img");
  img.src = base + "/logo.svg";
  box.appendChild(img);
  document.body.appendChild(box);
  var last = "";
  function tick() {
    fetch(base + "/state.json", {cache: "no-store"}).then(function (r) { return r.json(); }).then(function (s) {
      if (s.state !== last) {
        last = s.state;
        box.className = s.state === "idle" ? "" : "on " + s.state;
      }
    }).catch(function () { if (last !== "idle") { last = "idle"; box.className = ""; } });
  }
  setInterval(tick, 200);
})();
