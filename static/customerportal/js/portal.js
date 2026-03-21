
document.addEventListener("DOMContentLoaded", function () {
  document.body.classList.add("js-enabled");

  const toggleBtn = document.getElementById("menuToggle");
  const mobileMenu = document.getElementById("mobileMenu");

  if (toggleBtn && mobileMenu) {
    toggleBtn.addEventListener("click", function () {
      mobileMenu.classList.toggle("show");
    });
  }

  const revealItems = document.querySelectorAll(".reveal");

  revealItems.forEach((item, index) => {
    if (!item.classList.contains("from-left") &&
        !item.classList.contains("from-right") &&
        !item.classList.contains("from-bottom") &&
        !item.classList.contains("zoom-in")) {
      const mode = index % 4;
      if (mode === 0) item.classList.add("from-bottom");
      if (mode === 1) item.classList.add("from-left");
      if (mode === 2) item.classList.add("from-right");
      if (mode === 3) item.classList.add("zoom-in");
    }

    item.style.transitionDelay = `${Math.min(index * 70, 280)}ms`;
  });

  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    }, {
      threshold: 0.14
    });

    revealItems.forEach((item) => observer.observe(item));
  } else {
    revealItems.forEach((item) => item.classList.add("is-visible"));
  }

  setTimeout(() => {
    revealItems.forEach((item) => item.classList.add("is-visible"));
  }, 450);
});

/* rider scroll animation */
(function () {
  const rider = document.getElementById("riderMove");
  if (!rider) return;

  function animateRider() {
    const scrollY = window.scrollY;
    const speed = 0.8;

    let move = scrollY * speed;
    if (move > 1200) move = 1200;

    rider.style.transform = `translateX(${move}px)`;
  }

  window.addEventListener("scroll", animateRider);
  window.addEventListener("load", animateRider);
})();

/* feature boxes move with scroll */
(function () {
  const featureSection = document.getElementById("featureSection");
  const featureBoxes = document.querySelectorAll(".feature-scroll-box");
  if (!featureSection || !featureBoxes.length) return;

  function animateFeatureBoxes() {
    const rect = featureSection.getBoundingClientRect();
    const windowH = window.innerHeight;

    const sectionStart = windowH;
    const sectionEnd = -rect.height;
    const totalDistance = sectionStart - sectionEnd;
    const current = sectionStart - rect.top;

    let progress = current / totalDistance;
    progress = Math.max(0, Math.min(1, progress));

    featureBoxes.forEach((box, index) => {
      const startOffset = 900 + (index * 200);
      const speed = 1.6;
      const moveX = (1 - progress * speed) * startOffset;
      const opacity = 0.3 + (progress * 0.9);

      box.style.transform = `translateX(${moveX}px)`;
      box.style.opacity = opacity;
    });
  }

  window.addEventListener("scroll", animateFeatureBoxes);
  window.addEventListener("load", animateFeatureBoxes);
})();

/* services content move from bottom */
(function () {
  const serviceItems = document.querySelectorAll(".service-scroll-item");
  if (!serviceItems.length) return;

  function animateServiceInner() {
    const windowH = window.innerHeight;

    serviceItems.forEach((item, index) => {
      const rect = item.getBoundingClientRect();

      const startPoint = windowH * 1.1;
      const endPoint = windowH * 0.55;

      let progress = (startPoint - rect.top) / (startPoint - endPoint);
      progress = progress - (index * 0.06);
      progress = Math.max(0, Math.min(1, progress));

      const startOffset = 220 + (index * 30);
      const moveY = (1 - progress) * startOffset;
      const opacity = 0.25 + (progress * 0.75);

      item.style.transform = `translateY(${moveY}px)`;
      item.style.opacity = opacity;
    });
  }

  window.addEventListener("scroll", animateServiceInner);
  window.addEventListener("load", animateServiceInner);
  window.addEventListener("resize", animateServiceInner);
})();

/* metric cards fly from right continuously */
(function () {
  const cards = document.querySelectorAll(".metric-fly");
  if (!cards.length) return;

  function animateMetricFly() {
    const windowH = window.innerHeight;

    cards.forEach((card, index) => {
      const rect = card.getBoundingClientRect();

      const startPoint = windowH * 1.05;
      const endPoint = windowH * 0.45;

      let progress = (startPoint - rect.top) / (startPoint - endPoint);
      progress = progress - (index * 0.08);
      progress = Math.max(0, Math.min(1, progress));

      const startOffset = 420 + (index * 120);
      const moveX = (1 - progress) * startOffset;
      const opacity = 0.2 + (progress * 0.8);

      card.style.transform = `translateX(${moveX}px)`;
      card.style.opacity = opacity;
    });
  }

  window.addEventListener("scroll", animateMetricFly);
  window.addEventListener("load", animateMetricFly);
  window.addEventListener("resize", animateMetricFly);
})();

/* typing when contact section appears */
/* CONTACT SECTION: type on enter, reset on leave, fly cards after typing */
(function () {
  const section = document.getElementById("contactSection");
  const title = document.getElementById("contactTypingTitle");
  const cards = document.querySelectorAll(".contact-fly");

  if (!section || !title) return;

  const fullText = title.dataset.text || "";
  let typingTimer = null;
  let hasEntered = false;
  let isTyping = false;

  function resetContactSection() {
    if (typingTimer) {
      clearInterval(typingTimer);
      typingTimer = null;
    }

    isTyping = false;
    hasEntered = false;
    title.classList.remove("typing");
    title.innerHTML = "";

    cards.forEach(card => {
      card.classList.remove("show");
    });
  }

  function showCardsAfterTyping() {
    cards.forEach(card => card.classList.add("show"));
  }

  function startTyping() {
    if (isTyping || hasEntered) return;

    hasEntered = true;
    isTyping = true;
    title.classList.add("typing");
    title.innerHTML = "";

    let i = 0;

    typingTimer = setInterval(() => {
      if (i >= fullText.length) {
        clearInterval(typingTimer);
        typingTimer = null;
        isTyping = false;
        title.classList.remove("typing");
        showCardsAfterTyping();
        return;
      }

      const span = document.createElement("span");
      span.className = "contact-letter";
      span.style.animationDelay = `${i * 0.01}s`;
      span.textContent = fullText[i] === " " ? "\u00A0" : fullText[i];
      title.appendChild(span);

      i++;
    }, 38);
  }

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        startTyping();
      } else {
        resetContactSection();
      }
    });
  }, {
    threshold: 0.45
  });

  observer.observe(section);
})();
/* FINAL CTA BOX fly in from left */
(function () {
  const section = document.getElementById("finalCtaSection");
  const card = document.querySelector(".cta-fly");

  if (!section || !card) return;

  function handleFinalCta() {
    const rect = section.getBoundingClientRect();
    const windowH = window.innerHeight;

    if (rect.top < windowH * 0.82) {
      card.classList.add("show");
    } else {
      card.classList.remove("show");
    }
  }

  window.addEventListener("scroll", handleFinalCta);
  window.addEventListener("load", handleFinalCta);
  window.addEventListener("resize", handleFinalCta);
})();