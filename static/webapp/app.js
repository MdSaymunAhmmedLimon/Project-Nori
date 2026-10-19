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
  let historyRequest = 0, notificationsRequest = 0, earningsRequest = 0;
  let historyFilters = new URLSearchParams(), earningsFilters = new URLSearchParams();
  let savedPlacesPage = 1, savedPlacesRequest = 0, editingPlaceId = null;
  let bookingStops = [];
  let scheduledPage = 1;
  let favouritePage = 1, lostItemPage = 1, favouriteRequest = 0, lostItemRequest = 0;
  let initialRideId = new URLSearchParams(location.search).get('ride');

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
    if (button) button.disabled = true;
    try { await task(); } catch (error) { toast(error.message || 'Please try again.', true); }
    finally { if (button) button.disabled = button.dataset.restDisabled === 'true'; }
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
    if (markers[kind]) markers[kind].setLatLng(coords);
    else markers[kind] = L.marker(coords, {icon:L.divIcon({className:'', html:`<span class="map-pin ${kind}">${kind === 'pickup' ? 'A' : kind === 'destination' ? 'B' : '↗'}</span>`, iconSize:[25,25], iconAnchor:[12,12]})}).addTo(map);
    if (fit) map.setView(coords, 14);
  }
  function drawRoute(quote, fit = false) {
    if (!quote || !map) return;
    point('pickup', quote.pickup_latitude, quote.pickup_longitude);
    point('destination', quote.destination_latitude, quote.destination_longitude);
    const line = [[Number(quote.pickup_latitude), Number(quote.pickup_longitude)], ...(quote.stops || []).map(stop => [Number(stop.latitude), Number(stop.longitude)]), [Number(quote.destination_latitude), Number(quote.destination_longitude)]];
    const signature = JSON.stringify(line);
    if (!routeLine || signature !== routeSignature) {
      if (routeLine) map.removeLayer(routeLine);
      routeLine = L.polyline(line, {color:'#285c4e', weight:4, dashArray:'7 8'}).addTo(map);
      routeSignature = signature;
    }
    if (fit) map.fitBounds(line, {padding:[55,80], maxZoom:15});
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
    if (!window.L) { $('#map-instruction').textContent = 'Map unavailable. Enter coordinates to plan a ride.'; return; }
    map = L.map('ride-map', {zoomControl:true}).setView([config.map.latitude, config.map.longitude], 13);
    L.tileLayer(config.map.tileUrl, {maxZoom:19, attribution:esc(config.map.attribution) + ' · <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">License</a>'}).addTo(map);
    map.on('click', event => { if (config.user.role === 'RIDER') choosePoint(state.pick, event.latlng.lat, event.latlng.lng); });
  }
  function gps() {
    return new Promise((resolve, reject) => {
      if (!navigator.geolocation) { reject(new Error('This browser does not support location sharing. Enter coordinates manually.')); return; }
      navigator.geolocation.getCurrentPosition(position => resolve(position.coords), () => reject(new Error('Location is unavailable. Allow location access or enter coordinates manually.')), {enableHighAccuracy:true, timeout:12000, maximumAge:10000});
    });
  }
  async function sendLocation() {
    const coords = await gps();
    state.profile = await api('drivers/location/', {method:'POST', data:{latitude:coords.latitude.toFixed(6), longitude:coords.longitude.toFixed(6)}});
    point('driver', coords.latitude, coords.longitude);
    $('#gps-status').textContent = `Location updated at ${new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}.`;
    populate($('#driver-location-form'), state.profile);
  }
  async function shareLocation() {
    await sendLocation();
    state.sharing = true; $('#share-location').textContent = 'Stop sharing';
    clearInterval(state.locationTimer);
    state.locationTimer = setInterval(() => sendLocation().catch(error => { $('#gps-status').textContent = error.message; }), 25000);
  }
  function stopSharingLocation() {
    clearInterval(state.locationTimer);
    state.sharing = false;
    if ($('#share-location')) $('#share-location').textContent = 'Share location';
    if ($('#gps-status')) $('#gps-status').textContent = state.online ? 'Location sharing is off. Share a fresh location to keep receiving requests.' : 'Location sharing is off.';
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
    if (tab === 'earnings') await loadEarnings();
    if (tab === 'notifications') await loadNotifications();
    if (tab === 'stats') await loadStats();
    if (tab === 'payouts') await loadPayouts();
    if (tab === 'account' && config.user.role === 'RIDER') await Promise.all([loadFavouriteDrivers(), loadLostItems()]);
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
    if (profile?.latitude != null) point('driver', profile.latitude, profile.longitude);
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
    $('#booking-stops-summary').textContent = 'Stops: ' + bookingStops.map(stop => stop.label).join(' / ');
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
      setDriverProfile(profile); state.vehicle = vehicle;
      populate($('#driver-profile-form'), profile); populate($('#vehicle-form'), vehicle); populate($('#driver-location-form'), profile);
      $('#vehicle-form').elements.year.max = new Date().getFullYear() + 1;
      $('#driver-profile-form').elements.license_expiry_date.min = new Date().toISOString().slice(0,10);
      await loadEmergencyContacts();
    }
  }
  function renderTrip(ride) {
    const panel = $('#trip-panel'); panel.hidden = !ride;
    if ($('#booking-panel')) $('#booking-panel').hidden = Boolean(ride || state.active);
    $('#return-active-trip').hidden = !state.active || state.active.id === ride?.id;
    if (!ride) {
      if (tripSignature) {
        if (routeLine) { map?.removeLayer(routeLine); routeLine = null; }
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
      point('driver', ride.driver.location.latitude, ride.driver.location.longitude);
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
      person = `<div class="trip-person"><strong>${esc(partner.name)} ${partner.rating ? ' · ★ ' + Number(partner.rating).toFixed(1) : ''}</strong>${vehicle ? `<p>${esc(vehicle.color)} ${esc(vehicle.make)} ${esc(vehicle.model)}</p><p><strong>${esc(vehicle.license_plate)}</strong></p>` : ''}${partner.phone_number ? `<a href="tel:${esc(partner.phone_number)}">Call ${esc(partner.phone_number)}</a>` : ''}</div>`;
    }
    const actions = {ACCEPTED:['arriving','Head to pickup'], DRIVER_ARRIVING:['arrived','Mark as arrived'], DRIVER_ARRIVED:['start','Start trip'], IN_PROGRESS:['complete','Complete trip']};
    let actionButton = '';
    if (config.user.role === 'DRIVER' && actions[ride.status]) { const [action, label] = actions[ride.status]; actionButton = `<button class="button full" data-ride-action="${action}">${label} →</button>`; }
    const canCancel = !['IN_PROGRESS','COMPLETED','CANCELLED'].includes(ride.status);
    let payment = '';
    if (ride.payment) payment = `<div class="receipt"><p><strong>${amount(ride.payment.amount, ride.payment.currency)}</strong> · ${esc(ride.payment.method.toLowerCase())}</p><small>${ride.payment.status === 'PAID' ? 'Paid · ' + esc(date(ride.payment.paid_at)) : 'Payment pending'}</small>${ride.payment.status !== 'PAID' && config.user.role === 'DRIVER' && ride.payment.method === 'CASH' ? '<button class="button secondary full" data-payment="cash">Confirm cash received</button>' : ''}${ride.payment.status !== 'PAID' && config.user.role === 'RIDER' && ride.payment.method === 'CARD' ? '<button class="button full" data-payment="checkout">Pay securely by card ↗</button>' : ''}<a class="plain-link receipt-link" href="/app/rides/${esc(ride.id)}/receipt/">${ride.payment.status === 'PAID' ? 'View receipt' : 'View trip summary'} ↗</a></div>`;
    const rated = ride.ratings.some(rating => rating.author === config.user.id);
    const rating = completed && !rated ? '<form id="rating-form" class="rating-form"><h3>How was your journey?</h3><label>Rating<select name="score"><option value="5">★★★★★ · Excellent</option><option value="4">★★★★ · Good</option><option value="3">★★★ · Okay</option><option value="2">★★ · Could be better</option><option value="1">★ · Poor</option></select></label><label>Your feedback<textarea name="comment" maxlength="500" placeholder="Anything you would like to share? (optional)"></textarea></label><button class="button secondary full" type="submit">Send rating</button></form>' : (rated ? '<p class="form-hint">Thanks for sharing your rating.</p>' : '');

    const sosButton = isActive ? `<div style="margin-top:10px;"><button type="button" id="sos-alert-btn" class="button danger small full" style="background:#c0392b;border-color:#c0392b;">🚨 Emergency SOS Alert</button><button type="button" id="share-trip-btn" class="button secondary small full" style="margin-top:8px;">Share live trip</button><div id="share-trip-link" class="form-hint" hidden></div></div>` : '';
    const hasPartner = Boolean(ride.driver && ride.rider);
    const chatSection = hasPartner && ['ACCEPTED','DRIVER_ARRIVING','DRIVER_ARRIVED','IN_PROGRESS'].includes(ride.status) ? `
      <div class="chat-section" style="margin-top:16px;border-top:1px solid var(--line);padding-top:12px;">
        <h3 style="font-size:14px;margin-bottom:8px;">💬 In-Trip Messages</h3>
        <div id="trip-chat-log" style="max-height:140px;overflow-y:auto;background:var(--bg-subtle,#f8f9fa);padding:8px;border-radius:6px;font-size:13px;margin-bottom:8px;display:flex;flex-direction:column;gap:6px;">
          <span class="muted" style="font-size:12px;">Loading messages…</span>
        </div>
        <form id="trip-chat-form" style="display:flex;gap:6px;">
          <input name="content" id="trip-chat-input" placeholder="Message ${config.user.role === 'RIDER' ? 'driver' : 'rider'}…" maxlength="500" required style="flex:1;">
          <button type="submit" class="button small">Send</button>
        </form>
      </div>
    ` : '';

    const tipSection = completed && config.user.role === 'RIDER' ? (ride.tip ? `<p class="form-hint" style="color:var(--success,#2e7d32);margin-top:8px;">★ Tip of ${amount(ride.tip, ride.quote.currency)} sent to driver.</p>` : `
      <div class="tip-section" style="margin-top:12px;padding:10px;background:var(--bg-subtle,#f8f9fa);border-radius:6px;">
        <span style="font-size:13px;font-weight:600;display:block;margin-bottom:6px;">Tip your driver (optional)</span>
        <div style="display:flex;gap:6px;">
          <button type="button" class="button secondary small tip-btn" data-tip="20">BDT 20</button>
          <button type="button" class="button secondary small tip-btn" data-tip="50">BDT 50</button>
          <button type="button" class="button secondary small tip-btn" data-tip="100">BDT 100</button>
        </div>
      </div>
    `) : '';

    $('#trip-content').innerHTML = `<div class="trip-title"><h2>${esc(labels[ride.status])}</h2><div style="display:flex;gap:6px;align-items:center;"><span class="pill ${completed ? 'success' : cancelled ? 'error' : ''}">${esc(ride.status.toLowerCase().replaceAll('_',' '))}</span><span class="pill success" style="font-size:11px;">👤 Private · 1 Rider</span></div></div><div class="route-summary"><small>PICKUP</small>${esc(ride.quote.pickup_label)}<small>DESTINATION</small>${esc(ride.quote.destination_label)}</div><div class="fare-meta"><span>${amount(ride.quote.fare, ride.quote.currency)}</span><span>${esc(ride.quote.distance_km)} km est.</span>${ride.quote.vehicle_class ? `<span class="pill" style="font-size:11px;">${esc(ride.quote.vehicle_class)}</span>` : ''}</div>${person}${cancelled ? `<p class="muted">${esc(ride.cancellation_reason || 'This ride was cancelled.')}</p>` : ''}<ol class="timeline">${ride.events.map(event => `<li>${esc(labels[event.status])}<time>${esc(date(event.created_at))}</time></li>`).join('')}</ol>${actionButton}${canCancel ? '<form id="cancel-form" class="cancel-form"><label>Reason for cancelling (optional)<input name="reason" maxlength="300" placeholder="Let us know what changed"></label><button type="submit" class="button danger full">Cancel ride</button></form>' : ''}${sosButton}${chatSection}${payment}${tipSection}${rating}${completed || cancelled ? '<button class="plain-link" id="close-trip">Back to your next journey →</button>' : ''}`;
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
      state.selected = await api(`rides/${ride.id}/actions/`, {method:'POST', data:{action:'cancel', ...data}}); toast('Your ride has been cancelled.'); await sync();
    });
    onForm('#rating-form', async data => {
      await api(`rides/${ride.id}/rating/`, {method:'POST', data:{...data, score:Number(data.score)}}); toast('Thank you for your feedback.'); await sync();
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
    if ($('#trip-chat-log')) {
      async function loadChat() {
        try {
          const msgs = await api(`rides/${ride.id}/messages/`);
          const log = $('#trip-chat-log');
          if (!log) return;
          if (!msgs.length) {
            log.innerHTML = '<span class="muted" style="font-size:12px;">No messages yet. Send a message to coordinate.</span>';
          } else {
            log.innerHTML = msgs.map(m => `
              <div style="padding:4px 8px;border-radius:4px;background:${m.sender === config.user.id ? '#e8f5e9' : '#ffffff'};align-self:${m.sender === config.user.id ? 'flex-end' : 'flex-start'};max-width:85%;border:1px solid var(--line);">
                <div style="font-size:10px;color:#777;font-weight:600;">${esc(m.sender_name)} · ${esc(date(m.created_at))}</div>
                <div>${esc(m.content)}</div>
              </div>
            `).join('');
            log.scrollTop = log.scrollHeight;
          }
        } catch (_) {}
      }
      loadChat();
      onForm('#trip-chat-form', async (data, form) => {
        if (!data.content?.trim()) return;
        await api(`rides/${ride.id}/messages/`, {method:'POST', data:{content:data.content.trim()}});
        form.reset();
        await loadChat();
      });
    }
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
  function updateNotificationCount(unread) {
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
    $('#notifications-list').innerHTML = data.results.length ? data.results.map(item => `<div class="notification ${item.read_at ? 'read' : ''}"><span class="status-dot"></span><div><p>${item.ride_id ? `<a href="/app/?ride=${encodeURIComponent(item.ride_id)}">${esc(item.message)}</a>` : esc(item.message)}</p><time>${esc(date(item.created_at))}</time></div></div>`).join('') : '<div class="empty-state">You are all caught up. Trip updates will appear here.</div>';
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
      const updates = [state.tab === 'notifications' ? loadNotifications() : loadNotificationSummary()];
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
      if (data.event !== 'pong' && !document.hidden) { clearTimeout(refreshTimer); refreshTimer = setTimeout(() => sync().catch(error => toast(error.message,true)), 200); }
    };
    socket.onclose = () => {
      $('#connection-status').textContent = 'Refreshing every 8 seconds';
      const delay = Math.min(60000, 6000 * 2 ** Math.min(socketFailures++, 4));
      if (!state.closing) setTimeout(connect, delay);
    };
    socket.onerror = () => socket.close();
  }

  document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => changeTab(button.dataset.tab).catch(error => toast(error.message,true))));
  document.querySelectorAll('[data-pick]').forEach(button => button.addEventListener('click', () => { state.pick = button.dataset.pick; $('#map-instruction').textContent = `Click the map to set your ${state.pick}`; map?.invalidateSize(); }));
  function invalidateQuote() {
    state.quoteVersion++;
    state.quote = null; state.requestId = null; state.appliedPromo = null; $('#quote-result').hidden = true;
  }
  $('#quote-form')?.addEventListener('input', event => {
    invalidateQuote();
    if (event.target.name.endsWith('_label')) {
      event.target.dataset.generated = 'false';
      const kind = event.target.name.split('_')[0];
      for (const field of ['latitude', 'longitude']) event.currentTarget.elements[`${kind}_${field}`].value = '';
      if (markers[kind]) { map?.removeLayer(markers[kind]); delete markers[kind]; }
      if (routeLine) { map?.removeLayer(routeLine); routeLine = null; }
    }
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
    if (routeLine) { map?.removeLayer(routeLine); routeLine = null; }
    for (const kind of ['pickup', 'destination']) {
      if (markers[kind]) { map?.removeLayer(markers[kind]); delete markers[kind]; }
      form.elements[`${kind}_label`].dataset.generated = 'false';
      if (form.elements[`${kind}_latitude`].value && form.elements[`${kind}_longitude`].value) point(kind, form.elements[`${kind}_latitude`].value, form.elements[`${kind}_longitude`].value);
    }
    if (form.elements.pickup_latitude.value && form.elements.destination_latitude.value) drawRoute({...Object.fromEntries(new FormData(form)), stops:bookingStops}, true);
    toast('Pickup and destination swapped. Get a fresh fare to continue.');
  }));
  $('#clear-booking-stops')?.addEventListener('click', () => {
    bookingStops = []; renderBookingStops(); invalidateQuote();
    const route = Object.fromEntries(new FormData($('#quote-form')));
    if (route.pickup_latitude && route.destination_latitude) drawRoute(route);
  });
  $('#use-pickup-location')?.addEventListener('click', event => busy(event.currentTarget, async () => { const coords = await gps(); choosePoint('pickup', coords.latitude, coords.longitude); map?.setView([coords.latitude,coords.longitude],14); }));
  
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
    const payload = {...data, stops:bookingStops};
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
    const isSchedule = $('#booking-type-select')?.value === 'SCHEDULE';
    $('#quote-result').innerHTML = `
      <div class="fare-box">
        <div class="eyebrow">YOUR UPFRONT FARE</div>
        <div class="fare-amount" id="fare-amount-display">${amount(quote.fare,quote.currency)}</div>
        <div class="fare-meta">
          <span>≈ ${esc(quote.distance_km)} km</span>
          <span>≈ ${quote.estimated_minutes} min</span>
          <span class="pill success" style="font-size:11px;">👤 Private · 1 Rider</span>
          ${quote.vehicle_class ? `<span class="pill" style="font-size:11px;">${esc(quote.vehicle_class)}</span>` : ''}
        </div>
        ${quote.surge_multiplier && Number(quote.surge_multiplier) > 1 ? `<div class="pill warning" style="margin:6px 0;display:inline-block;">⚡ Surge pricing active (×${quote.surge_multiplier})</div>` : ''}
        <p class="form-hint">Distance and time are estimates. This trip is reserved exclusively for you. Fare quote valid for 10 minutes.</p>
        <div class="promo-box" ${isSchedule ? 'hidden' : ''} style="margin:12px 0;padding:10px;background:var(--bg-subtle,#f4f6f8);border-radius:6px;">
          <label style="margin:0 0 4px 0;font-size:12px;font-weight:600;">Promo code</label>
          <div style="display:flex;gap:6px;">
            <input type="text" id="promo-code-input" placeholder="e.g. DISCOUNT10" maxlength="20" style="text-transform:uppercase;flex:1;">
            <button type="button" id="apply-promo-btn" class="button secondary small">Apply</button>
          </div>
          <div id="promo-status" style="font-size:12px;margin-top:4px;" hidden></div>
        </div>
        <label>Payment method
          <select id="payment-method">
            <option value="CASH">Cash · pay your driver</option>
            ${config.cardPayments ? '<option value="CARD">Card · pay after your trip</option>' : ''}
          </select>
        </label>
        <button id="request-ride" class="button full">${isSchedule ? 'Confirm scheduled ride' : 'Request a ride'} <span>→</span></button>
      </div>
    `;

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
      if (!state.quote) throw new Error('Get a new fare estimate first.');
      const currentIsSchedule = $('#booking-type-select')?.value === 'SCHEDULE';
      const paymentMethod = $('#payment-method').value;
      if (currentIsSchedule) {
        const scheduledFor = validateScheduleInput(scheduleInput);
        await api('rides/schedule/', {
          method: 'POST',
          data: {
            quote_id: state.quote.id,
            payment_method: paymentMethod,
            scheduled_for: scheduledFor
          }
        });
        state.quote = null;
        $('#quote-result').hidden = true;
        $('#history-filters').reset();
        historyFilters = new URLSearchParams();
        state.page = 1;
        scheduledPage = 1;
        toast('Ride scheduled successfully! View it in My trips.');
        await changeTab('history');
      } else {
        state.selected = await api('rides/', {
          method: 'POST',
          data: {
            quote_id: state.quote.id,
            request_id: state.requestId,
            payment_method: paymentMethod,
            promo_code: state.appliedPromo || undefined
          }
        });
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
  $('#return-active-trip').addEventListener('click', () => { state.selected = state.active; renderTrip(state.active); });
  window.addEventListener('pagehide', () => { state.closing = true; state.socket?.close(); stopSharingLocation(); });
  window.addEventListener('pageshow', event => {
    if (event.persisted) { state.closing = false; connect(); sync().catch(error => toast(error.message,true)); }
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) sync().catch(error => toast(error.message,true)); });
  function setupPlaceSearch() {
    ['pickup', 'destination'].forEach(kind => {
      const input = $(`#${kind}-label`);
      const dropdown = $(`#${kind}-places`);
      if (!input || !dropdown) return;

      let timer, controller, searchVersion = 0;
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
          dropdown.innerHTML = places.map(p => `
            <button type="button" class="place-item" data-label="${esc(p.label)}" data-address="${esc(p.address)}" data-lat="${p.latitude}" data-lon="${p.longitude}">
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
              dropdown.hidden = true;
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
    setupMap(); setupPlaceSearch(); connect();
    setInterval(() => { if (!document.hidden) sync().catch(() => { $('#connection-status').textContent = 'Connection interrupted. Retrying…'; }); }, 8000);
    setInterval(() => { if (state.socket?.readyState === WebSocket.OPEN) state.socket.send(JSON.stringify({type:'ping'})); }, 30000);
    await sync();
  }
  start().catch(error => toast(error.message,true));
})();
