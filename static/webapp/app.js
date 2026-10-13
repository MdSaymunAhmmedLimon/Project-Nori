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
  let historyRequest = 0, notificationsRequest = 0;
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
  async function api(path, {method = 'GET', data, optional = false} = {}) {
    const isFormData = typeof FormData !== 'undefined' && data instanceof FormData;
    const csrf = document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=').slice(1).join('=');
    const headers = {'X-CSRFToken': decodeURIComponent(csrf || '')};
    if (!isFormData && data !== undefined) headers['Content-Type'] = 'application/json';
    const body = isFormData ? data : (data === undefined ? undefined : JSON.stringify(data));
    let response;
    try {
      response = await fetch(`/api/${path}`, {method, credentials:'same-origin', headers, body});
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
    const line = [[Number(quote.pickup_latitude), Number(quote.pickup_longitude)], [Number(quote.destination_latitude), Number(quote.destination_longitude)]];
    if (routeLine) map.removeLayer(routeLine);
    routeLine = L.polyline(line, {color:'#285c4e', weight:4, dashArray:'7 8'}).addTo(map);
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
    if (form.elements.pickup_latitude.value && form.elements.destination_latitude.value) drawRoute(Object.fromEntries(new FormData(form)));
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
  async function loadSavedPlaces() {
    if (config.user.role !== 'RIDER') return;
    try {
      const places = await api('rides/saved-places/', {optional:true});
      const list = $('#saved-places-list');
      if (!list) return;
      list.innerHTML = places && places.length ? places.map(p => `
        <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--line);">
          <div>
            <strong>${esc(p.label)}</strong> <span class="muted" style="font-size:12px;">(${esc(p.address)})</span>
          </div>
          <div style="display:flex;gap:6px;">
            <button type="button" class="button secondary small use-saved-place" data-lat="${p.latitude}" data-lon="${p.longitude}" data-label="${esc(p.label)}">Use</button>
            <button type="button" class="plain-link delete-saved-place" data-id="${p.id}" style="color:var(--error,#d32f2f);font-size:12px;">Delete</button>
          </div>
        </div>
      `).join('') : '<p class="muted" style="font-size:13px;">No saved places yet.</p>';

      list.querySelectorAll('.use-saved-place').forEach(btn => {
        btn.addEventListener('click', () => {
          choosePoint(state.pick, btn.dataset.lat, btn.dataset.lon, btn.dataset.label);
          changeTab('ride');
        });
      });
      list.querySelectorAll('.delete-saved-place').forEach(btn => {
        btn.addEventListener('click', () => busy(btn, async () => {
          await api(`rides/saved-places/${btn.dataset.id}/`, {method:'DELETE'});
          toast('Saved place deleted.');
          await loadSavedPlaces();
        }));
      });
    } catch (_) {}
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
      await Promise.all([loadSavedPlaces(), loadEmergencyContacts()]);
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
  async function loadHistory(page = state.page) {
    const request = ++historyRequest;
    const status = $('#history-filter').value;
    const [data, scheduled] = await Promise.all([
      api(`rides/?page=${page}${status ? '&status=' + encodeURIComponent(status) : ''}`),
      config.user.role === 'RIDER' && !status && page === 1 ? api('rides/schedule/', {optional:true}) : Promise.resolve(null),
    ]);
    if (request !== historyRequest) return;
    state.page = page;
    let scheduledHtml = '';
    const pendingScheduled = (scheduled?.results || scheduled || []).filter(s => !s.cancelled_at && !s.ride);
    if (pendingScheduled.length) {
      scheduledHtml = `<div class="scheduled-box" style="margin-bottom:16px;background:var(--bg-subtle,#f8f9fa);border:1px solid var(--line);border-radius:8px;padding:12px;"><strong style="display:block;margin-bottom:8px;">📅 Upcoming Scheduled Rides (Private)</strong>${pendingScheduled.map(s => `
        <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--line);">
          <div>
            <div style="font-weight:600;">${esc(s.quote.pickup_label)} → ${esc(s.quote.destination_label)}</div>
            <small class="muted">Scheduled for ${esc(date(s.scheduled_for))} · ${amount(s.quote.fare, s.quote.currency)}</small>
          </div>
          <button class="button danger small cancel-scheduled-btn" data-id="${s.id}">Cancel</button>
        </div>
      `).join('')}</div>`;
    }
    $('#history-list').innerHTML = scheduledHtml + (data.results.length ? data.results.map(ride => `<div class="trip-row"><span class="trip-icon">↗</span><div><h3>${esc(ride.quote.destination_label)}</h3><p>From ${esc(ride.quote.pickup_label)}</p><p>${esc(date(ride.created_at))}</p></div><div class="price">${amount(ride.quote.fare, ride.quote.currency)}<span class="pill">${esc(ride.status.toLowerCase().replaceAll('_',' '))}</span></div><button data-history="${esc(ride.id)}" aria-label="View trip to ${esc(ride.quote.destination_label)}">View →</button></div>`).join('') : '<div class="empty-state"><span class="empty-icon">◷</span>Your journeys will appear here.</div>');
    $('#history-prev').disabled = !data.previous; $('#history-next').disabled = !data.next; $('#history-page').textContent = `Page ${state.page} · ${data.count} trips`;
    $('#history-prev').dataset.restDisabled = String(!data.previous); $('#history-next').dataset.restDisabled = String(!data.next);
    $('#history-list').querySelectorAll('[data-history]').forEach(button => button.addEventListener('click', () => busy(button, async () => {
      state.selected = await api(`rides/${button.dataset.history}/`); renderTrip(state.selected); await changeTab('ride'); $('#trip-panel').scrollIntoView({behavior:'smooth',block:'nearest'});
    })));
    $('#history-list').querySelectorAll('.cancel-scheduled-btn').forEach(button => button.addEventListener('click', () => busy(button, async () => {
      await api(`rides/schedule/${button.dataset.id}/`, {method:'DELETE'});
      toast('Scheduled ride cancelled.');
      await loadHistory();
    })));
  }
  async function loadEarnings() {
    if (config.user.role !== 'DRIVER') return;
    const data = await api('payments/earnings/');
    $('#earnings-content').innerHTML = data.totals.length ? data.totals.map(row => `<div class="eyebrow">${esc(row.currency)} · ALL TIME</div><div class="metrics"><article class="metric"><span>Completed trips</span><strong>${row.trips}</strong></article><article class="metric"><span>Gross fares</span><strong>${amount(row.gross, row.currency)}</strong></article><article class="metric"><span>Platform fees</span><strong>${amount(row.platform_fees, row.currency)}</strong></article><article class="metric"><span>Your net earnings</span><strong>${amount(row.net, row.currency)}</strong></article></div><div class="panel"><h2>Payment breakdown</h2><p class="muted">Net earnings paid: ${amount(row.paid_net, row.currency)}<br>Net earnings awaiting payment: ${amount(row.pending_net, row.currency)}<br>Cash you collected: ${amount(row.cash_collected, row.currency)}<br>Card earnings after fees: ${amount(row.card_net, row.currency)}</p><p class="form-hint">These are trip records. Bank payouts and settlement of platform fees are handled separately.</p></div>`).join('') : '<div class="panel empty-state"><span class="empty-icon">◈</span>Every journey adds up.<br>Your earnings will appear after your first completed trip.</div>';
  }
  async function loadNotifications(page = state.notificationPage) {
    const request = ++notificationsRequest;
    const data = await api(`notifications/?page=${page}`);
    if (request !== notificationsRequest) return;
    state.notificationPage = page;
    const unread = data.unread_count;
    $('#notification-count').textContent = unread; $('#notification-count').hidden = unread === 0;
    $('#mark-read').disabled = unread === 0;
    $('#mark-read').dataset.restDisabled = String(unread === 0);
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
      state.active = await api('rides/current/');
      if (state.active && (!state.selected || state.selected.id === state.active.id)) state.selected = state.active;
      else if (state.selected) state.selected = await api(`rides/${state.selected.id}/`);
      else if (previous && !state.active) state.selected = await api(`rides/${previous.id}/`);
      renderTrip(state.selected || state.active);
      if (config.user.role === 'DRIVER') { setDriverProfile(await api('drivers/profile/', {optional:true})); await loadOffers(); }
      await loadNotifications();
      if (state.tab === 'history') await loadHistory();
      if (state.tab === 'earnings') await loadEarnings();
  }
  function connect() {
    if (state.closing) return;
    const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/events/`);
    state.socket = socket;
    socket.onmessage = event => {
      const data = JSON.parse(event.data);
      if (data.event === 'connected') { $('#connection-status').textContent = 'Live updates connected'; return; }
      if (data.event !== 'pong') { clearTimeout(refreshTimer); refreshTimer = setTimeout(() => sync().catch(error => toast(error.message,true)), 200); }
    };
    socket.onclose = () => { $('#connection-status').textContent = 'Refreshing every 8 seconds'; if (!state.closing) setTimeout(connect, 6000); };
    socket.onerror = () => socket.close();
  }

  document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => changeTab(button.dataset.tab).catch(error => toast(error.message,true))));
  document.querySelectorAll('[data-pick]').forEach(button => button.addEventListener('click', () => { state.pick = button.dataset.pick; $('#map-instruction').textContent = `Click the map to set your ${state.pick}`; map?.invalidateSize(); }));
  function invalidateQuote() {
    state.quoteVersion++;
    state.quote = null; state.requestId = null; state.appliedPromo = null; $('#quote-result').hidden = true;
  }
  $('#quote-form')?.addEventListener('input', event => { invalidateQuote(); if (event.target.name.endsWith('_label')) event.target.dataset.generated = 'false'; });
  $('#use-pickup-location')?.addEventListener('click', event => busy(event.currentTarget, async () => { const coords = await gps(); choosePoint('pickup', coords.latitude, coords.longitude); map?.setView([coords.latitude,coords.longitude],14); }));
  
  const bookingTypeSelect = $('#booking-type-select');
  const scheduleRow = $('#schedule-picker-row');
  const scheduleInput = $('#schedule-datetime');
  if (bookingTypeSelect && scheduleRow) {
    bookingTypeSelect.addEventListener('change', () => {
      const isSchedule = bookingTypeSelect.value === 'SCHEDULE';
      scheduleRow.hidden = !isSchedule;
      if (isSchedule && scheduleInput && !scheduleInput.value) {
        const minDate = new Date(Date.now() + 25 * 60000);
        minDate.setMinutes(minDate.getMinutes() - minDate.getTimezoneOffset());
        scheduleInput.min = minDate.toISOString().slice(0, 16);
        scheduleInput.value = minDate.toISOString().slice(0, 16);
      }
    });
  }

  onForm('#quote-form', async data => {
    invalidateQuote();
    const version = state.quoteVersion;
    const payload = {...data};
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
        <div class="promo-box" style="margin:12px 0;padding:10px;background:var(--bg-subtle,#f4f6f8);border-radius:6px;">
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
        const schedVal = $('#schedule-datetime')?.value;
        if (!schedVal) throw new Error('Please select a scheduled pickup date and time.');
        const schedDate = new Date(schedVal);
        if (isNaN(schedDate.getTime())) throw new Error('Invalid scheduled pickup date/time.');
        await api('rides/schedule/', {
          method: 'POST',
          data: {
            quote_id: state.quote.id,
            payment_method: paymentMethod,
            scheduled_for: schedDate.toISOString()
          }
        });
        state.quote = null;
        $('#quote-result').hidden = true;
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
    await api('rides/saved-places/', {method:'POST', data});
    form.reset();
    toast('Saved place added.');
    await loadSavedPlaces();
  });
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
  $('#history-filter').addEventListener('change', () => { state.page=1; loadHistory().catch(error => toast(error.message,true)); });
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

      let timer;
      async function search(q = '') {
        try {
          const places = await api(`rides/places/search/?q=${encodeURIComponent(q)}`);
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
        } catch (_) {
          dropdown.hidden = true;
        }
      }

      input.addEventListener('focus', () => search(input.value.trim()));
      input.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(() => search(input.value.trim()), 200);
      });
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
