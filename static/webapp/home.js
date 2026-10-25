/* A real city overview; no simulated drivers or arrival estimates. */
(() => {
  'use strict';
  const status = document.getElementById('home-map-status');
  if (!window.L) { status.hidden = false; return; }
  const config = JSON.parse(document.getElementById('public-map-config').textContent);
  const map = L.map('home-map', {zoomControl:false, scrollWheelZoom:false, dragging:false,
    touchZoom:false, doubleClickZoom:false, boxZoom:false, keyboard:false})
    .setView([config.latitude, config.longitude], 12);
  const attribution = document.createElement('span');
  attribution.textContent = config.attribution;
  const tiles = L.tileLayer(config.tileUrl, {maxZoom:19, attribution:attribution.innerHTML + ' · <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">License</a>'}).addTo(map);
  tiles.on('tileerror', () => { status.hidden = false; });
})();
