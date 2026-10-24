# Kiosk overlay

Add this to the kiosk page once, replacing the address with the Pi's LAN address. It keeps retrying every ten seconds until the daemon's web server answers, which matters at boot when the kiosk starts before the daemon.

```html
<script>
(function load(){var s=document.createElement("script");s.src="http://192.0.2.4:8766/overlay.js?t="+Date.now();
s.onerror=function(){s.remove();setTimeout(load,10000)};document.body.appendChild(s)})();
</script>
```

`overlay.js` polls `/state.json` five times a second and shows `logo.svg` over the page: breathing while the daemon hears speech, still while it waits, spinning while Claude thinks, glowing in and out while the reply plays, gone when idle.
