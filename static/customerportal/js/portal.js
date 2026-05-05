document.addEventListener("DOMContentLoaded", function () {
  document.body.classList.add("js-enabled");

  const MOBILE_WIDTH = 768;
  const isMobile = window.innerWidth <= MOBILE_WIDTH;

  const toggleBtn = document.getElementById("menuToggle");
  const mobileMenu = document.getElementById("mobileMenu");

  if (toggleBtn && mobileMenu) {
    toggleBtn.addEventListener("click", function () {
      mobileMenu.classList.toggle("show");
    });

    const mobileLinks = mobileMenu.querySelectorAll("a");
    mobileLinks.forEach(function (link) {
      link.addEventListener("click", function () {
        mobileMenu.classList.remove("show");
      });
    });
  }

  const revealItems = document.querySelectorAll(".reveal");
  const featureBoxes = document.querySelectorAll(".feature-scroll-box");
  const serviceItems = document.querySelectorAll(".service-scroll-item");
  const metricCards = document.querySelectorAll(".metric-fly");
  const contactCards = document.querySelectorAll(".contact-fly");
  const ctaCards = document.querySelectorAll(".cta-fly");
  const rider = document.getElementById("riderMove");

  function showStaticOnMobile() {
    revealItems.forEach(function (item) {
      item.classList.add("is-visible");
      item.style.opacity = "1";
      item.style.transform = "none";
      item.style.transitionDelay = "0ms";
    });

    featureBoxes.forEach(function (box) {
      box.style.opacity = "1";
      box.style.transform = "none";
    });

    serviceItems.forEach(function (item) {
      item.style.opacity = "1";
      item.style.transform = "none";
    });

    metricCards.forEach(function (card) {
      card.style.opacity = "1";
      card.style.transform = "none";
    });

    contactCards.forEach(function (card) {
      card.classList.add("show");
      card.style.opacity = "1";
      card.style.transform = "none";
    });

    ctaCards.forEach(function (card) {
      card.classList.add("show");
      card.style.opacity = "1";
      card.style.transform = "none";
    });

    if (rider) {
      rider.style.opacity = "1";
      rider.style.transform = "none";
    }

    const contactTitle = document.getElementById("contactTypingTitle");
    if (contactTitle) {
      contactTitle.classList.remove("typing");
      contactTitle.textContent = contactTitle.dataset.text || contactTitle.textContent || "Talk to the DS Express team";
    }
  }

  if (isMobile) {
    showStaticOnMobile();
    return;
  }

  /* =========================
     DESKTOP REVEAL ANIMATION
     ========================= */

  revealItems.forEach(function (item, index) {
    if (
      !item.classList.contains("from-left") &&
      !item.classList.contains("from-right") &&
      !item.classList.contains("from-bottom") &&
      !item.classList.contains("zoom-in")
    ) {
      const mode = index % 4;

      if (mode === 0) item.classList.add("from-bottom");
      if (mode === 1) item.classList.add("from-left");
      if (mode === 2) item.classList.add("from-right");
      if (mode === 3) item.classList.add("zoom-in");
    }

    item.style.transitionDelay = `${Math.min(index * 70, 280)}ms`;
  });

  if ("IntersectionObserver" in window) {
    const revealObserver = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            revealObserver.unobserve(entry.target);
          }
        });
      },
      {
        threshold: 0.14
      }
    );

    revealItems.forEach(function (item) {
      revealObserver.observe(item);
    });
  } else {
    revealItems.forEach(function (item) {
      item.classList.add("is-visible");
    });
  }

  setTimeout(function () {
    revealItems.forEach(function (item) {
      item.classList.add("is-visible");
    });
  }, 450);

  /* =========================
     PERFORMANCE SCROLL RAF
     ========================= */

  let ticking = false;

  function onScrollOrResize() {
    if (!ticking) {
      window.requestAnimationFrame(function () {
        animateRider();
        animateFeatureBoxes();
        animateServiceInner();
        animateMetricFly();
        handleFinalCta();
        ticking = false;
      });

      ticking = true;
    }
  }

  /* =========================
     RIDER / MOTOR ANIMATION
     ========================= */

  function animateRider() {
    if (!rider) return;

    const scrollY = window.scrollY || window.pageYOffset || 0;
    const moveX = Math.min(scrollY * 0.12, 120);
    const moveY = Math.sin(scrollY / 90) * 6;
    const rotate = Math.sin(scrollY / 160) * 2;

    rider.style.transform =
      "translateX(" + moveX + "px) translateY(" + moveY + "px) rotate(" + rotate + "deg)";
  }

  /* =========================
     FEATURE BOXES MOVE WITH SCROLL
     ========================= */

  const featureSection = document.getElementById("featureSection");

  function animateFeatureBoxes() {
    if (!featureSection || !featureBoxes.length) return;

    const rect = featureSection.getBoundingClientRect();
    const windowH = window.innerHeight;

    const sectionStart = windowH;
    const sectionEnd = -rect.height;
    const totalDistance = sectionStart - sectionEnd;
    const current = sectionStart - rect.top;

    let progress = current / totalDistance;
    progress = Math.max(0, Math.min(1, progress));

    featureBoxes.forEach(function (box, index) {
      const startOffset = 420 + index * 90;
      const speed = 1.25;
      const moveX = (1 - progress * speed) * startOffset;
      const opacity = 0.35 + progress * 0.65;

      box.style.transform = "translateX(" + Math.max(0, moveX) + "px)";
      box.style.opacity = opacity;
    });
  }

  /* =========================
     SERVICE ITEMS MOVE FROM BOTTOM
     ========================= */

  function animateServiceInner() {
    if (!serviceItems.length) return;

    const windowH = window.innerHeight;

    serviceItems.forEach(function (item, index) {
      const rect = item.getBoundingClientRect();

      const startPoint = windowH * 1.1;
      const endPoint = windowH * 0.55;

      let progress = (startPoint - rect.top) / (startPoint - endPoint);
      progress = progress - index * 0.06;
      progress = Math.max(0, Math.min(1, progress));

      const startOffset = 180 + index * 25;
      const moveY = (1 - progress) * startOffset;
      const opacity = 0.25 + progress * 0.75;

      item.style.transform = "translateY(" + moveY + "px)";
      item.style.opacity = opacity;
    });
  }

  /* =========================
     METRIC CARDS FLY FROM RIGHT
     ========================= */

  function animateMetricFly() {
    if (!metricCards.length) return;

    const windowH = window.innerHeight;

    metricCards.forEach(function (card, index) {
      const rect = card.getBoundingClientRect();

      const startPoint = windowH * 1.05;
      const endPoint = windowH * 0.45;

      let progress = (startPoint - rect.top) / (startPoint - endPoint);
      progress = progress - index * 0.08;
      progress = Math.max(0, Math.min(1, progress));

      const startOffset = 320 + index * 80;
      const moveX = (1 - progress) * startOffset;
      const opacity = 0.2 + progress * 0.8;

      card.style.transform = "translateX(" + moveX + "px)";
      card.style.opacity = opacity;
    });
  }

  /* =========================
     CONTACT TYPING
     ========================= */

  const contactSection = document.getElementById("contactSection");
  const contactTitle = document.getElementById("contactTypingTitle");

  if (contactSection && contactTitle) {
    const fullText = contactTitle.dataset.text || "";
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
      contactTitle.classList.remove("typing");
      contactTitle.innerHTML = "";

      contactCards.forEach(function (card) {
        card.classList.remove("show");
      });
    }

    function showCardsAfterTyping() {
      contactCards.forEach(function (card) {
        card.classList.add("show");
      });
    }

    function startTyping() {
      if (isTyping || hasEntered) return;

      hasEntered = true;
      isTyping = true;
      contactTitle.classList.add("typing");
      contactTitle.innerHTML = "";

      let i = 0;

      typingTimer = setInterval(function () {
        if (i >= fullText.length) {
          clearInterval(typingTimer);
          typingTimer = null;
          isTyping = false;
          contactTitle.classList.remove("typing");
          showCardsAfterTyping();
          return;
        }

        const span = document.createElement("span");
        span.className = "contact-letter";
        span.style.animationDelay = i * 0.01 + "s";
        span.textContent = fullText[i] === " " ? "\u00A0" : fullText[i];
        contactTitle.appendChild(span);

        i++;
      }, 38);
    }

    if ("IntersectionObserver" in window) {
      const contactObserver = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              startTyping();
            } else {
              resetContactSection();
            }
          });
        },
        {
          threshold: 0.45
        }
      );

      contactObserver.observe(contactSection);
    } else {
      startTyping();
    }
  }

  /* =========================
     FINAL CTA FLY FROM LEFT
     ========================= */

  const finalCtaSection = document.getElementById("finalCtaSection");
  const finalCtaCard = document.querySelector(".cta-fly");

  function handleFinalCta() {
    if (!finalCtaSection || !finalCtaCard) return;

    const rect = finalCtaSection.getBoundingClientRect();
    const windowH = window.innerHeight;

    if (rect.top < windowH * 0.82) {
      finalCtaCard.classList.add("show");
    } else {
      finalCtaCard.classList.remove("show");
    }
  }

  window.addEventListener("scroll", onScrollOrResize, { passive: true });
  window.addEventListener("resize", onScrollOrResize);
  window.addEventListener("load", onScrollOrResize);

  onScrollOrResize();
});