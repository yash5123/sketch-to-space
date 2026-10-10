/**
 * Sketch-to-Space - Landing Page Interactive Scripts
 * Handles mobile navigation toggle, smooth anchor navigation,
 * and accessible FAQ accordion state.
 */
document.addEventListener("DOMContentLoaded", () => {
  // Mobile Navigation Toggle
  const mobileMenuBtn = document.getElementById("mobileMenuBtn");
  const landingNav = document.getElementById("landingNav");

  if (mobileMenuBtn && landingNav) {
    mobileMenuBtn.addEventListener("click", () => {
      const isExpanded = mobileMenuBtn.getAttribute("aria-expanded") === "true";
      mobileMenuBtn.setAttribute("aria-expanded", String(!isExpanded));
      landingNav.classList.toggle("mobile-open");
    });

    // Close menu when clicking a link
    landingNav.querySelectorAll("a").forEach(link => {
      link.addEventListener("click", () => {
        if (landingNav.classList.contains("mobile-open")) {
          landingNav.classList.remove("mobile-open");
          mobileMenuBtn.setAttribute("aria-expanded", "false");
        }
      });
    });
  }

  // Accessible FAQ Accordion
  const faqItems = document.querySelectorAll(".faq-item");
  faqItems.forEach(item => {
    const trigger = item.querySelector(".faq-trigger");
    const content = item.querySelector(".faq-content");

    if (trigger && content) {
      trigger.addEventListener("click", () => {
        const isOpen = item.classList.contains("open");

        // Close other items in the same column for clean reading
        const parentCol = item.closest(".faq-col");
        if (parentCol) {
          parentCol.querySelectorAll(".faq-item.open").forEach(other => {
            if (other !== item) {
              other.classList.remove("open");
              const otherTrigger = other.querySelector(".faq-trigger");
              if (otherTrigger) otherTrigger.setAttribute("aria-expanded", "false");
            }
          });
        }

        item.classList.toggle("open", !isOpen);
        trigger.setAttribute("aria-expanded", String(!isOpen));
      });
    }
  });

  // Header Elevation on Scroll
  const landingHeader = document.querySelector(".landing-header");
  if (landingHeader) {
    window.addEventListener("scroll", () => {
      if (window.scrollY > 20) {
        landingHeader.classList.add("header-scrolled");
      } else {
        landingHeader.classList.remove("header-scrolled");
      }
    }, { passive: true });
  }
});
