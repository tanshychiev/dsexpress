document.addEventListener("DOMContentLoaded", function () {
  const currentLocationBtn = document.getElementById("currentLocationBtn");
  const openMapBtn = document.getElementById("openMapBtn");
  const locationInput = document.getElementById("id_pickup_location");
  const latInput = document.getElementById("id_latitude");
  const lngInput = document.getElementById("id_longitude");

  if (currentLocationBtn) {
    currentLocationBtn.addEventListener("click", function () {
      if (!navigator.geolocation) {
        alert("Your device does not support location sharing.");
        return;
      }

      currentLocationBtn.disabled = true;
      currentLocationBtn.textContent = "Getting location...";

      navigator.geolocation.getCurrentPosition(
        function (position) {
          const lat = position.coords.latitude;
          const lng = position.coords.longitude;

          if (latInput) latInput.value = lat;
          if (lngInput) lngInput.value = lng;
          if (locationInput) {
            locationInput.value = `https://maps.google.com/?q=${lat},${lng}`;
          }

          currentLocationBtn.textContent = "Location added ✓";
          currentLocationBtn.disabled = false;
        },
        function () {
          alert("Please allow location access.");
          currentLocationBtn.textContent = "Use Current Location";
          currentLocationBtn.disabled = false;
        },
        {
          enableHighAccuracy: true,
          timeout: 10000,
          maximumAge: 0
        }
      );
    });
  }

  if (openMapBtn) {
    openMapBtn.addEventListener("click", function () {
      window.open("https://maps.google.com", "_blank");
    });
  }
});