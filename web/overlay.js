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
    "@keyframes claude-spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}" +
    "#claude-music{position:fixed;left:0;right:0;bottom:0;display:none;align-items:center;gap:14px;padding:10px 16px;" +
    "background:rgba(20,16,14,.92);color:#f3ede8;font:500 17px/1.25 system-ui,sans-serif;z-index:2147483646;" +
    "box-shadow:0 -6px 24px rgba(0,0,0,.5)}" +
    "#claude-music.on{display:flex}" +
    "#claude-music .np{flex:1;min-width:0;overflow:hidden}" +
    "#claude-music .np b{display:block;font-size:19px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}" +
    "#claude-music .np span{display:block;opacity:.75;font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}" +
    "#claude-music button{width:58px;height:58px;border:0;border-radius:50%;background:rgba(255,255,255,.1);color:#f3ede8;" +
    "font-size:24px;line-height:58px;padding:0;touch-action:manipulation;cursor:pointer}" +
    "#claude-music button:active{background:rgba(217,119,87,.8)}" +
    "#claude-music .lvl{font-variant-numeric:tabular-nums;opacity:.7;font-size:15px;min-width:34px;text-align:center}";
  document.head.appendChild(css);
  var box = document.createElement("div");
  box.id = "claude-voice";
  var img = document.createElement("img");
  img.src = base + "/logo.svg";
  box.appendChild(img);
  document.body.appendChild(box);
  // Now-playing bar: shown while Pandora is on, with the controls a music app would have.
  var bar = document.createElement("div");
  bar.id = "claude-music";
  bar.innerHTML = '<div class="np"><b></b><span></span></div>' +
    '<button data-a="toggle" title="Play / pause">&#9646;&#9646;</button>' +
    '<button data-a="next" title="Skip">&#9197;</button>' +
    '<button data-a="love" title="Thumbs up">&#128077;</button>' +
    '<button data-a="ban" title="Thumbs down">&#128078;</button>' +
    '<button data-a="vol-" title="Quieter">&minus;</button><div class="lvl"></div><button data-a="vol+" title="Louder">+</button>' +
    '<button data-a="stop" title="Off">&#10005;</button>';
  document.body.appendChild(bar);
  var music = null;
  function send(action, arg) {
    // text/plain body: a "simple" request, no CORS preflight needed
    return fetch(base + "/music", {method: "POST", headers: {"Content-Type": "text/plain"},
      body: JSON.stringify({action: action, station: arg === undefined ? null : arg})})
      .then(function () { setTimeout(tick, 300); }).catch(function (e) { console.warn("music control failed", e); });
  }
  bar.addEventListener("click", function (e) {
    var b = e.target.closest("button"); if (!b || !music) return;
    var a = b.getAttribute("data-a");
    if (a === "toggle") send(music.playing ? "pause" : "play");
    else if (a === "vol+") send("volume", Math.min(10, (music.level || 8) + 1));
    else if (a === "vol-") send("volume", Math.max(1, (music.level || 8) - 1));
    else send(a);
  });
  function showMusic(m) {
    music = m;
    if (!m || !m.on || !(m.title || m.station)) { bar.className = ""; return; }
    bar.className = "on";
    bar.querySelector(".np b").textContent = m.title ? m.title + (m.artist ? " \u2014 " + m.artist : "") : "Pandora";
    bar.querySelector(".np span").textContent = (m.station || "") + (m.playing ? "" : "  \u00b7  paused");
    bar.querySelector('[data-a="toggle"]').innerHTML = m.playing ? "&#9646;&#9646;" : "&#9654;";
    bar.querySelector(".lvl").textContent = m.level || "";
  }
  var last = "";
  function tick() {
    fetch(base + "/state.json", {cache: "no-store"}).then(function (r) { return r.json(); }).then(function (s) {
      if (s.state !== last) {
        last = s.state;
        box.className = s.state === "idle" ? "" : "on " + s.state;
      }
      showMusic(s.music);
    }).catch(function () { if (last !== "idle") { last = "idle"; box.className = ""; } showMusic(null); });
  }
  setInterval(tick, 200);
})();
