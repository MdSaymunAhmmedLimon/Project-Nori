(() => {
  const card = document.querySelector('#shared-trip');
  if (!card) return;
  const token = card.dataset.shareToken;
  const status = document.querySelector('#share-status');
  const message = document.querySelector('#share-message');
  const details = document.querySelector('#share-details');
  const driver = document.querySelector('#share-driver');
  const location = document.querySelector('#share-location');
  const labels = {
    REQUESTED: 'Ride requested', SEARCHING: 'Finding a driver', ACCEPTED: 'Driver assigned',
    DRIVER_ARRIVING: 'Driver on the way', DRIVER_ARRIVED: 'Driver has arrived',
    IN_PROGRESS: 'Trip in progress', COMPLETED: 'Trip complete', CANCELLED: 'Trip cancelled',
  };
  let timer;
  async function refresh() {
    try {
      const response = await fetch(`/api/rides/shared/${encodeURIComponent(token)}/`, {
        headers: {Accept: 'application/json'}, credentials: 'omit', cache: 'no-store',
      });
      if (!response.ok) throw new Error(response.status === 404 ? 'This trip link has expired or sharing was stopped.' : 'Trip details are temporarily unavailable.');
      const trip = await response.json();
      status.textContent = labels[trip.status] || 'Trip update';
      document.querySelector('#share-pickup').textContent = trip.pickup;
      document.querySelector('#share-destination').textContent = trip.destination;
      details.hidden = false;
      message.textContent = trip.location ? 'The driver’s location is updating while the ride is active.' : 'Location updates will appear when the driver is assigned and shares a recent location.';
      driver.hidden = !trip.driver;
      driver.replaceChildren();
      if (trip.driver) {
        const name = document.createElement('strong');
        name.textContent = trip.driver.name;
        driver.append(name);
        if (trip.driver.vehicle) {
          const vehicle = document.createElement('p');
          const v = trip.driver.vehicle;
          vehicle.textContent = `${v.color} ${v.make} ${v.model} · ${v.license_plate}`;
          driver.append(vehicle);
        }
      }
      location.hidden = !trip.location;
      location.replaceChildren();
      if (trip.location) {
        const link = document.createElement('a');
        link.href = `https://www.openstreetmap.org/?mlat=${encodeURIComponent(trip.location.latitude)}&mlon=${encodeURIComponent(trip.location.longitude)}#map=16/${encodeURIComponent(trip.location.latitude)}/${encodeURIComponent(trip.location.longitude)}`;
        link.rel = 'noopener noreferrer';
        link.target = '_blank';
        link.textContent = 'Open latest driver location on map';
        location.append(link, document.createTextNode(` · Updated ${new Date(trip.location.updated_at).toLocaleTimeString()}`));
      }
      if (['COMPLETED', 'CANCELLED'].includes(trip.status)) {
        message.textContent = `This ride has ended. The link expires ${new Date(trip.expires_at).toLocaleString()}.`;
        clearInterval(timer);
      }
    } catch (error) {
      status.textContent = 'Trip unavailable';
      message.textContent = error.message;
      details.hidden = true;
      clearInterval(timer);
    }
  }
  refresh();
  timer = setInterval(refresh, 10000);
})();
