/* Shared REST API client for the same-origin web application. */
(() => {
  'use strict';
  const config = JSON.parse(document.getElementById('app-config').textContent);
  const $ = selector => document.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const date = value => value ? new Date(value).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '';
  const amount = (value, currency = config.currency) => `${esc(currency)} ${Number(value || 0).toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2})}`;
  const labels = {REQUESTED:'Ride requested', SEARCHING:'Finding your driver', ACCEPTED:'Your driver is assigned', DRIVER_ARRIVING:'Driver on the way', DRIVER_ARRIVED:'Your driver has arrived', IN_PROGRESS:'Enjoy your journey', COMPLETED:'You have arrived', CANCELLED:'Ride cancelled'};
  const state = {tab:'ride', quote:null, quoteVersion:0, requestId:null, active:null, selected:null, profile:null, vehicle:null, pick:'pickup', page:1, notificationPage:1, syncing:null, syncAgain:false, accountLoaded:false, online:false, sharing:false, locationTimer:null, socket:null, closing:false};
  let map, routeLine, markers = {}, tripSignature = '', toastTimer, refreshTimer;
  let notificationSignature = '', socketFailures = 0;
  let routeSignature = '';
  let routeRequest = 0;
  let routeController;
  let routeRetryAt = 0, lastRouteQuote = null;
  let deviceWatch = null, deviceCoords = null, deviceFixAt = 0, deviceAccuracy = null;
  let locationStart = null, locationTickBusy = false;
  let pickupLine = null, pickupRouteKey = '', pickupRouteAt = 0, pickupRouteRequest = 0;
  let followDevice = false, locationPosting = false, lastLocationPost = 0, locationGeneration = 0;
  const markerAnimations = {};
  let chatRide = null, chatRequest = 0, chatSignature = '', chatUnread = 0;
  const chatDrafts = new Map();
  let chatLoading = null;
  let chatMessageIds = new Set();
  function clearRoute() {
    routeRequest++;
    routeController?.abort();
    routeController = null;
    routeSignature = '';
    lastRouteQuote = null; routeRetryAt = 0;
    if ($('#retry-route')) $('#retry-route').hidden = true;
    if (routeLine) { map?.removeLayer(routeLine); routeLine = null; }
    stopMarkers.forEach(marker => map?.removeLayer(marker)); stopMarkers = [];
  }
  let historyRequest = 0, notificationsRequest = 0, earningsRequest = 0;
  let historyFilters = new URLSearchParams(), earningsFilters = new URLSearchParams();
  let savedPlacesPage = 1, savedPlacesRequest = 0, editingPlaceId = null;
  let bookingStops = [];
  let recentTripsAt = 0, recentTripsLoading = false;
  let estimateKey = '', estimateAt = 0, estimateRequest = 0;
  let stopMarkers = [];
  let scheduledPage = 1;
  let favouritePage = 1, lostItemPage = 1, favouriteRequest = 0, lostItemRequest = 0;
  let initialRideId = new URLSearchParams(location.search).get('ride');
  const paymentNames = {CASH:'Cash', CARD:'Card', WALLET:'Nori Wallet · demo', SSL_DEMO:'SSLCOMMERZ · demo'};
  const isDemoMethod = method => ['WALLET', 'SSL_DEMO'].includes(method);
  let walletPage = 1, walletRequest = 0, checkoutTask = null, checkoutResolve = null, checkoutBusy = false;

  async function loadWallet(page = walletPage) {
    const version = ++walletRequest;
    const [wallet, entries] = await Promise.all([api('payments/wallet/'), api(`payments/wallet/transactions/?page=${page}`)]);
    if (version !== walletRequest) return;
    walletPage = page;
    $('#wallet-balance').textContent = `${wallet.currency} ${Number(wallet.balance).toLocaleString(undefined, {minimumFractionDigits:2})}`;
    $('#wallet-currency').textContent = 'Demo credits · no cash value';
    $('#wallet-transactions').innerHTML = entries.results.length ? entries.results.map(entry => `<article class="wallet-entry"><span class="wallet-entry-icon">${entry.kind === 'RIDE' ? '↗' : '↙'}</span><div><strong>${esc(entry.description)}</strong><small>${esc(date(entry.created_at))}</small><small>Balance after: ${amount(entry.balance_after, wallet.currency)}</small></div><strong class="${Number(entry.amount) > 0 ? 'wallet-credit' : ''}">${Number(entry.amount) > 0 ? '+' : '−'}${amount(Math.abs(Number(entry.amount)), wallet.currency)}</strong></article>`).join('') : '<div class="empty-state">Your wallet starts here.<br>Add demo credits to try prepaid rides.</div>';
    $('#wallet-page').textContent = `Page ${page}`;
    $('#wallet-prev').disabled = !entries.previous;
    $('#wallet-next').disabled = !entries.next;
    $('#wallet-prev').dataset.restDisabled = String(!entries.previous);
    $('#wallet-next').dataset.restDisabled = String(!entries.next);
  }
  function demoCheckout(details, task) {
    const dialog = $('#demo-checkout');
    if (dialog.open) return Promise.reject(new Error('Finish the open demo checkout first.'));
    checkoutTask = task;
    $('#demo-checkout-form').reset();
    $('#demo-checkout-purpose').textContent = details.purpose;
    $('#demo-checkout-amount').textContent = `${details.currency} ${Number(details.amount).toFixed(2)}`;
    $('#demo-checkout-status').textContent = '';
    dialog.showModal();
    return new Promise(resolve => { checkoutResolve = resolve; });
  }

  function toast(message, error = false) {
    clearTimeout(toastTimer);
    const node = $('#toast'); node.textContent = message; node.classList.toggle('error', error); node.hidden = false;
    toastTimer = setTimeout(() => { node.hidden = true; }, error ? 8500 : 4500);
  }
  function explain(data) {
    if (typeof data === 'string') return data;
    if (Array.isArray(data)) return data.map(explain).join(' ');
    return Object.entries(data || {}).map(([key, value]) => `${key === 'detail' || key === 'non_field_errors' ? '' : key.replaceAll('_', ' ') + ': '}${explain(value)}`).join(' ');
  }
  async function api(path, {method = 'GET', data, optional = false, signal} = {}) {
    const isFormData = typeof FormData !== 'undefined' && data instanceof FormData;
    const csrf = document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=').slice(1).join('=');
    const headers = {'X-CSRFToken': decodeURIComponent(csrf || '')};
    if (!isFormData && data !== undefined) headers['Content-Type'] = 'application/json';
    const body = isFormData ? data : (data === undefined ? undefined : JSON.stringify(data));
    let response;
    try {
      response = await fetch(`/api/${path}`, {method, credentials:'same-origin', headers, body, signal});
    } catch (err) {
      if (optional) return null;
      throw err;
    }
    const result = await response.json().catch(() => null);
    if (optional && (response.status === 404 || response.status === 405)) return null;
    if (response.status === 401) { state.closing = true; state.socket?.close(); location.assign('/login/?next=/app/'); throw new Error('Please log in again.'); }
    if (!response.ok) {
      if (optional) return null;
      throw new Error(explain(result) || `The request failed (${response.status}).`);
    }
    return result;
  }
  async function busy(button, task) {
    if (button?.disabled) return;
    if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); }
    try { await task(); } catch (error) { toast(error.message || 'Please try again.', true); }
    finally { if (button) { button.disabled = button.dataset.restDisabled === 'true'; button.removeAttribute('aria-busy'); } }
  }
  function onForm(id, handler) {
    const form = $(id); if (!form) return;
    form.addEventListener('submit', event => { event.preventDefault(); busy(form.querySelector('[type=submit]'), () => handler(Object.fromEntries(new FormData(form)), form)); });
    form.addEventListener('invalid', event => { const details = event.target.closest('details'); if (details) details.open = true; }, true);
  }
  function populate(form, data) { if (!form || !data) return; for (const [key, value] of Object.entries(data)) if (form.elements[key]) form.elements[key].value = value ?? ''; }
  function point(kind, lat, lon, fit = false) {
    if (!map || !Number.isFinite(Number(lat)) || !Number.isFinite(Number(lon))) return;
    const coords = [Number(lat), Number(lon)];
    if (markers[kind]) {
      cancelAnimationFrame(markerAnimations[kind]);
      const marker = markers[kind], from = marker.getLatLng(), started = performance.now();
      const animate = now => {
        const progress = Math.min(1, (now - started) / 600);
        marker.setLatLng([from.lat + (coords[0] - from.lat) * progress, from.lng + (coords[1] - from.lng) * progress]);
        if (progress < 1) markerAnimations[kind] = requestAnimationFrame(animate);
      };
      if (['driver', 'device'].includes(kind) && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) markerAnimations[kind] = requestAnimationFrame(animate);
      else marker.setLatLng(coords);
    } else markers[kind] = L.marker(coords, {title:kind === 'device' ? 'Your device location' : kind === 'driver' ? 'Driver location' : kind,
      icon:L.divIcon({className:'', html:`<span class="map-pin ${kind}">${kind === 'pickup' ? 'A' : kind === 'destination' ? 'B' : kind === 'device' ? '●' : '↗'}</span>`, iconSize:[25,25], iconAnchor:[12,12]})}).addTo(map);
    if (fit) map.setView(coords, 14);
  }
  async function drawRoute(quote, fit = false) {
    if (!quote || !map) return;
    point('pickup', quote.pickup_latitude, quote.pickup_longitude);
    point('destination', quote.destination_latitude, quote.destination_longitude);
    const line = [[Number(quote.pickup_latitude), Number(quote.pickup_longitude)], ...(quote.stops || []).map(stop => [Number(stop.latitude), Number(stop.longitude)]), [Number(quote.destination_latitude), Number(quote.destination_longitude)]];
    const geometry = quote.route_geometry;
    const signature = JSON.stringify([line, geometry || null]);
    if (signature === routeSignature && (routeLine || Date.now() < routeRetryAt || routeController)) {
      if (fit && routeLine) map.fitBounds(routeLine.getBounds(), {padding:[55,80], maxZoom:15});
      return;
    }
    clearRoute();
    lastRouteQuote = quote;
    stopMarkers = (quote.stops || []).map((stop, index) => {
      const label = document.createElement('span'); label.textContent = `Stop ${index + 1}: ${stop.label}`;
      return L.marker([Number(stop.latitude), Number(stop.longitude)], {title:label.textContent,
        icon:L.divIcon({className:'', html:`<span class="map-pin stop">${index + 1}</span>`, iconSize:[25,25], iconAnchor:[12,12]})}).bindTooltip(label).addTo(map);
    });
    const version = routeRequest;
    routeSignature = signature;
    $('#retry-route').hidden = true;
    if (fit) map.fitBounds(line, {padding:[55,80], maxZoom:15});
    try {
      let road = geometry;
      if (road?.type !== 'LineString' || !road.coordinates?.length) {
        routeController = new AbortController();
        $('#map-instruction').textContent = 'Finding a road route…';
        const result = await api('rides/route/', {method:'POST', signal:routeController.signal,
          data:{coordinates:line.map(([latitude, longitude]) => ({latitude:latitude.toFixed(6), longitude:longitude.toFixed(6)}))}});
        road = result.geometry;
      }
      if (version !== routeRequest) return;
      routeController = null;
      routeRetryAt = 0;
      const roadLine = road.coordinates.map(([longitude, latitude]) => [latitude, longitude]);
      routeLine = L.polyline(roadLine, {color:'#285c4e', weight:4}).addTo(map);
      $('#map-instruction').textContent = quote.route_geometry?.type === 'LineString'
        ? 'Road route shown.' : 'Road route shown. Select See fare for your estimate.';
      if (fit) map.fitBounds(routeLine.getBounds(), {padding:[55,80], maxZoom:15});
    } catch (error) {
      if (version !== routeRequest || error.name === 'AbortError') return;
      routeController = null;
      routeRetryAt = Date.now() + 15000;
      $('#retry-route').hidden = false;
      $('#map-instruction').textContent = error.message || 'Road routing is unavailable. Please try again.';
    }
  }
  async function drawPickupRoute(ride) {
    if (!map) return;
    const eligible = ride && ['ACCEPTED','DRIVER_ARRIVING'].includes(ride.status) && ride.driver?.location;
    const key = eligible ? ride.id : '';
    if (key !== pickupRouteKey) {
      pickupRouteRequest++; pickupRouteAt = 0; pickupRouteKey = key;
      if (pickupLine) { map.removeLayer(pickupLine); pickupLine = null; }
    }
    if (!eligible || Date.now() - pickupRouteAt < 15000) return;
    pickupRouteAt = Date.now();
    const request = ++pickupRouteRequest;
    const location = config.user.role === 'DRIVER' && state.sharing && deviceCoords ? deviceCoords : ride.driver.location;
    try {
      const route = await api('rides/route/', {method:'POST', data:{coordinates:[
        {latitude:Number(location.latitude).toFixed(6), longitude:Number(location.longitude).toFixed(6)},
        {latitude:ride.quote.pickup_latitude, longitude:ride.quote.pickup_longitude},
      ]}});
      if (request !== pickupRouteRequest) return;
      const points = route.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
      if (pickupLine) pickupLine.setLatLngs(points);
      else pickupLine = L.polyline(points, {color:'#7960ac', weight:4, dashArray:'8 6'}).addTo(map);
    } catch (_) {
      if (request !== pickupRouteRequest) return;
      if (pickupLine) { map.removeLayer(pickupLine); pickupLine = null; }
      $('#map-instruction').textContent = 'Trip road route shown. Driver-to-pickup route is temporarily unavailable.';
    }
  }
  async function choosePoint(kind, lat, lon, customLabel = null) {
    const form = $('#quote-form'); if (!form || state.active || state.selected) return;
    form.elements[`${kind}_latitude`].value = Number(lat).toFixed(6);
    form.elements[`${kind}_longitude`].value = Number(lon).toFixed(6);
    const label = form.elements[`${kind}_label`];
    if (customLabel) {
      label.value = customLabel;
      label.dataset.generated = 'false';
    } else if (!label.value || label.dataset.generated === 'true') {
      try {
        const places = await api(`rides/places/search/?q=`);
        let nearestName = null, minDist = Infinity;
        for (const p of (places || [])) {
          const d = Math.hypot(Number(lat) - Number(p.latitude), Number(lon) - Number(p.longitude));
          if (d < minDist) { minDist = d; nearestName = p.label; }
        }
        label.value = (minDist < 0.025 && nearestName) ? `Near ${nearestName}` : (kind === 'pickup' ? 'Selected Pickup' : 'Selected Destination');
      } catch (_) {
        label.value = kind === 'pickup' ? 'Selected Pickup' : 'Selected Destination';
      }
      label.dataset.generated = 'true';
    }
    point(kind, lat, lon);
    invalidateQuote();
    state.pick = kind === 'pickup' ? 'destination' : 'pickup';
    $('#map-instruction').textContent = kind === 'pickup' ? 'Now choose your destination' : 'Your route is ready. See your fare to continue.';
    if (form.elements.pickup_latitude.value && form.elements.destination_latitude.value) drawRoute({...Object.fromEntries(new FormData(form)), stops:bookingStops});
  }
  function setupMap() {
    if (!window.L) { $('#map-instruction').textContent = 'Map unavailable. Search for a pickup and destination to continue.'; return; }
    map = L.map('ride-map', {zoomControl:true}).setView([config.map.latitude, config.map.longitude], 13);
    const tiles = L.tileLayer(config.map.tileUrl, {maxZoom:19, attribution:esc(config.map.attribution) + ' · <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">License</a>'}).addTo(map);
    tiles.on('tileerror', () => { $('#map-instruction').textContent = 'Map tiles unavailable. You can still search for locations.'; });
    map.on('click', event => { if (config.user.role === 'RIDER') choosePoint(state.pick, event.latlng.lat, event.latlng.lng); });
    map.on('dragstart', () => setFollowDevice(false));
  }
  function gps() {
    return new Promise((resolve, reject) => {
      if (!navigator.geolocation) { reject(new Error('This browser does not support location sharing. Enter coordinates manually.')); return; }
      navigator.geolocation.getCurrentPosition(position => resolve(position.coords), () => reject(new Error('Location is unavailable. Allow location access and try again.')), {enableHighAccuracy:true, timeout:12000, maximumAge:0});
    });
  }
  function setFollowDevice(enabled) {
    followDevice = enabled;
    $('#follow-device').setAttribute('aria-pressed', String(enabled));
    $('#follow-device').textContent = enabled ? 'Following you' : 'Follow me';
    if (enabled && deviceCoords) map?.panTo([deviceCoords.latitude, deviceCoords.longitude]);
  }
  function displayDevice(coords) {
    deviceCoords = coords; deviceFixAt = Date.now();
    point('device', coords.latitude, coords.longitude);
    if (map) {
      const center = [coords.latitude, coords.longitude];
      if (!deviceAccuracy) deviceAccuracy = L.circle(center, {radius:coords.accuracy, color:'#2878d0', weight:1, fillOpacity:0.08, interactive:false}).addTo(map);
      else deviceAccuracy.setLatLng(center).setRadius(coords.accuracy);
      if (followDevice) map.panTo(center, {animate:true});
    }
    $('#device-location-status').textContent = `Live location · accuracy ±${Math.round(coords.accuracy)} m`;
  }
  async function sendLocation(coords = deviceCoords) {
    if (locationPosting || !coords || config.user.role !== 'DRIVER') return;
    locationPosting = true;
    lastLocationPost = Date.now();
    const generation = locationGeneration;
    try {
      const profile = await api('drivers/location/', {method:'POST', data:{latitude:coords.latitude.toFixed(6), longitude:coords.longitude.toFixed(6)}});
      if (generation !== locationGeneration) return;
      state.profile = profile; lastLocationPost = Date.now();
      point('driver', deviceCoords?.latitude ?? coords.latitude, deviceCoords?.longitude ?? coords.longitude);
      $('#gps-status').textContent = 'Location updated. Live device location is being shared with your rider.';
      populate($('#driver-location-form'), profile);
    } finally { locationPosting = false; }
  }
  async function startDeviceLocation(share = config.user.role === 'DRIVER' && Boolean(state.profile)) {
    if (locationStart) return locationStart;
    locationStart = beginDeviceLocation(share);
    try { await locationStart; } finally { locationStart = null; }
  }
  async function beginDeviceLocation(share) {
    const generation = locationGeneration;
    if (!navigator.geolocation || !window.isSecureContext) throw new Error('Live location needs HTTPS (or localhost) and browser location access.');
    if (deviceWatch !== null) {
      if (share && !state.sharing) { await sendLocation(); state.sharing = true; $('#share-location').textContent = 'Stop sharing'; }
      return;
    }
    const coords = await gps();
    if (generation !== locationGeneration || state.closing) return;
    displayDevice(coords);
    if (share) await sendLocation(coords);
    if (generation !== locationGeneration || state.closing) return;
    state.sharing = share;
    if ($('#share-location')) $('#share-location').textContent = share ? 'Stop sharing' : 'Share location';
    $('#device-location').textContent = 'Stop location';
    $('#device-location').setAttribute('aria-pressed', 'true');
    $('#follow-device').hidden = false;
    setFollowDevice(true);
    deviceWatch = navigator.geolocation.watchPosition(position => {
      if (generation !== locationGeneration || deviceWatch === null) return;
      displayDevice(position.coords);
      if (state.sharing && Date.now() - lastLocationPost >= 5000) sendLocation(position.coords).catch(error => { $('#gps-status').textContent = error.message; });
    }, error => {
      if (generation !== locationGeneration) return;
      if (error.code === 1) stopSharingLocation();
      $('#device-location-status').textContent = error.code === 1 ? 'Location permission denied. Enable it in your browser and try again.' : 'GPS signal unavailable. Waiting for a fresh location.';
    }, {enableHighAccuracy:true, timeout:15000, maximumAge:0});
    clearInterval(state.locationTimer);
    state.locationTimer = setInterval(async () => {
      if (locationTickBusy) return;
      locationTickBusy = true;
      const generation = locationGeneration;
      try {
        if (Date.now() - deviceFixAt > 20000) {
          const fresh = await gps();
          if (generation !== locationGeneration || deviceWatch === null) return;
          displayDevice(fresh);
        }
        if (state.sharing && Date.now() - lastLocationPost >= 5000) await sendLocation();
      } catch (error) { if (generation === locationGeneration) $('#device-location-status').textContent = error.message; }
      finally { locationTickBusy = false; }
    }, 5000);
  }
  async function shareLocation() { await startDeviceLocation(true); }
  function stopSharingLocation() {
    locationGeneration++;
    clearInterval(state.locationTimer);
    if (deviceWatch !== null) navigator.geolocation.clearWatch(deviceWatch);
    deviceWatch = null; state.sharing = false; deviceCoords = null;
    cancelAnimationFrame(markerAnimations.device);
    if (markers.device) { map?.removeLayer(markers.device); delete markers.device; }
    if (deviceAccuracy) { map?.removeLayer(deviceAccuracy); deviceAccuracy = null; }
    setFollowDevice(false);
    $('#device-location').textContent = 'Show my location';
    $('#device-location').setAttribute('aria-pressed', 'false');
    $('#follow-device').hidden = true;
    $('#device-location-status').textContent = 'Location is off.';
    if ($('#share-location')) $('#share-location').textContent = 'Share location';
    if ($('#gps-status')) $('#gps-status').textContent = 'Location sharing is off.';
  }
  async function changeTab(tab) {
    state.tab = tab;
    document.querySelectorAll('.tab-panel').forEach(node => { node.hidden = node.id !== `tab-${tab}`; });
    document.querySelectorAll('[data-tab]').forEach(node => {
      const selected = node.dataset.tab === tab;
      node.classList.toggle('active', selected);
      node.setAttribute('aria-pressed', String(selected));
    });
    if (tab === 'ride') setTimeout(() => map?.invalidateSize(), 30);
    if (tab === 'history') await loadHistory();
    if (tab === 'wallet') await loadWallet();
    if (tab === 'earnings') await loadEarnings();
    if (tab === 'notifications') await loadNotifications();
    if (tab === 'stats') await loadStats();
    if (tab === 'payouts') await loadPayouts();
    if (tab === 'account' && config.user.role === 'RIDER') await Promise.all([loadFavouriteDrivers(), loadLostItems()]);
    if (tab === 'account' && config.user.role === 'DRIVER') await loadDriverDocuments();
  }
  function setDriverProfile(profile) {
    state.profile = profile; state.online = Boolean(profile?.is_online);
    const approved = profile?.verification_status === 'APPROVED';
    $('#driver-review').textContent = profile ? ({APPROVED:'Approved',PENDING:'Awaiting review',REJECTED:'Needs changes'}[profile.verification_status]) : 'Profile needed';
    $('#driver-review').className = `pill ${approved ? 'success' : 'warning'}`;
    $('#driver-guidance').textContent = profile?.review_note || (approved ? 'Share your current location, then go online to receive nearby requests.' : 'Add your license and vehicle details in My profile. An administrator will review them.');
    $('#toggle-online').disabled = !approved && !state.online;
    $('#toggle-online').dataset.restDisabled = String(!approved && !state.online);
    $('#toggle-online').textContent = state.online ? 'Go offline' : 'Go online';
    $('#availability-badge').textContent = state.online ? 'Online' : 'Offline';
    $('#availability-badge').className = `pill ${state.online ? 'success' : ''}`;
    $('#profile-review-note').textContent = profile?.review_note || 'Changes to driver or vehicle details require a new review.';
    if (profile?.latitude != null && !state.sharing) point('driver', profile.latitude, profile.longitude);
    renderReadiness();
  }
  function resetPlaceEditor() {
    editingPlaceId = null;
    $('#save-place-form').reset();
    $('#save-place-heading').textContent = 'Add a place';
    $('#save-place-form [type="submit"]').textContent = 'Save place';
    $('#cancel-place-edit').hidden = true;
  }
  async function prepareBooking() {
    await sync();
    if (state.active) throw new Error('Finish or cancel your current trip before planning another.');
    state.selected = null;
    history.replaceState({}, '', '/app/');
    renderTrip(null);
    invalidateQuote();
  }
  function renderBookingStops() {
    $('#booking-stops').hidden = !bookingStops.length;
    $('#booking-stops-summary').textContent = `${bookingStops.length} of 8 stops · visited in this order`;
    $('#add-booking-stop').disabled = bookingStops.length >= 8;
    $('#booking-stops-list').innerHTML = bookingStops.map((stop, i) => `<li><span class="stop-number">${i + 1}</span><strong>${esc(stop.label)}</strong><div class="stop-actions"><button type="button" data-stop-up="${i}" aria-label="Move stop ${i + 1} earlier" ${i === 0 ? 'disabled' : ''}>↑</button><button type="button" data-stop-down="${i}" aria-label="Move stop ${i + 1} later" ${i === bookingStops.length - 1 ? 'disabled' : ''}>↓</button><button type="button" data-stop-remove="${i}" aria-label="Remove stop ${i + 1}">×</button></div></li>`).join('');
  }
  function updateStops() {
    renderBookingStops(); invalidateQuote(); saveBookingRoute();
    const route = Object.fromEntries(new FormData($('#quote-form')));
    if (route.pickup_latitude && route.destination_latitude) drawRoute({...route, stops:bookingStops}, true);
  }
  function setupStopPlanner() {
    const dialog = $('#stop-dialog');
    if (!dialog) return;
    const input = $('#stop-search'), results = $('#stop-search-results');
    let timer, controller, version = 0;
    const cancel = () => { clearTimeout(timer); controller?.abort(); version++; };
    async function search() {
      controller?.abort(); controller = new AbortController();
      const request = ++version;
      results.innerHTML = '<p class="form-hint">Finding places…</p>';
      try {
        const places = await api(`rides/places/search/?q=${encodeURIComponent(input.value.trim())}`, {signal:controller.signal});
        if (request !== version || !dialog.open) return;
        results.innerHTML = places.length ? places.map((place, i) => `<button type="button" data-place-index="${i}" class="command-option"><span><strong>${esc(place.label)}</strong><small>${esc(place.address)}</small></span><span aria-hidden="true">+</span></button>`).join('') : '<p class="empty-state">No places found. Try a nearby landmark.</p>';
        results.querySelectorAll('[data-place-index]').forEach(button => button.addEventListener('click', () => {
          if (bookingStops.length >= 8 || state.active || state.selected) return;
          const place = places[Number(button.dataset.placeIndex)];
          bookingStops.push({label:place.label, latitude:place.latitude, longitude:place.longitude});
          updateStops(); dialog.close(); $('#add-booking-stop').focus();
        }));
      } catch (error) {
        if (request === version && error.name !== 'AbortError') results.innerHTML = '<p class="empty-state">Could not load places. Try searching again.</p>';
      }
    }
    $('#add-booking-stop').addEventListener('click', () => { input.value = ''; dialog.showModal(); input.focus(); search(); });
    input.addEventListener('input', () => { cancel(); results.innerHTML = '<p class="form-hint">Finding places…</p>'; timer = setTimeout(search, 300); });
    dialog.addEventListener('close', cancel);
    $('#booking-stops-list').addEventListener('click', event => {
      const button = event.target.closest('button');
      if (!button) return;
      if (button.dataset.stopRemove !== undefined) bookingStops.splice(Number(button.dataset.stopRemove), 1);
      else {
        const i = Number(button.dataset.stopUp ?? button.dataset.stopDown);
        const j = i + (button.dataset.stopUp !== undefined ? -1 : 1);
        if (j < 0 || j >= bookingStops.length) return;
        [bookingStops[i], bookingStops[j]] = [bookingStops[j], bookingStops[i]];
      }
      updateStops(); $('#add-booking-stop').focus();
    });
  }
  async function loadRecentTrips(force = false) {
    if (!$('#recent-trips') || recentTripsLoading || (!force && Date.now() - recentTripsAt < 60000)) return;
    recentTripsLoading = true; recentTripsAt = Date.now();
    try {
      const data = await api('rides/?status=COMPLETED');
      $('#retry-recent-trips').hidden = true;
      $('#recent-trips').innerHTML = data.results.length ? data.results.slice(0, 3).map(ride => `<article class="recent-journey"><span class="recent-journey-icon" aria-hidden="true">↗</span><div><strong>${esc(ride.quote.destination_label)}</strong><small>From ${esc(ride.quote.pickup_label)}</small><small>${esc(date(ride.completed_at || ride.created_at))} · ${esc(ride.quote.distance_km)} km</small></div><button type="button" class="button secondary small" data-recent-ride="${esc(ride.id)}">Ride again</button></article>`).join('') : '<div class="journey-empty"><span aria-hidden="true">↗</span><strong>Your next favourite route starts here.</strong><p>Completed trips will appear here so you can plan them again in a tap.</p></div>';
      $('#recent-trips').querySelectorAll('[data-recent-ride]').forEach(button => button.addEventListener('click', () => busy(button, () => rideAgain(button.dataset.recentRide))));
    } catch (_) {
      $('#recent-trips').innerHTML = '<p class="empty-state">Your recent journeys could not be loaded.</p>';
      $('#retry-recent-trips').hidden = false;
    } finally { recentTripsLoading = false; }
  }
  function renderReadiness() {
    if (!$('#driver-readiness-list')) return;
    const profile = state.profile;
    const checks = [
      [Boolean(profile?.license_number && profile.license_expiry_date >= new Date().toISOString().slice(0, 10)), 'Driving licence', 'Add a valid licence in your profile'],
      [Boolean(state.vehicle), 'Vehicle details', 'Register your vehicle in your profile'],
      [profile?.verification_status === 'APPROVED', 'Administrator approval', 'Your profile needs approval before going online'],
      [Boolean(profile?.location_updated_at && Date.now() - new Date(profile.location_updated_at).getTime() < 90000), 'Current location', 'Share your location to receive nearby requests'],
    ];
    $('#readiness-count').textContent = `${checks.filter(([ready]) => ready).length} / ${checks.length} ready`;
    $('#driver-readiness-list').innerHTML = checks.map(([ready, label, hint]) => `<li class="${ready ? 'ready' : ''}"><span aria-hidden="true">${ready ? '✓' : '○'}</span><div><strong>${label}</strong><small>${ready ? 'Ready' : hint}</small></div></li>`).join('');
  }
  async function updateJourneyProgress(ride) {
    const panel = $('#journey-progress');
    panel.hidden = !ride;
    const key = ride ? `${ride.id}:${ride.status}` : '';
    if (key !== estimateKey) { estimateKey = key; estimateAt = 0; estimateRequest++; }
    if (!ride) return;
    $('#journey-progress-title').textContent = labels[ride.status];
    const stage = {REQUESTED:0, SEARCHING:0, ACCEPTED:1, DRIVER_ARRIVING:1, DRIVER_ARRIVED:1, IN_PROGRESS:2, COMPLETED:3, CANCELLED:-1}[ride.status];
    $('#journey-progress-steps').innerHTML = ['Requested', 'Pickup', 'On the road', 'Arrived'].map((label, i) => `<li class="${i <= stage ? 'done' : ''}" ${i === stage ? 'aria-current="step"' : ''}><span>${i < stage ? '✓' : i + 1}</span>${label}</li>`).join('');
    const output = $('#journey-estimate');
    const show = (value, detail) => { output.innerHTML = `<strong>${esc(value)}</strong><span>${esc(detail)}</span>`; };
    if (ride.status === 'CANCELLED') { show('Cancelled', 'No active journey'); return; }
    if (ride.status === 'COMPLETED') { show(`${ride.quote.distance_km} km`, 'Booked road distance · Journey complete'); return; }
    if (ride.status === 'DRIVER_ARRIVED') { show('At pickup', 'Check the vehicle plate before getting in'); return; }
    if (!ride.driver) { show(`≈ ${ride.quote.estimated_minutes} min`, 'Booked trip estimate · Waiting for a driver'); return; }
    if (ride.status === 'IN_PROGRESS' && ride.quote.stops?.length) { show(`≈ ${ride.quote.estimated_minutes} min`, 'Full booked route including stops · Not remaining time'); return; }
    if (!ride.driver.location || ride.driver.location.age_seconds > 90) {
      estimateRequest++; estimateAt = 0;
      show('Waiting for GPS', 'A fresh driver location is needed for an arrival estimate'); return;
    }
    if (Date.now() - estimateAt < 15000) return;
    estimateAt = Date.now(); const request = ++estimateRequest;
    show('Updating…', 'Calculating the road route from your driver');
    try {
      const eta = await api(`rides/${ride.id}/eta/`);
      if (request !== estimateRequest || key !== estimateKey) return;
      show(`≈ ${eta.estimated_minutes} min`, `${eta.remaining_km} km ${ride.status === 'IN_PROGRESS' ? 'to destination' : 'to pickup'} · Updated ${new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}`);
    } catch (_) {
      if (request === estimateRequest) show('Estimate unavailable', 'We will retry the road estimate shortly');
    }
  }
  function setupQuickActions() {
    const dialog = $('#quick-actions-dialog'), search = $('#quick-actions-search'), results = $('#quick-actions-results');
    const actions = Array.from(document.querySelectorAll('.workspace-nav [data-tab]')).map(button => ({label:button.getAttribute('aria-label'), hint:'Open workspace', run:() => changeTab(button.dataset.tab)}));
    actions.push({label:'Safety centre', hint:'Trip safety and trusted contacts', run:() => $('#open-safety').click()}, {label:'Show my location', hint:'View your device on the map', run:async () => { await changeTab('ride'); if (deviceWatch === null) $('#device-location').click(); }});
    if (config.user.role === 'RIDER') actions.push({label:'Schedule a ride', hint:'Plan up to 7 days ahead', run:() => $('[data-shortcut="schedule"]').click()}, {label:'Saved places', hint:'Home, work and your favourite places', run:() => $('[data-shortcut="saved"]').click()});
    function render() {
      const matches = actions.filter(action => `${action.label} ${action.hint}`.toLowerCase().includes(search.value.trim().toLowerCase()));
      results.innerHTML = matches.length ? matches.map((action, i) => `<button type="button" class="command-option" data-command="${i}"><span><strong>${esc(action.label)}</strong><small>${esc(action.hint)}</small></span><span aria-hidden="true">↗</span></button>`).join('') : '<p class="empty-state">No matching actions. Try “trips” or “profile”.</p>';
      results.querySelectorAll('button').forEach(button => button.addEventListener('click', () => { dialog.close(); Promise.resolve().then(matches[Number(button.dataset.command)].run).catch(error => toast(error.message, true)); }));
    }
    const open = () => { if (document.querySelector('dialog[open]')) return; search.value = ''; render(); dialog.showModal(); search.focus(); };
    $('#open-quick-actions').addEventListener('click', open);
    search.addEventListener('input', render);
    document.addEventListener('keydown', event => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); open(); } });
    document.querySelectorAll('.command-dialog').forEach(node => node.addEventListener('keydown', event => {
      const buttons = Array.from(node.querySelectorAll('.command-results button'));
      if (!buttons.length) return;
      const i = buttons.indexOf(document.activeElement);
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault(); buttons[(i + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length].focus();
      } else if (event.key === 'Enter' && document.activeElement?.matches('input')) { event.preventDefault(); buttons[0].click(); }
    }));
    document.querySelectorAll('[data-close-dialog]').forEach(button => button.addEventListener('click', () => document.getElementById(button.dataset.closeDialog).close()));
    $('#recent-trips-history')?.addEventListener('click', () => busy(null, () => changeTab('history')));
    $('#retry-recent-trips')?.addEventListener('click', () => loadRecentTrips(true));
    $('#readiness-profile')?.addEventListener('click', () => busy(null, () => changeTab('account')));
  }
  async function rideAgain(rideId) {
    const ride = await api(`rides/${rideId}/`);
    if (!['COMPLETED', 'CANCELLED'].includes(ride.status)) throw new Error('Choose a completed or cancelled trip to ride again.');
    await prepareBooking();
    const form = $('#quote-form');
    for (const kind of ['pickup', 'destination']) {
      for (const field of ['label', 'latitude', 'longitude']) form.elements[`${kind}_${field}`].value = ride.quote[`${kind}_${field}`];
      form.elements[`${kind}_label`].dataset.generated = 'false';
    }
    form.elements.vehicle_class.value = ride.quote.vehicle_class || 'STANDARD';
    syncVehicleChoice();
    bookingStops = (ride.quote.stops || []).map(({label, latitude, longitude}) => ({label, latitude, longitude}));
    renderBookingStops();
    $('#booking-type-select').value = 'NOW';
    $('#schedule-picker-row').hidden = true;
    $('#schedule-datetime').value = '';
    $('#schedule-datetime').required = false;
    $('#schedule-datetime').disabled = true;
    document.querySelectorAll('.places-dropdown').forEach(node => { node.hidden = true; });
    drawRoute(ride.quote, true);
    await changeTab('ride');
    form.querySelector('[type="submit"]').focus();
    toast('Route copied. Check the details and get a fresh fare before booking.');
  }
  async function loadSavedPlaces(page = savedPlacesPage) {
    if (config.user.role !== 'RIDER') return;
    const request = ++savedPlacesRequest;
    const data = await api(`rides/saved-places/?page=${page}`);
    if (request !== savedPlacesRequest) return;
    savedPlacesPage = page;
    const places = data.results;
    for (const selector of ['#saved-places-list', '#booking-saved-places-list']) {
      const list = $(selector);
      const canEdit = selector === '#saved-places-list';
      list.innerHTML = places.length ? places.map(place => `<article class="saved-place-row">
        <div><strong>${esc(place.label)}</strong><p class="muted">${esc(place.address)}</p></div>
        <div class="saved-place-actions">
          <button type="button" class="button secondary small" data-place="${place.id}" data-use-place="pickup">Pickup</button>
          <button type="button" class="button secondary small" data-place="${place.id}" data-use-place="destination">Destination</button>
          ${canEdit ? `<button type="button" class="plain-link" data-edit-place="${place.id}">Edit</button><button type="button" class="plain-link" data-delete-place="${place.id}">Delete</button>` : ''}
        </div></article>`).join('') : '<p class="muted">No saved places yet. Add one in My profile.</p>';
      if (data.previous || data.next) list.innerHTML += `<div class="pagination"><button type="button" class="button secondary small" data-place-page="${page - 1}" ${data.previous ? '' : 'disabled'}>Previous places</button><span>Page ${page}</span><button type="button" class="button secondary small" data-place-page="${page + 1}" ${data.next ? '' : 'disabled'}>Next places</button></div>`;
      list.querySelectorAll('[data-use-place]').forEach(button => button.addEventListener('click', () => busy(button, async () => {
        const place = places.find(item => item.id === Number(button.dataset.place));
        await prepareBooking();
        await choosePoint(button.dataset.usePlace, place.latitude, place.longitude, place.address);
        await changeTab('ride');
        $('#quote-form').scrollIntoView({block:'nearest'});
        toast(`${place.label} selected as ${button.dataset.usePlace}.`);
      })));
      list.querySelectorAll('[data-edit-place]').forEach(button => button.addEventListener('click', () => {
        const place = places.find(item => item.id === Number(button.dataset.editPlace));
        editingPlaceId = place.id;
        populate($('#save-place-form'), place);
        $('#save-place-heading').textContent = 'Edit saved place';
        $('#save-place-form [type="submit"]').textContent = 'Save changes';
        $('#cancel-place-edit').hidden = false;
        $('#save-place-form [name="label"]').focus();
      }));
      list.querySelectorAll('[data-delete-place]').forEach(button => button.addEventListener('click', () => busy(button, async () => {
        const id = Number(button.dataset.deletePlace);
        await api(`rides/saved-places/${id}/`, {method:'DELETE'});
        if (editingPlaceId === id) resetPlaceEditor();
        await loadSavedPlaces(1);
        toast('Saved place deleted.');
      })));
      list.querySelectorAll('[data-place-page]').forEach(button => button.addEventListener('click', () => busy(button, () => loadSavedPlaces(Number(button.dataset.placePage)))));
    }
  }
  async function loadFavouriteDrivers(page = favouritePage) {
    if (config.user.role !== 'RIDER') return;
    const request = ++favouriteRequest;
    const data = await api(`rides/favourite-drivers/?page=${page}`);
    if (request !== favouriteRequest) return;
    favouritePage = page;
    const list = $('#favourite-drivers-list');
    list.innerHTML = data.results.length ? data.results.map(driver => `<article class="saved-place-row">
      <div class="panel-heading"><div><strong>${esc(driver.driver_name)}</strong><p class="muted">${driver.driver_rating == null ? 'No ratings yet' : Number(driver.driver_rating).toFixed(1) + ' / 5 rider rating'}</p></div>
      <button type="button" class="plain-link" data-remove-favourite="${driver.id}" aria-label="Remove ${esc(driver.driver_name)} from favourites">Remove</button></div></article>`).join('') : '<p class="muted">No favourite drivers yet. Save a driver from a completed trip.</p>';
    if (data.previous || data.next) list.innerHTML += `<div class="pagination"><button type="button" class="button secondary small" data-favourite-page="${page - 1}" ${data.previous ? '' : 'disabled'}>Previous drivers</button><span>Page ${page}</span><button type="button" class="button secondary small" data-favourite-page="${page + 1}" ${data.next ? '' : 'disabled'}>Next drivers</button></div>`;
    list.querySelectorAll('[data-remove-favourite]').forEach(button => button.addEventListener('click', () => busy(button, async () => {
      const favourite = data.results.find(item => item.id === Number(button.dataset.removeFavourite));
      await api(`rides/favourite-drivers/${button.dataset.removeFavourite}/`, {method:'DELETE'});
      const saveButton = $('#save-favourite-driver');
      if (saveButton?.dataset.driverId === String(favourite.driver_id)) {
        saveButton.disabled = false; saveButton.dataset.restDisabled = 'false'; saveButton.textContent = 'Save driver to favourites';
      }
      await loadFavouriteDrivers(1);
      toast('Driver removed from favourites.');
    })));
    list.querySelectorAll('[data-favourite-page]').forEach(button => button.addEventListener('click', () => busy(button, () => loadFavouriteDrivers(Number(button.dataset.favouritePage)))));
  }
  async function loadLostItems(page = lostItemPage) {
    if (config.user.role !== 'RIDER') return;
    const request = ++lostItemRequest;
    const data = await api(`rides/lost-items/?page=${page}`);
    if (request !== lostItemRequest) return;
    lostItemPage = page;
    const list = $('#lost-items-list');
    const statuses = {REPORTED:'Reported', CONTACTING:'Contacting driver', FOUND:'Found', RETURNED:'Returned', NOT_FOUND:'Not found'};
    list.innerHTML = data.results.length ? data.results.map(report => `<article class="support-report">
      <div class="panel-heading"><strong>Report #${report.id}</strong><span class="pill ${report.status === 'RETURNED' ? 'success' : ''}">${esc(statuses[report.status] || report.status)}</span></div>
      <p class="support-description">${esc(report.description)}</p><p class="muted">${esc(report.pickup_label)} to ${esc(report.destination_label)}</p>
      <p class="form-hint">Reported ${esc(date(report.created_at))}${report.resolved_at ? '<br>Resolved ' + esc(date(report.resolved_at)) : ''}</p>
      <a class="plain-link" href="/app/?ride=${encodeURIComponent(report.ride)}">View trip</a></article>`).join('') : '<p class="muted">No lost-item reports. Open a completed trip to report something you left behind.</p>';
    if (data.previous || data.next) list.innerHTML += `<div class="pagination"><button type="button" class="button secondary small" data-lost-page="${page - 1}" ${data.previous ? '' : 'disabled'}>Previous reports</button><span>Page ${page}</span><button type="button" class="button secondary small" data-lost-page="${page + 1}" ${data.next ? '' : 'disabled'}>Next reports</button></div>`;
    list.querySelectorAll('[data-lost-page]').forEach(button => button.addEventListener('click', () => busy(button, () => loadLostItems(Number(button.dataset.lostPage)))));
  }
  async function loadEmergencyContacts() {
    const list = $('#emergency-contacts-list, #driver-emergency-contacts-list');
    if (!list) return;
    const contacts = await api('auth/emergency-contacts/');
    list.innerHTML = contacts.length ? contacts.map(contact => `
      <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--line);">
        <div><strong>${esc(contact.name)}</strong><div class="muted" style="font-size:12px;">${esc(contact.relationship || 'Trusted contact')} · ${esc(contact.phone_number)}</div></div>
        <button type="button" class="plain-link delete-emergency-contact" data-id="${contact.id}" aria-label="Remove ${esc(contact.name)}" style="color:var(--error,#d32f2f);font-size:12px;">Remove</button>
      </div>
    `).join('') : '<p class="muted" style="font-size:13px;">No emergency contacts saved.</p>';
    list.querySelectorAll('.delete-emergency-contact').forEach(button => {
      button.addEventListener('click', () => busy(button, async () => {
        await api(`auth/emergency-contacts/${button.dataset.id}/`, {method:'DELETE'});
        toast('Emergency contact removed.');
        await loadEmergencyContacts();
      }));
    });
  }
  async function loadAccount() {
    const user = await api('auth/me/');
    populate($('#account-form'), user); $('#account-identity').textContent = `${user.username} · ${user.email}`;
    if ($('#account-security-question') && user.security_question) {
      $('#account-security-question').value = user.security_question;
    }
    try {
      const ref = await api('auth/referral/', {optional:true});
      if (ref && $('#referral-code-display')) {
        $('#referral-code-display').textContent = ref.referral_code || 'None';
        $('#referral-stats-display').textContent = `Earned credits: ${amount(ref.total_rewards_earned)} · ${ref.total_referrals} friends referred (${ref.completed_referrals} completed)`;
      }
    } catch (_) {}
    if (config.user.role === 'RIDER') {
      await Promise.all([loadSavedPlaces(), loadEmergencyContacts(), loadFavouriteDrivers(), loadLostItems()]);
    }
    if (config.user.role === 'DRIVER') {
      const [profile, vehicle] = await Promise.all([api('drivers/profile/', {optional:true}), api('drivers/vehicle/', {optional:true})]);
      state.vehicle = vehicle; setDriverProfile(profile);
      populate($('#driver-profile-form'), profile); populate($('#vehicle-form'), vehicle); populate($('#driver-location-form'), profile);
      $('#vehicle-form').elements.year.max = new Date().getFullYear() + 1;
      $('#driver-profile-form').elements.license_expiry_date.min = new Date().toISOString().slice(0,10);
      await loadEmergencyContacts();
    }
  }
  function syncChat(ride) {
    const panel = $('#trip-chat-panel');
    const available = Boolean(ride?.driver && ride?.rider);
    panel.hidden = !available;
    if (chatRide?.id !== ride?.id) {
      if (chatRide) chatDrafts.set(chatRide.id, $('#trip-chat-input').value);
      chatRequest++; chatLoading = null; chatSignature = ''; chatUnread = 0;
      chatMessageIds = new Set();
      $('#trip-chat-log').replaceChildren();
      $('#trip-chat-input').value = chatDrafts.get(ride?.id) || '';
      $('#chat-unread').hidden = true;
      $('#chat-retry').hidden = true;
    }
    chatRide = available ? ride : null;
    if (!chatRide) return;
    const writable = ['ACCEPTED','DRIVER_ARRIVING','DRIVER_ARRIVED','IN_PROGRESS'].includes(ride.status);
    $('#trip-chat-title').textContent = config.user.role === 'RIDER' ? 'Messages with your driver' : 'Messages with your rider';
    $('#trip-chat-form').hidden = !writable;
    $('#trip-chat-status').textContent = writable ? 'Messages update live while your trip is active.' : 'This ride has ended. Your messages are available to read.';
  }
  async function loadTripMessages(force = false) {
    const ride = chatRide;
    if (!ride || (chatLoading === ride.id && !force)) return;
    const request = ++chatRequest;
    chatLoading = ride.id;
    try {
      const messages = await api(`rides/${ride.id}/messages/`);
      if (request !== chatRequest || chatRide?.id !== ride.id) return;
      $('#chat-retry').hidden = true;
      const signature = JSON.stringify(messages.map(message => message.id));
      if (signature === chatSignature) return;
      const log = $('#trip-chat-log');
      const wasAtBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 50;
      const initial = chatSignature === '';
      const additions = messages.filter(message => !chatMessageIds.has(message.id));
      if (!chatMessageIds.size) log.replaceChildren();
      if (!messages.length) log.innerHTML = '<p class="muted">No messages yet. Say hello or share your pickup details.</p>';
      for (const message of additions) {
        const own = message.sender === config.user.id;
        const bubble = document.createElement('article');
        bubble.className = `chat-message${own ? ' own' : ''}`;
        bubble.innerHTML = `<small>${esc(own ? 'You' : message.sender_name)} · ${esc(date(message.created_at))}</small><p>${esc(message.content)}</p>`;
        log.append(bubble);
        chatMessageIds.add(message.id);
        if (!own && !initial && !$('#trip-chat-panel').open) chatUnread++;
      }
      chatSignature = signature;
      $('#chat-unread').hidden = chatUnread === 0;
      $('#chat-unread').textContent = `${chatUnread} new`;
      if (initial || wasAtBottom) log.scrollTop = log.scrollHeight;
    } catch (error) {
      if (request !== chatRequest) return;
      $('#trip-chat-status').textContent = `Messages could not load. ${error.message}`;
      $('#chat-retry').hidden = false;
    } finally { if (request === chatRequest) chatLoading = null; }
  }
  let pendingCancellation = null;
  function renderTrip(ride) {
    updateJourneyProgress(ride);
    syncChat(ride);
    drawPickupRoute(ride);
    const panel = $('#trip-panel'); panel.hidden = !ride;
    if ($('#booking-panel')) $('#booking-panel').hidden = Boolean(ride || state.active);
    $('#return-active-trip').hidden = !state.active || state.active.id === ride?.id;
    if (!ride) {
      if (tripSignature) {
        clearRoute();
        for (const kind of ['pickup', 'destination', ...(config.user.role === 'RIDER' ? ['driver'] : [])]) {
          if (markers[kind]) { map?.removeLayer(markers[kind]); delete markers[kind]; }
        }
        $('#map-instruction').textContent = config.user.role === 'RIDER' ? 'Click the map to choose your pickup' : 'Your city, at a glance';
        state.pick = 'pickup';
      }
      tripSignature = ''; return;
    }
    const signature = JSON.stringify({...ride, driver:ride.driver ? {...ride.driver, location:undefined} : null});
    drawRoute(ride.quote, tripSignature === '' || !tripSignature.includes(ride.id));
    if (ride.driver?.location) {
      if (!(config.user.role === 'DRIVER' && state.sharing)) point('driver', ride.driver.location.latitude, ride.driver.location.longitude);
      $('#map-instruction').textContent = ride.driver.location.age_seconds > 90 ? 'Driver location is out of date. Waiting for an update.' : `Driver location updated ${ride.driver.location.age_seconds}s ago`;
    } else {
      if (markers.driver && config.user.role === 'RIDER') { map?.removeLayer(markers.driver); delete markers.driver; }
      $('#map-instruction').textContent = labels[ride.status];
    }
    if (signature === tripSignature) return;
    tripSignature = signature;
    const completed = ride.status === 'COMPLETED', cancelled = ride.status === 'CANCELLED';
    const isActive = ['REQUESTED','SEARCHING','ACCEPTED','DRIVER_ARRIVING','DRIVER_ARRIVED','IN_PROGRESS'].includes(ride.status);
    const partner = config.user.role === 'RIDER' ? ride.driver : ride.rider;
    let person = '';
    if (partner) {
      const vehicle = partner.vehicle;
      person = `<div class="trip-person"><strong>${esc(partner.name)} ${partner.rating ? ' · ★ ' + Number(partner.rating).toFixed(1) : ''}</strong>${vehicle ? `<p>${esc(vehicle.color)} ${esc(vehicle.make)} ${esc(vehicle.model)}</p><p><strong>${esc(vehicle.license_plate)}</strong></p>` : ''}${partner.phone_number ? `<a href="tel:${esc(partner.phone_number)}">Call ${esc(partner.phone_number)}</a>` : ''}${ride.driver ? '<button type="button" id="open-trip-messages" class="button secondary small">Messages</button>' : ''}</div>`;
    }
    const actions = {ACCEPTED:['arriving','Head to pickup'], DRIVER_ARRIVING:['arrived','Mark as arrived'], DRIVER_ARRIVED:['start','Start trip'], IN_PROGRESS:['complete','Complete trip']};
    let actionButton = '';
    if (config.user.role === 'DRIVER' && actions[ride.status]) { const [action, label] = actions[ride.status]; actionButton = `<button class="button full" data-ride-action="${action}">${label} →</button>`; }
    const canCancel = !['IN_PROGRESS','COMPLETED','CANCELLED'].includes(ride.status);
    let payment = '';
    if (ride.payment) payment = `<div class="receipt"><p><strong>${amount(ride.payment.amount, ride.payment.currency)}</strong> · ${esc(paymentNames[ride.payment.method] || ride.payment.method)}</p><small>${ride.payment.status === 'REFUNDED' ? 'Demo payment refunded in full' : ride.payment.status === 'PAID' ? (ride.payment.is_demo ? 'Prepaid with demo credits · ' : 'Paid · ') + esc(date(ride.payment.paid_at)) : 'Payment pending'}</small>${ride.payment.is_demo ? '<p class="demo-notice">Demo only · no real money charged. This trip does not add to payable driver earnings.</p>' : ''}${ride.payment.status !== 'PAID' && config.user.role === 'DRIVER' && ride.payment.method === 'CASH' ? '<button class="button secondary full" data-payment="cash">Confirm cash received</button>' : ''}${ride.payment.status !== 'PAID' && config.user.role === 'RIDER' && ride.payment.method === 'CARD' ? '<button class="button full" data-payment="checkout">Pay securely by card ↗</button>' : ''}${completed ? `<a class="plain-link receipt-link" href="/app/rides/${esc(ride.id)}/receipt/">${ride.payment.is_demo ? 'View demo receipt' : ride.payment.status === 'PAID' ? 'View receipt' : 'View trip summary'} ↗</a>` : ''}</div>`;
    const rated = ride.ratings.some(rating => rating.author === config.user.id);
    const rating = completed && !rated ? `<form id="rating-form" class="rating-form"><h3>How was your journey?</h3><fieldset class="star-rating"><legend>Choose a rating</legend>${[1,2,3,4,5].map(score => `<label><input type="radio" name="score" value="${score}" aria-label="${score} star${score === 1 ? '' : 's'}" required><span aria-hidden="true">★</span></label>`).join('')}</fieldset><fieldset class="rating-tags"><legend>What stood out? (optional)</legend>${['Friendly','On time','Safe journey','Great communication'].map(tag => `<label><input type="checkbox" name="feedback_tags" value="${tag}"><span>${tag}</span></label>`).join('')}</fieldset><label>Your feedback<textarea name="comment" maxlength="500" placeholder="Anything you would like to share? (optional)"></textarea></label><button class="button secondary full" type="submit">Send rating</button></form>` : (rated ? `<p class="rating-thanks">★ You rated this trip ${esc(ride.ratings.find(item => item.author === config.user.id).score)}/5. Thanks for sharing your rating.</p>` : '');

    const sosButton = isActive ? `<div style="margin-top:10px;"><button type="button" id="sos-alert-btn" class="button danger small full" style="background:#c0392b;border-color:#c0392b;">🚨 Emergency SOS Alert</button><button type="button" id="share-trip-btn" class="button secondary small full" style="margin-top:8px;">Share live trip</button><div id="share-trip-link" class="form-hint" hidden></div></div>` : '';
    const tipSection = completed && config.user.role === 'RIDER' && !isDemoMethod(ride.payment_method) ? (ride.tip ? `<p class="form-hint" style="color:var(--success,#2e7d32);margin-top:8px;">★ Tip of ${amount(ride.tip, ride.quote.currency)} sent to driver.</p>` : `
      <div class="tip-section" style="margin-top:12px;padding:10px;background:var(--bg-subtle,#f8f9fa);border-radius:6px;">
        <span style="font-size:13px;font-weight:600;display:block;margin-bottom:6px;">Tip your driver (optional)</span>
        <div style="display:flex;gap:6px;">
          <button type="button" class="button secondary small tip-btn" data-tip="20">BDT 20</button>
          <button type="button" class="button secondary small tip-btn" data-tip="50">BDT 50</button>
          <button type="button" class="button secondary small tip-btn" data-tip="100">BDT 100</button>
        </div>
      </div>
    `) : '';

    $('#trip-content').innerHTML = `<div class="trip-title"><h2>${esc(labels[ride.status])}</h2><div style="display:flex;gap:6px;align-items:center;"><span class="pill ${completed ? 'success' : cancelled ? 'error' : ''}">${esc(ride.status.toLowerCase().replaceAll('_',' '))}</span><span class="pill success" style="font-size:11px;">Private ride</span></div></div><div class="route-summary"><small>PICKUP</small>${esc(ride.quote.pickup_label)}${(ride.quote.stops || []).map((stop, index) => `<small>STOP ${index + 1}</small>${esc(stop.label)}`).join('')}<small>DESTINATION</small>${esc(ride.quote.destination_label)}</div><div class="fare-meta"><span>${amount(ride.quote.fare, ride.quote.currency)}</span><span>${esc(ride.quote.distance_km)} km est.</span>${ride.quote.vehicle_class ? `<span class="pill" style="font-size:11px;">${esc(ride.quote.vehicle_class)}</span>` : ''}</div>${person}${cancelled ? `<p class="muted">${esc(ride.cancellation_reason || 'This ride was cancelled.')}</p>` : ''}<ol class="timeline">${ride.events.map(event => `<li>${esc(labels[event.status])}<time>${esc(date(event.created_at))}</time></li>`).join('')}</ol>${actionButton}${canCancel ? '<form id="cancel-form" class="cancel-form"><label>Reason for cancelling (optional)<input name="reason" maxlength="300" placeholder="Let us know what changed"></label><button type="submit" class="button danger full">Cancel ride</button></form>' : ''}${sosButton}${payment}${tipSection}${rating}${completed || cancelled ? '<button class="plain-link" id="close-trip">Back to your next journey →</button>' : ''}`;
    if (config.user.role === 'RIDER' && (completed || cancelled)) {
      const again = document.createElement('button'); again.type = 'button'; again.className = 'button secondary full';
      again.textContent = 'Ride again'; again.id = 'ride-again';
      again.addEventListener('click', () => busy(again, () => rideAgain(ride.id)));
      $('#close-trip').before(again);
    }
    if (config.user.role === 'RIDER' && completed) {
      const support = document.createElement('section'); support.className = 'trip-support';
      support.innerHTML = `${ride.driver ? '<button type="button" id="save-favourite-driver" class="button secondary full">Save driver to favourites</button>' : ''}
        <details><summary>Left something behind?</summary>
          <form id="lost-item-form"><label>Describe your lost item<textarea name="description" maxlength="500" rows="3" placeholder="What did you leave, and where were you sitting?" required></textarea></label><button type="submit" class="button secondary small">Report lost item</button></form>
        </details><button type="button" id="view-lost-items" class="plain-link">View my lost-item reports</button>`;
      $('#close-trip').before(support);
      if ($('#save-favourite-driver')) $('#save-favourite-driver').dataset.driverId = String(ride.driver.id);
      $('#save-favourite-driver')?.addEventListener('click', event => busy(event.currentTarget, async () => {
        const button = event.currentTarget;
        await api(`rides/${ride.id}/favourite-driver/`, {method:'POST', data:{}});
        button.textContent = 'Driver saved'; button.dataset.restDisabled = 'true';
        await loadFavouriteDrivers(1);
        toast('Driver saved to your favourites in My profile.');
      }));
      onForm('#lost-item-form', async (data, form) => {
        await api(`rides/${ride.id}/lost-items/`, {method:'POST', data});
        form.reset();
        await loadLostItems(1);
        toast('Report received. Track its progress in My profile.');
      });
      $('#view-lost-items').addEventListener('click', event => busy(event.currentTarget, async () => {
        await changeTab('account'); $('#lost-items-panel').scrollIntoView({block:'start'});
      }));
    }
    $('#trip-content').querySelectorAll('[data-ride-action]').forEach(button => button.addEventListener('click', () => busy(button, async () => {
      state.selected = await api(`rides/${ride.id}/actions/`, {method:'POST', data:{action:button.dataset.rideAction}}); await sync();
    })));
    onForm('#cancel-form', async data => {
      const preview = await api(`rides/${ride.id}/actions/`);
      pendingCancellation = preview;
      $('#cancellation-fee').textContent = amount(preview.fee, preview.currency);
      $('#cancellation-policy').textContent = preview.refund_note;
      $('#cancellation-reason').value = data.reason || '';
      $('#cancellation-error').textContent = '';
      $('#cancellation-dialog').showModal();
      $('#keep-ride').focus();
    });
    onForm('#rating-form', async (data, form) => {
      await api(`rides/${ride.id}/rating/`, {method:'POST', data:{score:Number(data.score), comment:data.comment, feedback_tags:new FormData(form).getAll('feedback_tags')}}); toast('Thank you for your feedback.'); await sync();
    });
    $('#trip-content').querySelectorAll('[data-payment]').forEach(button => button.addEventListener('click', () => busy(button, async () => {
      const result = await api(`payments/${ride.id}/${button.dataset.payment}/`, {method:'POST', data:{}});
      if (result.url) { location.assign(result.url); return; }
      toast('Payment recorded.'); await sync();
    })));
    $('#trip-content').querySelectorAll('.tip-btn').forEach(button => button.addEventListener('click', () => busy(button, async () => {
      await api(`rides/${ride.id}/tip/`, {method:'POST', data:{amount:button.dataset.tip}});
      toast(`Driver tip of BDT ${button.dataset.tip} added!`); await sync();
    })));
    $('#sos-alert-btn')?.addEventListener('click', event => busy(event.currentTarget, async () => {
      const msg = prompt('Emergency SOS: Enter details of your situation (or click OK to dispatch SOS immediately):');
      if (msg === null) return;
      await api(`rides/${ride.id}/sos/`, {method:'POST', data:{message:msg || 'Emergency assistance requested.'}});
      toast('🚨 Emergency SOS alert broadcasted! Support has been notified.', true);
    }));
    $('#share-trip-btn')?.addEventListener('click', event => busy(event.currentTarget, async () => {
      const shareButton = event.currentTarget;
      const share = await api(`rides/${ride.id}/share/`, {method:'POST', data:{}});
      shareButton.hidden = true;
      const sharePanel = $('#share-trip-link');
      sharePanel.replaceChildren();
      sharePanel.hidden = false;
      const link = document.createElement('a');
      link.href = share.share_url;
      link.textContent = 'Open live trip link';
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      const stop = document.createElement('button');
      stop.type = 'button';
      stop.className = 'plain-link';
      stop.textContent = 'Stop sharing';
      stop.addEventListener('click', () => busy(stop, async () => {
        await api(`rides/share-links/${share.id}/`, {method:'DELETE'});
        sharePanel.hidden = true;
        shareButton.hidden = false;
        toast('Trip sharing stopped.');
      }));
      sharePanel.append(link, document.createTextNode(' · '), stop, document.createTextNode(' · Expires in six hours.'));
      try {
        await navigator.clipboard.writeText(share.share_url);
        toast('Live trip link copied. It expires in six hours.');
      } catch (_) {
        window.prompt('Copy this live trip link. It expires in six hours:', share.share_url);
      }
    }));
    $('#open-trip-messages')?.addEventListener('click', () => {
      $('#trip-chat-panel').open = true;
      $('#trip-chat-panel').scrollIntoView({block:'nearest', behavior:'smooth'});
      if (!$('#trip-chat-form').hidden) $('#trip-chat-input').focus({preventScroll:true});
      loadTripMessages();
    });
    loadTripMessages();
    $('#close-trip')?.addEventListener('click', () => { state.selected = state.active; history.replaceState({}, '', '/app/'); renderTrip(state.active); });
  }
  async function loadOffers() {
    if (config.user.role !== 'DRIVER') return;
    if (!state.online || state.active) { $('#offers-list').innerHTML = `<div class="empty-state">${state.active ? 'Your current trip is in progress.' : 'Go online to find your next trip.'}</div>`; return; }
    const offers = (await api('rides/offers/')).results;
    $('#offers-list').innerHTML = offers.length ? offers.map(offer => `<div class="offer-card"><div class="panel-heading"><strong>${amount(offer.quote.fare, offer.quote.currency)}</strong><small>${esc(offer.distance_km)} km to pickup</small></div><div class="route-summary">${esc(offer.quote.pickup_label)}<small>↓</small>${esc(offer.quote.destination_label)}</div><small class="muted">Offer expires ${esc(date(offer.expires_at))}</small><div class="button-row"><button class="button" data-offer="${offer.id}" data-action="accept">Accept ride</button><button class="button secondary" data-offer="${offer.id}" data-action="reject">Pass</button></div></div>`).join('') : '<div class="empty-state"><span class="empty-icon">↝</span>No nearby requests right now.<br>Keep your location sharing on.</div>';
    $('#offers-list').querySelectorAll('[data-offer]').forEach(button => button.addEventListener('click', () => busy(button, async () => {
      const result = await api(`rides/offers/${button.dataset.offer}/${button.dataset.action}/`, {method:'POST', data:{}});
      if (button.dataset.action === 'accept') { state.selected = result; toast('Ride accepted. Your rider has been notified.'); }
      await sync();
    })));
  }
  async function loadHistory(page = state.page, preserveEditor = false) {
    // Polling must not discard a date the rider is currently editing.
    if (preserveEditor && $('#history-list .reschedule-form:not([hidden])')) return;
    const request = ++historyRequest;
    const [data, scheduled] = await Promise.all([
      api(`rides/?page=${page}&${historyFilters}`),
      config.user.role === 'RIDER' && !historyFilters.size && page === 1 ? api(`rides/schedule/?pending=1&page=${scheduledPage}`) : Promise.resolve(null),
    ]);
    if (request !== historyRequest) return;
    if (preserveEditor && $('#history-list .reschedule-form:not([hidden])')) return;
    state.page = page;
    let scheduledHtml = '';
    const pendingScheduled = (scheduled?.results || scheduled || []).filter(s => !s.cancelled_at && !s.ride);
    if (pendingScheduled.length || scheduledPage > 1) {
      scheduledHtml = `<section class="scheduled-box" aria-label="Upcoming reservations"><h3>Upcoming reservations</h3>
        <p class="form-hint">Times use ${esc(Intl.DateTimeFormat().resolvedOptions().timeZone)}. Calendar reminders do not sync: update them after changing or cancelling a reservation.</p>
        ${pendingScheduled.map(s => {
          const upcoming = new Date(s.scheduled_for).getTime() > Date.now();
          return `<article class="scheduled-card">
            <strong>${esc(s.quote.pickup_label)} &rarr; ${esc(s.quote.destination_label)}</strong>
            <p>Pickup ${esc(date(s.scheduled_for))} &middot; ${amount(s.quote.fare, s.quote.currency)}</p>
            <p class="form-hint">${isDemoMethod(s.payment_method) ? 'Prepaid · ' : 'Pay after trip · '}${esc(paymentNames[s.payment_method] || s.payment_method)}</p>
            ${upcoming ? '' : '<p class="form-hint">Pickup is due. Waiting for dispatch; reservations expire after 15 minutes.</p>'}
            <div class="scheduled-actions">
              ${upcoming ? `<button type="button" class="button secondary small" data-edit-schedule="${s.id}">Change pickup time</button>
              <button type="button" class="button secondary small" data-calendar="${s.id}">Add to calendar</button>` : ''}
              <button type="button" class="button danger small cancel-scheduled-btn" data-id="${s.id}">Cancel reservation</button>
            </div>
            <form class="reschedule-form" id="reschedule-${s.id}" data-id="${s.id}" hidden>
              <label for="reschedule-time-${s.id}">New pickup date &amp; time</label>
              <input type="datetime-local" id="reschedule-time-${s.id}" name="scheduled_for" value="${localDateTime(s.scheduled_for)}" required>
              <p class="form-hint">Choose more than 15 minutes and up to 7 days ahead. Your route, fare and payment method stay the same.</p>
              <div class="scheduled-actions"><button class="button small" type="submit">Save pickup time</button>
              <button class="button secondary small" type="button" data-close-schedule>Keep current time</button></div>
            </form>
          </article>`;
        }).join('')}
        <div class="scheduled-actions">
          <button type="button" class="button secondary small" data-scheduled-page="${scheduledPage-1}" ${!scheduled?.previous ? 'disabled' : ''}>Earlier reservations</button>
          <span>Page ${scheduledPage} &middot; ${scheduled?.count || 0} reservations</span>
          <button type="button" class="button secondary small" data-scheduled-page="${scheduledPage+1}" ${!scheduled?.next ? 'disabled' : ''}>Later reservations</button>
        </div></section>`;
    }
    $('#history-list').innerHTML = scheduledHtml + (data.results.length ? data.results.map(ride => `<div class="trip-row"><span class="trip-icon">↗</span><div><h3>${esc(ride.quote.destination_label)}</h3><p>From ${esc(ride.quote.pickup_label)}</p><p>${esc(date(ride.created_at))}</p></div><div class="price">${amount(ride.quote.fare, ride.quote.currency)}<span class="pill">${esc(ride.status.toLowerCase().replaceAll('_',' '))}</span></div><button data-history="${esc(ride.id)}" aria-label="View trip to ${esc(ride.quote.destination_label)}">View →</button></div>`).join('') : '<div class="empty-state"><span class="empty-icon">◷</span>No trips match these filters. Try another route or reset the filters.</div>');
    $('#history-prev').disabled = !data.previous; $('#history-next').disabled = !data.next; $('#history-page').textContent = `Page ${state.page} · ${data.count} trips`;
    $('#history-prev').dataset.restDisabled = String(!data.previous); $('#history-next').dataset.restDisabled = String(!data.next);
    $('#history-list').querySelectorAll('[data-history]').forEach(button => button.addEventListener('click', () => busy(button, async () => {
      state.selected = await api(`rides/${button.dataset.history}/`); renderTrip(state.selected); await changeTab('ride'); $('#trip-panel').scrollIntoView({behavior:'smooth',block:'nearest'});
    })));
    if (config.user.role === 'RIDER') $('#history-list').querySelectorAll('[data-history]').forEach(viewButton => {
      const ride = data.results.find(item => item.id === viewButton.dataset.history);
      if (!['COMPLETED', 'CANCELLED'].includes(ride.status)) return;
      const actions = document.createElement('div'); actions.className = 'trip-history-actions';
      viewButton.replaceWith(actions); actions.appendChild(viewButton);
      const again = document.createElement('button'); again.type = 'button'; again.className = 'plain-link';
      again.textContent = 'Ride again'; again.dataset.rebook = ride.id;
      again.addEventListener('click', () => busy(again, () => rideAgain(ride.id)));
      actions.appendChild(again);
    });
    $('#history-list').querySelectorAll('.cancel-scheduled-btn').forEach(button => button.addEventListener('click', () => busy(button, async () => {
      await api(`rides/schedule/${button.dataset.id}/`, {method:'DELETE'});
      scheduledPage = 1;
      toast('Scheduled ride cancelled.');
      await loadHistory();
    })));
    $('#history-list').querySelectorAll('[data-edit-schedule]').forEach(button => button.addEventListener('click', () => {
      const form = $(`#reschedule-${button.dataset.editSchedule}`);
      form.hidden = false;
      setScheduleBounds(form.elements.scheduled_for);
      form.elements.scheduled_for.focus();
    }));
    $('#history-list').querySelectorAll('[data-close-schedule]').forEach(button => button.addEventListener('click', () => {
      button.closest('form').hidden = true;
      loadHistory().catch(error => toast(error.message, true));
    }));
    $('#history-list').querySelectorAll('.reschedule-form').forEach(form => form.addEventListener('submit', event => {
      event.preventDefault();
      busy(form.querySelector('[type="submit"]'), async () => {
        const scheduledFor = validateScheduleInput(form.elements.scheduled_for);
        await api(`rides/schedule/${form.dataset.id}/`, {method:'PATCH', data:{scheduled_for:scheduledFor}});
        form.hidden = true;
        scheduledPage = 1;
        toast('Pickup time updated. Replace your calendar reminder if you downloaded one.');
        await loadHistory();
      });
    }));
    $('#history-list').querySelectorAll('[data-calendar]').forEach(button => button.addEventListener('click', () => busy(button, async () => {
      const id = button.dataset.calendar;
      await downloadFile(`/api/rides/schedule/${id}/calendar/`, `nori-reservation-${id}.ics`);
      toast('Open the downloaded file in your calendar app to add the reminder.');
    })));
    $('#history-list').querySelectorAll('[data-scheduled-page]').forEach(button => button.addEventListener('click', () => busy(button, async () => {
      scheduledPage = Number(button.dataset.scheduledPage);
      await loadHistory();
    })));
  }
  function reportParams(form) {
    const params = new URLSearchParams();
    for (const [key, value] of new FormData(form)) if (value.trim()) params.set(key, value.trim());
    if (params.get('date_from') && params.get('date_to') && params.get('date_from') > params.get('date_to')) {
      throw new Error('End date must be on or after start date.');
    }
    return params;
  }
  async function downloadFile(path, filename) {
    const response = await fetch(path, {credentials:'same-origin'});
    if (!response.ok) {
      const error = await response.json().catch(() => null);
      throw new Error(explain(error) || 'The file could not be downloaded. Please try again.');
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = url; link.download = filename;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }
  async function exportHistory() {
    await downloadFile(`/api/rides/export/?${historyFilters}`, 'nori-trips.csv');
    toast('Your trip report has been downloaded.');
  }
  async function loadEarnings() {
    if (config.user.role !== 'DRIVER') return;
    const request = ++earningsRequest;
    const data = await api(`payments/earnings/?${earningsFilters}`);
    if (request !== earningsRequest) return;
    const period = earningsFilters.size ? 'SELECTED DATES' : 'ALL TIME';
    $('#earnings-content').innerHTML = data.totals.length ? data.totals.map(row => {
      const days = data.daily.filter(day => day.currency === row.currency);
      const peak = Math.max(1, ...days.map(day => Number(day.net)));
      return `<div class="eyebrow">${esc(row.currency)} &middot; ${period}</div>
        <div class="metrics">
          <article class="metric"><span>Completed trips</span><strong>${row.trips}</strong></article>
          <article class="metric"><span>Gross fares</span><strong>${amount(row.gross, row.currency)}</strong></article>
          <article class="metric"><span>Platform fees</span><strong>${amount(row.platform_fees, row.currency)}</strong></article>
          <article class="metric"><span>Your net earnings</span><strong>${amount(row.net, row.currency)}</strong></article>
        </div>
        <div class="panel"><h2>Payment breakdown</h2><dl class="receipt-details">
          <div><dt>Average net per trip</dt><dd>${amount(row.average_net, row.currency)}</dd></div>
          <div><dt>Net earnings paid</dt><dd>${amount(row.paid_net, row.currency)}</dd></div>
          <div><dt>Net earnings awaiting payment</dt><dd>${amount(row.pending_net, row.currency)}</dd></div>
          <div><dt>Cash you collected</dt><dd>${amount(row.cash_collected, row.currency)}</dd></div>
          <div><dt>Paid card earnings after fees</dt><dd>${amount(row.card_net, row.currency)}</dd></div>
        </dl><p class="form-hint">These are trip records. Bank payouts and settlement of platform fees are handled separately.</p></div>
        <div class="panel"><h2>Daily earnings</h2>
          <p class="form-hint">${esc(data.trend_from)} through ${esc(data.trend_to)} (UTC), within your selected dates. Net includes paid and pending fares.</p>
          ${days.length ? `<div class="report-table-scroll" tabindex="0" role="region" aria-label="Daily earnings in ${esc(row.currency)}"><table class="report-table"><caption class="sr-only">Daily earnings in ${esc(row.currency)}</caption><thead><tr><th scope="col">Date</th><th scope="col">Trips</th><th scope="col">Net earnings</th><th scope="col">Awaiting payment</th></tr></thead><tbody>${days.map(day => `<tr><th scope="row">${esc(day.date)}</th><td>${day.trips}</td><td>${amount(day.net, row.currency)}<meter min="0" max="${peak}" value="${Math.max(0, Number(day.net))}" aria-label="Net earnings for ${esc(day.date)}">${amount(day.net, row.currency)}</meter></td><td>${amount(day.pending_net, row.currency)}</td></tr>`).join('')}</tbody></table></div>` : '<p class="muted">No completed trips in this daily breakdown. Select an earlier end date to view older days.</p>'}
        </div>`;
    }).join('') : '<div class="panel empty-state">No completed trips in this period. Try a different date range.</div>';
  }
  async function loadStats() {
    const output = $('#stats-content');
    output.innerHTML = '<div class="panel empty-state">Loading your journey insights…</div>';
    try {
      if (config.user.role === 'RIDER') {
        const stats = await api('rides/stats/');
        output.innerHTML = `<div class="metrics"><article class="metric"><span>Completed journeys</span><strong>${esc(stats.total_rides)}</strong></article><article class="metric"><span>Average rating you gave</span><strong>${stats.avg_rating_given == null ? '—' : Number(stats.avg_rating_given).toFixed(1) + ' / 5'}</strong></article></div><article class="panel"><div class="eyebrow">YOUR EVERYDAY DESTINATIONS</div><h2>Most travelled routes</h2>${stats.favourite_routes.length ? stats.favourite_routes.map(route => `<div class="stats-route"><span>${esc(route.pickup)} → ${esc(route.destination)}</span><strong>${esc(route.count)} trips</strong></div>`).join('') : '<p class="empty-state">Complete your first journey to start your travel story.</p>'}</article>`;
      } else {
        const data = await api('payments/earnings/');
        output.innerHTML = `<article class="panel"><div class="eyebrow">YOUR WORK ON THE ROAD</div><h2>Driving performance</h2><p class="muted">Completed trips with cash or card fares. Demo payments are excluded; paid and pending fares are included.</p></article>${data.totals.length ? data.totals.map(row => `<div class="metrics"><article class="metric"><span>Trips · ${esc(row.currency)}</span><strong>${esc(row.trips)}</strong></article><article class="metric"><span>Average net fare</span><strong>${amount(row.average_net, row.currency)}</strong></article><article class="metric"><span>Net fares today</span><strong>${amount(data.today.find(day => day.currency === row.currency)?.net || 0, row.currency)}</strong></article></div>`).join('') : '<article class="panel empty-state">Your performance will appear after your first completed cash or card trip.</article>'}<button type="button" id="performance-earnings" class="button secondary">View earnings breakdown ↗</button>`;
        $('#performance-earnings').addEventListener('click', () => busy(null, () => changeTab('earnings')));
      }
    } catch (error) { output.innerHTML = '<div class="panel empty-state">Could not load insights. Open this tab again to retry.</div>'; throw error; }
  }
  async function loadPayouts() {
    const output = $('#payouts-content');
    if (!state.profile) { output.innerHTML = '<div class="panel empty-state">Create your driver profile to view payouts.</div>'; return; }
    const data = await api('drivers/payouts/');
    const methods = {BANK:'Bank transfer', BKASH:'bKash', NAGAD:'Nagad'};
    output.innerHTML = `<div class="payout-layout"><article class="panel"><div class="eyebrow">AVAILABLE TO REQUEST</div><strong class="payout-balance">${amount(data.available_balance)}</strong><h2>Request a payout</h2><p class="muted">Requests are reviewed by an administrator. Minimum ${amount(10)}.</p><form id="payout-request-form"><label>Amount<input type="number" name="amount" min="10" max="${esc(data.available_balance)}" step="0.01" required></label><label>Transfer method<select name="payout_method"><option value="BANK">Bank transfer</option><option value="BKASH">bKash</option><option value="NAGAD">Nagad</option></select></label><label>Account details<textarea name="account_details" maxlength="200" placeholder="Account holder and bank/account number or mobile wallet number" required></textarea></label><button type="submit" class="button full" ${Number(data.available_balance) < 10 ? 'disabled' : ''}>Submit request</button><p class="form-hint">Double-check the destination account before submitting.</p></form></article><article class="panel"><h2>Payout history</h2>${data.payouts.length ? data.payouts.map(payout => `<div class="payout-record"><div class="panel-heading"><strong>${amount(payout.amount)}</strong><span class="pill ${payout.status === 'PAID' ? 'success' : payout.status === 'REJECTED' ? 'error' : 'warning'}">${esc(payout.status.toLowerCase())}</span></div><p>${esc(methods[payout.payout_method] || payout.payout_method)} · ${esc(date(payout.requested_at))}</p>${payout.admin_note ? `<p>${esc(payout.admin_note)}</p>` : ''}</div>`).join('') : '<div class="journey-empty"><span aria-hidden="true">↗</span><strong>Your payout history starts here.</strong><p>Track each request from review through payment.</p></div>'}</article></div>`;
    onForm('#payout-request-form', async form => {
      await api('drivers/payouts/', {method:'POST', data:form});
      toast('Payout requested. You can track its review here.'); await loadPayouts();
    });
  }
  async function loadDriverDocuments() {
    if (config.user.role !== 'DRIVER') return;
    const data = await api('drivers/documents/');
    const docs = data.results || data;
    $('#driver-documents-list').innerHTML = docs.length ? docs.map(doc => `<div class="payout-record"><strong>${esc(doc.doc_type.replaceAll('_', ' '))}</strong> <span class="pill ${doc.review_status === 'ACCEPTED' ? 'success' : 'warning'}">${esc(doc.review_status.toLowerCase())}</span>${doc.review_note ? `<p>${esc(doc.review_note)}</p>` : ''}<p>Uploaded ${esc(date(doc.uploaded_at))}</p></div>`).join('') : '<p class="form-hint">No verification documents uploaded yet.</p>';
  }
  function updateNotificationCount(unread) {
    document.dispatchEvent(new CustomEvent('nori:notifications', {detail: unread}));
    $('#notification-count').textContent = unread; $('#notification-count').hidden = unread === 0;
    $('#mark-read').disabled = unread === 0;
    $('#mark-read').dataset.restDisabled = String(unread === 0);
  }
  async function loadNotificationSummary() {
    const request = ++notificationsRequest;
    const data = await api('notifications/summary/');
    if (request === notificationsRequest) updateNotificationCount(data.unread_count);
  }
  async function loadNotifications(page = state.notificationPage) {
    const request = ++notificationsRequest;
    const data = await api(`notifications/?page=${page}`);
    if (request !== notificationsRequest) return;
    state.notificationPage = page;
    updateNotificationCount(data.unread_count);
    const signature = JSON.stringify([page, data]);
    if (signature === notificationSignature) return;
    notificationSignature = signature;
    $('#notifications-prev').disabled = !data.previous; $('#notifications-next').disabled = !data.next;
    $('#notifications-prev').dataset.restDisabled = String(!data.previous); $('#notifications-next').dataset.restDisabled = String(!data.next);
    $('#notifications-page').textContent = `Page ${page} · ${data.count} updates`;
    $('#notifications-list').innerHTML = data.results.length ? data.results.map(item => `<div class="notification ${item.read_at ? 'read' : ''}"><span class="status-dot"></span><div>${item.title ? `<strong>${esc(item.title)}</strong>` : ''}<p>${item.action_url?.startsWith('/') && !item.action_url.startsWith('//') && !item.action_url.includes('\\') ? `<a href="${esc(item.action_url)}">${esc(item.message)}</a>` : esc(item.message)}</p><time>${esc(date(item.created_at))}</time></div></div>`).join('') : '<div class="empty-state">You are all caught up. Trip updates and invitations will appear here.</div>';
  }
  async function sync() {
    if (state.closing) return;
    if (state.syncing) { state.syncAgain = true; return state.syncing; }
    state.syncing = (async () => {
      do {
        state.syncAgain = false;
        await refresh();
      } while (state.syncAgain && !state.closing);
    })();
    try { await state.syncing; } finally { state.syncing = null; }
  }
  async function refresh() {
      if (!state.accountLoaded) { await loadAccount(); state.accountLoaded = true; }
      if (initialRideId) {
        if (/^[0-9a-f-]{36}$/i.test(initialRideId)) state.selected = await api(`rides/${initialRideId}/`, {optional:true});
        initialRideId = null;
      }
      const previous = state.active;
      const [active, profile] = await Promise.all([
        api('rides/current/'),
        config.user.role === 'DRIVER' ? api('drivers/profile/', {optional:true}) : Promise.resolve(null),
      ]);
      state.active = active;
      if (state.active && (!state.selected || state.selected.id === state.active.id)) state.selected = state.active;
      else if (state.selected) state.selected = await api(`rides/${state.selected.id}/`);
      else if (previous && !state.active) state.selected = await api(`rides/${previous.id}/`);
      renderTrip(state.selected || state.active);
      const updates = [loadTripMessages(), loadRecentTrips(), state.tab === 'notifications' ? loadNotifications() : loadNotificationSummary()];
      if (config.user.role === 'DRIVER') { setDriverProfile(profile); updates.push(loadOffers()); }
      if (state.tab === 'history') updates.push(loadHistory(state.page, true));
      if (state.tab === 'earnings') updates.push(loadEarnings());
      if (state.tab === 'account' && config.user.role === 'RIDER') updates.push(loadFavouriteDrivers(), loadLostItems());
      // Wait for every update before allowing the next refresh to begin.
      const results = await Promise.allSettled(updates);
      const failure = results.find(result => result.status === 'rejected');
      if (failure) throw failure.reason;
  }
  function connect() {
    if (state.closing) return;
    const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/events/`);
    state.socket = socket;
    socket.onmessage = event => {
      const data = JSON.parse(event.data);
      if (data.event === 'connected') { socketFailures = 0; $('#connection-status').textContent = 'Live updates connected'; return; }
      if (data.event === 'chat_message' && data.ride_id === chatRide?.id) loadTripMessages(true);
      if (data.event !== 'pong' && !document.hidden) { clearTimeout(refreshTimer); refreshTimer = setTimeout(() => sync().catch(error => toast(error.message,true)), 200); }
    };
    socket.onclose = () => {
      $('#connection-status').textContent = 'Refreshing every 8 seconds';
      const delay = Math.min(60000, 6000 * 2 ** Math.min(socketFailures++, 4));
      if (!state.closing) setTimeout(connect, delay);
    };
    socket.onerror = () => socket.close();
  }

  const checkoutDialog = $('#demo-checkout');
  $('#close-demo-checkout').addEventListener('click', () => { if (!checkoutBusy) checkoutDialog.close(); });
  checkoutDialog.addEventListener('cancel', event => { if (checkoutBusy) event.preventDefault(); });
  checkoutDialog.addEventListener('close', () => {
    checkoutResolve?.(false); checkoutResolve = null; checkoutTask = null;
  });
  $('#demo-checkout-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (checkoutBusy || !checkoutTask) return;
    checkoutBusy = true;
    const channel = new FormData(event.currentTarget).get('channel');
    const outcome = event.submitter?.dataset.outcome || 'SUCCESS';
    const controls = checkoutDialog.querySelectorAll('button, input');
    controls.forEach(control => { control.disabled = true; });
    $('#demo-checkout-status').textContent = 'Processing demo payment…';
    try {
      await checkoutTask({demo_channel:channel, demo_outcome:outcome});
      const resolve = checkoutResolve; checkoutResolve = null;
      checkoutDialog.close(); resolve?.(true);
    } catch (error) {
      $('#demo-checkout-status').textContent = error.message || 'Unable to finish demo payment. Please try again.';
    } finally {
      checkoutBusy = false; controls.forEach(control => { control.disabled = false; });
    }
  });
  onForm('#wallet-topup-form', async data => {
    const requestId = crypto.randomUUID();
    const wallet = await api('payments/wallet/');
    const completed = await demoCheckout({amount:data.amount, currency:wallet.currency, purpose:'Add credits to Nori Wallet'}, payment => api('payments/wallet/top-up/', {method:'POST', data:{amount:data.amount, request_id:requestId, ...payment}}));
    if (completed) { toast('Demo credits added to Nori Wallet.'); await loadWallet(1); }
  });
  document.querySelectorAll('[data-topup]').forEach(button => button.addEventListener('click', () => { $('#wallet-topup-amount').value = button.dataset.topup; }));
  $('#refresh-wallet')?.addEventListener('click', event => busy(event.currentTarget, () => loadWallet(1)));
  $('#wallet-prev')?.addEventListener('click', event => busy(event.currentTarget, () => loadWallet(Math.max(1, walletPage - 1))));
  $('#wallet-next')?.addEventListener('click', event => busy(event.currentTarget, () => loadWallet(walletPage + 1)));
  document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => changeTab(button.dataset.tab).catch(error => toast(error.message,true))));
  document.querySelectorAll('[data-pick]').forEach(button => button.addEventListener('click', () => { state.pick = button.dataset.pick; $('#map-instruction').textContent = `Click the map to set your ${state.pick}`; map?.invalidateSize(); $('.map-panel').scrollIntoView({block:'center', behavior:'smooth'}); }));
  $('#map-overview')?.addEventListener('click', () => {
    setFollowDevice(false);
    if (routeLine) map?.fitBounds(pickupLine ? routeLine.getBounds().extend(pickupLine.getBounds()) : routeLine.getBounds(), {padding:[45,65], maxZoom:15});
    else if (markers.pickup) map?.setView(markers.pickup.getLatLng(), 14);
    else map?.setView([config.map.latitude, config.map.longitude], 13);
  });
  $('#device-location').addEventListener('click', event => busy(event.currentTarget, async () => {
    if (deviceWatch !== null) stopSharingLocation();
    else await startDeviceLocation();
  }));
  $('#follow-device').addEventListener('click', () => setFollowDevice(!followDevice));
  $('#retry-route').addEventListener('click', () => {
    routeRetryAt = 0;
    if (lastRouteQuote) drawRoute(lastRouteQuote, true);
  });
  $('#chat-retry').addEventListener('click', () => loadTripMessages(true));
  $('#trip-chat-panel').addEventListener('toggle', () => {
    if ($('#trip-chat-panel').open) { chatUnread = 0; $('#chat-unread').hidden = true; }
  });
  $('#trip-chat-input').addEventListener('input', event => {
    if (chatRide) chatDrafts.set(chatRide.id, event.target.value);
  });
  onForm('#trip-chat-form', async data => {
    const ride = chatRide;
    if (!ride || !data.content?.trim()) return;
    $('#trip-chat-status').textContent = 'Sending…';
    try {
      await api(`rides/${ride.id}/messages/`, {method:'POST', data:{content:data.content.trim()}});
      if (chatRide?.id !== ride.id) return;
      if ($('#trip-chat-input').value === data.content) { $('#trip-chat-input').value = ''; chatDrafts.delete(ride.id); }
      $('#trip-chat-status').textContent = 'Message sent.';
      await loadTripMessages(true);
    } catch (error) {
      if (chatRide?.id === ride.id) $('#trip-chat-status').textContent = `Message not sent. Your draft is saved. ${error.message}`;
      throw error;
    }
  });
  const vehicleDescriptions = {
    STANDARD:'Your own private sedan. Fare shown before you book.',
    MOTO:'A motorcycle ride for one. Remember to wear a helmet.',
    COMFORT:'An upgraded car for a more comfortable journey.',
    XL:'A larger vehicle with extra space for your journey.'
  };
  function syncVehicleChoice() {
    const selected = $('#vehicle-class')?.value;
    document.querySelectorAll('[name="ride_choice"]').forEach(input => { input.checked = input.value === selected; });
    if ($('#ride-choice-note')) $('#ride-choice-note').textContent = vehicleDescriptions[selected] || vehicleDescriptions.STANDARD;
  }
  document.querySelectorAll('[name="ride_choice"]').forEach(input => input.addEventListener('change', () => {
    $('#vehicle-class').value = input.value;
    syncVehicleChoice(); invalidateQuote();
  }));
  const safetyDialog = $('#safety-dialog');
  $('#open-safety')?.addEventListener('click', () => safetyDialog.showModal());
  $('#close-safety')?.addEventListener('click', () => safetyDialog.close());
  safetyDialog?.addEventListener('click', event => {
    const bounds = safetyDialog.getBoundingClientRect();
    if (event.target === safetyDialog && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) safetyDialog.close();
  });
  $('#manage-safety-contacts')?.addEventListener('click', event => busy(event.currentTarget, async () => {
    safetyDialog.close(); await changeTab('account');
    const panel = $('#emergency-contacts-panel, #driver-emergency-contacts-panel');
    panel?.scrollIntoView({block:'center', behavior:'smooth'});
    panel?.querySelector('input')?.focus({preventScroll:true});
  }));
  document.querySelectorAll('[data-shortcut]').forEach(button => button.addEventListener('click', () => busy(button, async () => {
    const shortcut = button.dataset.shortcut;
    if (state.active) { toast('Your current trip is below. Finish or cancel it before booking another.'); $('#trip-panel').scrollIntoView({block:'start'}); return; }
    await prepareBooking();
    if (shortcut === 'saved') {
      $('.booking-saved-places').open = true;
      $('.booking-saved-places').scrollIntoView({block:'center', behavior:'smooth'});
      $('.booking-saved-places summary').focus({preventScroll:true});
      return;
    }
    $('#booking-type-select').value = shortcut === 'schedule' ? 'SCHEDULE' : 'NOW';
    $('#booking-type-select').dispatchEvent(new Event('change'));
    $('#booking-panel').scrollIntoView({block:'start', behavior:'smooth'});
    $(shortcut === 'schedule' ? '#schedule-datetime' : '#pickup-label').focus({preventScroll:true});
  })));
  $('#manage-booking-places')?.addEventListener('click', event => busy(event.currentTarget, async () => {
    await changeTab('account');
    $('#save-place-form').scrollIntoView({block:'center', behavior:'smooth'});
    $('#save-place-form [name="label"]').focus({preventScroll:true});
  }));
  function invalidateQuote() {
    state.quoteVersion++;
    state.quote = null; state.requestId = null; state.appliedPromo = null; $('#quote-result').hidden = true;
    if ($('#booking-step')) $('#booking-step').textContent = '1 of 2 · Your route';
  }
  function updateFareValidity() {
    const output = $('#fare-validity');
    if (!output || !state.quote) return;
    const seconds = Math.max(0, Math.ceil((new Date(state.quote.expires_at).getTime() - Date.now()) / 1000));
    output.textContent = seconds ? `Fare held for ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')} · Confirm when ready` : 'This fare has expired. Select “See my fare” for a fresh quote.';
    output.classList.toggle('expired', !seconds);
    const button = $('#request-ride');
    if (button && !seconds) { button.disabled = true; button.dataset.restDisabled = 'true'; }
  }
  function saveBookingRoute() {
    const form = $('#quote-form');
    if (!form || state.active || state.selected) return;
    try { sessionStorage.setItem(`booking-route:${config.user.id}`, JSON.stringify({fields:Object.fromEntries(new FormData(form)), stops:bookingStops})); } catch (_) {}
  }
  function restoreBookingRoute() {
    if (!$('#quote-form') || state.active || state.selected) return;
    try {
      const saved = JSON.parse(sessionStorage.getItem(`booking-route:${config.user.id}`) || 'null');
      if (!saved?.fields) return;
      for (const name of ['pickup_label','destination_label','pickup_latitude','pickup_longitude','destination_latitude','destination_longitude']) {
        if (typeof saved.fields[name] === 'string') $('#quote-form').elements[name].value = saved.fields[name];
      }
      bookingStops = Array.isArray(saved.stops) ? saved.stops.slice(0,8) : [];
      renderBookingStops();
      const fields = Object.fromEntries(new FormData($('#quote-form')));
      if (fields.pickup_latitude && fields.pickup_longitude && fields.destination_latitude && fields.destination_longitude) drawRoute({...fields, stops:bookingStops}, true);
    } catch (_) {}
  }
  $('#quote-form')?.addEventListener('input', event => {
    invalidateQuote();
    if (event.target.name.endsWith('_label')) {
      event.target.dataset.generated = 'false';
      const kind = event.target.name.split('_')[0];
      for (const field of ['latitude', 'longitude']) event.currentTarget.elements[`${kind}_${field}`].value = '';
      if (markers[kind]) { map?.removeLayer(markers[kind]); delete markers[kind]; }
      clearRoute();
    }
    saveBookingRoute();
  });
  $('#swap-route')?.addEventListener('click', event => busy(event.currentTarget, async () => {
    if (state.active || state.selected) throw new Error('Return to booking before changing your route.');
    const form = $('#quote-form');
    if (!form.elements.pickup_label.value.trim() || !form.elements.destination_label.value.trim()) throw new Error('Choose both pickup and destination before swapping.');
    for (const field of ['label', 'latitude', 'longitude']) {
      const pickup = form.elements[`pickup_${field}`], destination = form.elements[`destination_${field}`];
      [pickup.value, destination.value] = [destination.value, pickup.value];
    }
    bookingStops.reverse(); renderBookingStops();
    invalidateQuote();
    document.querySelectorAll('.places-dropdown').forEach(node => { node.hidden = true; });
    clearRoute();
    for (const kind of ['pickup', 'destination']) {
      if (markers[kind]) { map?.removeLayer(markers[kind]); delete markers[kind]; }
      form.elements[`${kind}_label`].dataset.generated = 'false';
      if (form.elements[`${kind}_latitude`].value && form.elements[`${kind}_longitude`].value) point(kind, form.elements[`${kind}_latitude`].value, form.elements[`${kind}_longitude`].value);
    }
    if (form.elements.pickup_latitude.value && form.elements.destination_latitude.value) drawRoute({...Object.fromEntries(new FormData(form)), stops:bookingStops}, true);
    toast('Pickup and destination swapped. Get a fresh fare to continue.');
  }));
  $('#clear-booking-stops')?.addEventListener('click', () => {
    bookingStops = []; renderBookingStops(); invalidateQuote(); saveBookingRoute();
    const route = Object.fromEntries(new FormData($('#quote-form')));
    if (route.pickup_latitude && route.destination_latitude) drawRoute(route);
  });
  $('#use-pickup-location')?.addEventListener('click', event => busy(event.currentTarget, async () => { await startDeviceLocation(false); const coords = deviceCoords; if (coords) { await choosePoint('pickup', coords.latitude, coords.longitude); map?.setView([coords.latitude,coords.longitude],14); } }));
  
  const bookingTypeSelect = $('#booking-type-select');
  const scheduleRow = $('#schedule-picker-row');
  const scheduleInput = $('#schedule-datetime');
  function localDateTime(value) {
    const d = new Date(value);
    const pad = number => String(number).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  function setScheduleBounds(input) {
    input.min = localDateTime(Date.now() + 16 * 60000);
    input.max = localDateTime(Date.now() + 7 * 86400000);
  }
  function validateScheduleInput(input) {
    const value = input.value;
    const parsed = new Date(value);
    if (!value || Number.isNaN(parsed.getTime()) || localDateTime(parsed) !== value) {
      throw new Error('Choose a valid local pickup date and time.');
    }
    if (parsed.getTime() <= Date.now() + 15 * 60000 || parsed.getTime() > Date.now() + 7 * 86400000) {
      throw new Error('Choose a pickup more than 15 minutes and no more than 7 days from now.');
    }
    return parsed.toISOString();
  }
  if (bookingTypeSelect && scheduleRow) {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    $('#schedule-timezone').textContent = `Times use your device timezone (${zone}). Book more than 15 minutes and up to 7 days ahead. Driver availability is not guaranteed.`;
    bookingTypeSelect.addEventListener('change', () => {
      invalidateQuote();
      const isSchedule = bookingTypeSelect.value === 'SCHEDULE';
      scheduleRow.hidden = !isSchedule;
      scheduleInput.required = isSchedule;
      scheduleInput.disabled = !isSchedule;
      if (isSchedule) setScheduleBounds(scheduleInput);
      if (isSchedule && scheduleInput && !scheduleInput.value) {
        scheduleInput.value = localDateTime(Date.now() + 30 * 60000);
      }
    });
  }

  onForm('#quote-form', async data => {
    invalidateQuote();
    const version = state.quoteVersion;
    const {ride_choice, ...routeData} = data;
    const payload = {...routeData, stops:bookingStops};
    if (!payload.pickup_latitude) { delete payload.pickup_latitude; delete payload.pickup_longitude; }
    if (!payload.destination_latitude) { delete payload.destination_latitude; delete payload.destination_longitude; }
    const quote = await api('rides/quotes/', {method:'POST', data:payload});
    if (version !== state.quoteVersion || state.active) return;
    state.quote = quote; state.requestId = crypto.randomUUID(); state.appliedPromo = null;
    drawRoute(quote,true);
    $('#pickup-latitude').value = quote.pickup_latitude;
    $('#pickup-longitude').value = quote.pickup_longitude;
    $('#destination-latitude').value = quote.destination_latitude;
    $('#destination-longitude').value = quote.destination_longitude;
    $('#quote-result').hidden = false;
    $('#booking-step').textContent = '2 of 2 · Review & confirm';
    const isSchedule = $('#booking-type-select')?.value === 'SCHEDULE';
    $('#quote-result').innerHTML = `
      <div class="fare-box">
        <div class="eyebrow">YOUR UPFRONT FARE</div>
        <div class="fare-amount" id="fare-amount-display">${amount(quote.fare,quote.currency)}</div>
        <div class="fare-meta">
          <span>≈ ${esc(quote.distance_km)} km</span>
          <span>≈ ${quote.estimated_minutes} min</span>
          <span class="pill success">Private ride</span>
          ${quote.vehicle_class ? `<span class="pill">${esc({STANDARD:'Nori Car',MOTO:'Nori Moto',COMFORT:'Comfort',XL:'Nori XL'}[quote.vehicle_class] || quote.vehicle_class)}</span>` : ''}
        </div>
        ${quote.surge_multiplier && Number(quote.surge_multiplier) > 1 ? `<div class="pill warning" style="margin:6px 0;display:inline-block;">⚡ Surge pricing active (×${quote.surge_multiplier})</div>` : ''}
        <p class="form-hint">Road distance and travel time are estimates. This trip is reserved exclusively for you.</p>
        <p id="fare-validity" class="fare-validity"></p>
        <div class="promo-box" ${isSchedule ? 'hidden' : ''} style="margin:12px 0;padding:10px;background:var(--bg-subtle,#f4f6f8);border-radius:6px;">
          <label for="promo-code-input" style="margin:0 0 4px 0;font-size:12px;font-weight:600;">Promo code</label>
          <div style="display:flex;gap:6px;">
            <input type="text" id="promo-code-input" placeholder="e.g. DISCOUNT10" maxlength="20" style="text-transform:uppercase;flex:1;">
            <button type="button" id="apply-promo-btn" class="button secondary small">Apply</button>
          </div>
          <div id="promo-status" style="font-size:12px;margin-top:4px;" hidden></div>
        </div>
        <label>Payment method
          <select id="payment-method">
            <option value="CASH">Cash · pay your driver</option>
            <option value="WALLET">Nori Wallet · prepay with demo credits</option>
            <option value="SSL_DEMO">SSLCOMMERZ · demo prepayment</option>
            ${config.cardPayments ? '<option value="CARD">Card · pay after your trip</option>' : ''}
          </select>
        </label>
        <p id="payment-hint" class="form-hint" aria-live="polite">Pay your driver in cash after the trip.</p>
        <button type="button" id="booking-wallet-link" class="plain-link" hidden>Open Nori Wallet to top up →</button>
        <button id="request-ride" class="button full">${isSchedule ? 'Confirm scheduled ride' : 'Request a ride'} <span>→</span></button>
      </div>
    `;

    $('#quote-result').setAttribute('tabindex', '-1');
    $('#quote-result').focus({preventScroll:true});
    $('#quote-result').scrollIntoView({block:'nearest', behavior:'smooth'});

    $('#booking-wallet-link').addEventListener('click', () => changeTab('wallet').catch(error => toast(error.message, true)));
    $('#payment-method').addEventListener('change', async () => {
      const method = $('#payment-method').value;
      const demo = isDemoMethod(method);
      $('.promo-box').hidden = isSchedule || demo;
      state.appliedPromo = null;
      $('#promo-status').hidden = true;
      $('#fare-amount-display').textContent = `${quote.currency} ${Number(quote.fare).toFixed(2)}`;
      $('#booking-wallet-link').hidden = method !== 'WALLET';
      $('#request-ride').textContent = demo ? (isSchedule ? 'Prepay & reserve · demo →' : 'Prepay & request ride · demo →') : (isSchedule ? 'Confirm scheduled ride →' : 'Request a ride →');
      $('#payment-hint').textContent = method === 'SSL_DEMO' ? 'Demo bKash, Nagad, card, or bank checkout. Pay before booking; cancellations get a full simulated refund.' : method === 'WALLET' ? 'Pay before booking with Nori Wallet demo credits. Checking balance…' : method === 'CASH' ? 'Pay your driver in cash after the trip.' : 'Pay by card after your trip.';
      if (method === 'WALLET') {
        try {
          const wallet = await api('payments/wallet/');
          if ($('#payment-method')?.value === 'WALLET' && state.quote?.id === quote.id) $('#payment-hint').textContent = `Demo balance: ${wallet.currency} ${Number(wallet.balance).toFixed(2)}. ${wallet.currency !== quote.currency ? 'Wallet currency does not match this fare.' : Number(wallet.balance) < Number(quote.fare) ? 'Top up before confirming this ride.' : 'Your fare will be deducted when you confirm.'} Cancelled bookings receive a full demo refund.`;
        } catch (error) { if ($('#payment-method')?.value === 'WALLET') $('#payment-hint').textContent = error.message; }
      }
    });

    $('#apply-promo-btn')?.addEventListener('click', event => busy(event.currentTarget, async () => {
      const code = $('#promo-code-input')?.value?.trim().toUpperCase();
      const statusEl = $('#promo-status');
      if (!code) {
        if (statusEl) { statusEl.textContent = 'Please enter a promo code.'; statusEl.hidden = false; statusEl.style.color = 'var(--error)'; }
        return;
      }
      try {
        const res = await api('rides/promos/validate/', {
          method: 'POST',
          data: { code: code, fare: state.quote.fare }
        });
        state.appliedPromo = res.code;
        if (statusEl) {
          statusEl.textContent = `Promo ${res.code} applied! Saved ${amount(res.discount_applied, state.quote.currency)}.`;
          statusEl.hidden = false;
          statusEl.style.color = 'var(--success, #2e7d32)';
        }
        $('#fare-amount-display').innerHTML = `<s style="font-size:0.7em;opacity:0.6;margin-right:6px;">${amount(state.quote.fare, state.quote.currency)}</s>${amount(res.discounted_fare, state.quote.currency)}`;
        toast(`Promo code ${res.code} applied!`);
      } catch (err) {
        state.appliedPromo = null;
        if (statusEl) {
          statusEl.textContent = err.message || 'Invalid promo code.';
          statusEl.hidden = false;
          statusEl.style.color = 'var(--error, #d32f2f)';
        }
        $('#fare-amount-display').textContent = amount(state.quote.fare, state.quote.currency);
      }
    }));

    $('#request-ride')?.addEventListener('click', event => busy(event.currentTarget, async () => {
      if (!state.quote || new Date(state.quote.expires_at).getTime() <= Date.now()) throw new Error('Get a fresh fare estimate before booking.');
      const currentIsSchedule = $('#booking-type-select')?.value === 'SCHEDULE';
      const paymentMethod = $('#payment-method').value;
      const quoteId = state.quote.id;
      const version = state.quoteVersion;
      const scheduledFor = currentIsSchedule ? validateScheduleInput(scheduleInput) : null;
      const payload = {quote_id:quoteId, payment_method:paymentMethod,
        ...(currentIsSchedule ? {scheduled_for:scheduledFor} : {request_id:state.requestId, promo_code:state.appliedPromo || undefined})};
      let booked;
      const submitBooking = async demo => {
        if (!state.quote || state.quote.id !== quoteId || state.quoteVersion !== version) throw new Error('Your booking changed. Close checkout and get a fresh fare.');
        booked = await api(currentIsSchedule ? 'rides/schedule/' : 'rides/', {method:'POST', data:{...payload, ...demo}});
      };
      if (paymentMethod === 'SSL_DEMO') {
        const done = await demoCheckout({amount:state.quote.fare, currency:state.quote.currency,
          purpose:currentIsSchedule ? 'Prepay your Nori reservation' : 'Prepay your Nori ride'}, submitBooking);
        if (!done) return;
      } else await submitBooking({});
      if (currentIsSchedule) {
        state.quote = null;
        $('#quote-result').hidden = true;
        $('#history-filters').reset();
        historyFilters = new URLSearchParams();
        state.page = 1;
        scheduledPage = 1;
        toast('Ride scheduled successfully! View it in My trips.');
        await changeTab('history');
      } else {
        state.selected = booked;
        state.quote = null;
        $('#quote-result').hidden = true;
        toast('Finding a nearby driver for you.');
        await sync();
      }
    }));
  });
  onForm('#security-question-form', async (data, form) => {
    if (!data.security_question) throw new Error('Please select a security question.');
    if (!data.security_answer?.trim()) throw new Error('Please provide an answer to your security question.');
    await api('auth/me/', {method:'PATCH', data:{security_question: data.security_question, security_answer: data.security_answer.trim()}});
    form.elements.security_answer.value = '';
    toast('Security question and answer saved.');
  });
  onForm('#save-place-form', async (data, form) => {
    const editId = editingPlaceId;
    await api(editId ? `rides/saved-places/${editId}/` : 'rides/saved-places/', {method:editId ? 'PATCH' : 'POST', data});
    resetPlaceEditor();
    toast(editId ? 'Saved place updated.' : 'Saved place added.');
    await loadSavedPlaces(1);
  });
  $('#cancel-place-edit')?.addEventListener('click', resetPlaceEditor);
  ['#emergency-contact-form', '#driver-emergency-contact-form'].forEach(selector => onForm(selector, async (data, form) => {
    await api('auth/emergency-contacts/', {method:'POST', data});
    form.reset();
    toast('Emergency contact added.');
    await loadEmergencyContacts();
  }));
  onForm('#account-form', async data => { await api('auth/me/', {method:'PATCH', data}); toast('Your profile is updated.'); });
  onForm('#driver-profile-form', async data => { state.profile = await api('drivers/profile/', {method:state.profile ? 'PATCH' : 'POST', data}); setDriverProfile(state.profile); toast('Driver information saved for review.'); });
  onForm('#driver-document-form', async (_, form) => {
    await api('drivers/documents/', {method:'POST', data:new FormData(form)});
    form.reset(); await loadDriverDocuments();
    setDriverProfile(await api('drivers/profile/'));
    toast('Document uploaded for administrator review.');
  });
  onForm('#vehicle-form', async data => { state.vehicle = await api('drivers/vehicle/', {method:state.vehicle ? 'PATCH' : 'POST', data}); toast('Vehicle saved. Your profile will be reviewed.'); setDriverProfile(await api('drivers/profile/')); });
  onForm('#driver-location-form', async data => { state.profile = await api('drivers/location/', {method:'POST', data}); point('driver',data.latitude,data.longitude,true); $('#gps-status').textContent = 'Location updated manually. Keep it current while online.'; toast('Location updated.'); });
  $('#share-location')?.addEventListener('click', event => busy(event.currentTarget, async () => {
    if (state.sharing) stopSharingLocation(); else await shareLocation();
  }));
  $('#toggle-online')?.addEventListener('click', event => busy(event.currentTarget, async () => {
    if (!state.online && (!state.profile?.location_updated_at || Date.now() - new Date(state.profile.location_updated_at).getTime() > 80000)) await shareLocation();
    setDriverProfile(await api('drivers/availability/', {method:'POST', data:{is_online:!state.online}}));
    if (!state.online) stopSharingLocation();
    await sync();
  }));
  onForm('#history-filters', async (_data, form) => {
    historyFilters = reportParams(form); await loadHistory(1);
  });
  $('#history-filter').addEventListener('change', () => $('#history-filters').requestSubmit());
  $('#history-filters').addEventListener('reset', () => {
    historyFilters = new URLSearchParams(); loadHistory(1).catch(error => toast(error.message, true));
  });
  $('#history-export').addEventListener('click', event => busy(event.currentTarget, exportHistory));
  onForm('#earnings-filters', async (_data, form) => {
    earningsFilters = reportParams(form); await loadEarnings();
  });
  $('#earnings-filters').addEventListener('reset', () => {
    earningsFilters = new URLSearchParams(); loadEarnings().catch(error => toast(error.message, true));
  });
  $('#history-prev').addEventListener('click', event => busy(event.currentTarget, () => loadHistory(Math.max(1,state.page-1))));
  $('#history-next').addEventListener('click', event => busy(event.currentTarget, () => loadHistory(state.page+1)));
  $('#notifications-prev').addEventListener('click', event => busy(event.currentTarget, () => loadNotifications(Math.max(1,state.notificationPage-1))));
  $('#notifications-next').addEventListener('click', event => busy(event.currentTarget, () => loadNotifications(state.notificationPage+1)));
  $('#mark-read').addEventListener('click', event => busy(event.currentTarget, async () => { await api('notifications/read/', {method:'POST',data:{}}); await loadNotifications(); }));
  $('#keep-ride').addEventListener('click', () => $('#cancellation-dialog').close());
  $('#close-cancellation').addEventListener('click', () => $('#cancellation-dialog').close());
  onForm('#confirm-cancellation-form', async data => {
    if (!pendingCancellation) return;
    const preview = pendingCancellation;
    try {
      state.selected = await api(`rides/${preview.ride_id}/actions/`, {method:'POST', data:{action:'cancel', reason:data.reason, expected_cancellation_fee:preview.fee}});
      if (state.active?.id === preview.ride_id) state.active = null;
      pendingCancellation = null;
      $('#cancellation-dialog').close();
      toast('Your ride has been cancelled.');
      await sync();
    } catch (error) {
      $('#cancellation-error').textContent = error.message;
      throw error;
    }
  });
  $('#return-active-trip').addEventListener('click', () => { state.selected = state.active; renderTrip(state.active); });
  window.addEventListener('pagehide', () => { saveBookingRoute(); state.closing = true; state.socket?.close(); stopSharingLocation(); });
  window.addEventListener('pageshow', event => {
    if (event.persisted) { state.closing = false; connect(); sync().catch(error => toast(error.message,true)); }
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) sync().catch(error => toast(error.message,true)); });
  function setupPlaceSearch() {
    ['pickup', 'destination'].forEach(kind => {
      const input = $(`#${kind}-label`);
      const dropdown = $(`#${kind}-places`);
      if (!input || !dropdown) return;

      input.setAttribute('role', 'combobox');
      input.setAttribute('aria-autocomplete', 'list');
      input.setAttribute('aria-controls', dropdown.id);
      input.setAttribute('aria-expanded', 'false');
      dropdown.setAttribute('role', 'listbox');
      dropdown.setAttribute('aria-label', `${kind} suggestions`);
      let timer, controller, searchVersion = 0, activeIndex = -1;
      const collapse = () => {
        dropdown.hidden = true; activeIndex = -1;
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
      };
      new MutationObserver(() => {
        input.setAttribute('aria-expanded', String(!dropdown.hidden));
        if (dropdown.hidden) input.removeAttribute('aria-activedescendant');
      }).observe(dropdown, {attributes:true, attributeFilter:['hidden']});
      input.addEventListener('keydown', event => {
        if (event.key === 'Escape') { clearTimeout(timer); controller?.abort(); ++searchVersion; collapse(); return; }
        const options = [...dropdown.querySelectorAll('button.place-item')];
        if (dropdown.hidden || !options.length) return;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault();
          activeIndex = activeIndex < 0 ? (event.key === 'ArrowDown' ? 0 : options.length - 1) : (activeIndex + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
          options.forEach((option, index) => option.setAttribute('aria-selected', String(index === activeIndex)));
          input.setAttribute('aria-activedescendant', options[activeIndex].id);
          options[activeIndex].scrollIntoView({block:'nearest'});
        } else if (event.key === 'Enter' && activeIndex >= 0) {
          event.preventDefault(); options[activeIndex].click();
        } else if (event.key === 'Tab') collapse();
      });
      async function search(q = '') {
        controller?.abort();
        controller = new AbortController();
        const version = ++searchVersion;
        try {
          const places = await api(`rides/places/search/?q=${encodeURIComponent(q)}`, {signal:controller.signal});
          if (version !== searchVersion || document.activeElement !== input || input.value.trim() !== q) return;
          if (!places || !places.length) {
            dropdown.innerHTML = '<div class="place-item" style="cursor:default;"><span class="place-name">No locations found</span><span class="place-address">Try another landmark or area name</span></div>';
            dropdown.hidden = false;
            return;
          }
          activeIndex = -1;
          input.removeAttribute('aria-activedescendant');
          dropdown.innerHTML = places.map((p, index) => `
            <button type="button" class="place-item" role="option" tabindex="-1" aria-selected="false" id="${kind}-option-${index}" data-label="${esc(p.label)}" data-address="${esc(p.address)}" data-lat="${p.latitude}" data-lon="${p.longitude}">
              <span class="place-name">${esc(p.label)}</span>
              <span class="place-address">${esc(p.address)}</span>
              ${p.category ? `<span class="place-tag">${esc(p.category)}</span>` : ''}
            </button>
          `).join('');
          dropdown.hidden = false;

          dropdown.querySelectorAll('.place-item').forEach(btn => {
            btn.addEventListener('click', e => {
              e.preventDefault();
              e.stopPropagation();
              choosePoint(kind, btn.dataset.lat, btn.dataset.lon, btn.dataset.label);
              collapse();
              if (kind === 'pickup' && !$('#destination-label')?.value) {
                $('#destination-label')?.focus();
              }
            });
          });
        } catch (error) {
          if (error.name !== 'AbortError' && version === searchVersion) dropdown.hidden = true;
        }
      }

      input.addEventListener('focus', () => search(input.value.trim()));
      input.addEventListener('input', () => {
        collapse();
        clearTimeout(timer);
        controller?.abort();
        ++searchVersion;
        timer = setTimeout(() => search(input.value.trim()), 300);
      });
      input.addEventListener('blur', () => { clearTimeout(timer); controller?.abort(); });
    });

    document.addEventListener('click', event => {
      if (!event.target.closest('.location-input')) {
        document.querySelectorAll('.places-dropdown').forEach(d => { d.hidden = true; });
      }
    });

    document.querySelectorAll('.quick-place-pill').forEach(pill => {
      pill.addEventListener('click', () => {
        choosePoint('destination', pill.dataset.lat, pill.dataset.lon, pill.dataset.dest);
      });
    });
  }

  async function start() {
    setupMap(); setupPlaceSearch(); setupStopPlanner(); setupQuickActions(); connect();
    if (config.user.role === 'RIDER') {
      const params = new URLSearchParams(location.search);
      const vehicle = params.get('vehicle');
      if (Object.hasOwn(vehicleDescriptions, vehicle)) $('#vehicle-class').value = vehicle;
      syncVehicleChoice();
      if (params.get('when') === 'later') {
        bookingTypeSelect.value = 'SCHEDULE';
        bookingTypeSelect.dispatchEvent(new Event('change'));
      }
    }
    setInterval(() => { if (!document.hidden) sync().catch(() => { $('#connection-status').textContent = 'Connection interrupted. Retrying…'; }); }, 8000);
    setInterval(() => { if (!document.hidden) updateFareValidity(); }, 1000);
    setInterval(() => { if (state.socket?.readyState === WebSocket.OPEN) state.socket.send(JSON.stringify({type:'ping'})); }, 30000);
    await sync();
    restoreBookingRoute();
    loadRecentTrips();
  }
  start().catch(error => toast(error.message,true));
})();
