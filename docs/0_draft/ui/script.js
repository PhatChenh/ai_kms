/* ========================================
   AI-KMS Demo — Scroll Animations
   ======================================== */

(function () {

  'use strict';

  // --- Hero fade-in on load ---
  const heroContent = document.querySelector('.hero-content');
  if (heroContent) {
    // Short delay so the page paint settles
    requestAnimationFrame(() => {
      heroContent.classList.add('visible');
    });
  }

  // --- Intersection Observer for scroll-triggered animations ---
  const animateElements = document.querySelectorAll('.fade-in, .fade-in-up');

  if (animateElements.length > 0 && 'IntersectionObserver' in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('visible');
            // Stop observing once visible (animate once)
            observer.unobserve(entry.target);
          }
        });
      },
      {
        threshold: 0.15,
        rootMargin: '0px 0px -40px 0px',
      }
    );

    animateElements.forEach((el) => observer.observe(el));
  } else {
    // Fallback: show everything immediately
    animateElements.forEach((el) => el.classList.add('visible'));
  }

  // --- Nav shrink on scroll ---
  const nav = document.getElementById('nav');
  let lastScrollY = 0;

  if (nav) {
    window.addEventListener('scroll', () => {
      const scrollY = window.scrollY;
      if (scrollY > 80 && scrollY > lastScrollY) {
        nav.classList.add('nav-scrolled');
      } else if (scrollY <= 80) {
        nav.classList.remove('nav-scrolled');
      }
      lastScrollY = scrollY;
    }, { passive: true });
  }

  // --- Smooth scroll for nav links (fallback for older browsers) ---
  document.querySelectorAll('a[href^="#"]').forEach((anchor) => {
    anchor.addEventListener('click', (e) => {
      const targetId = anchor.getAttribute('href');
      if (!targetId || targetId === '#') return;
      const target = document.querySelector(targetId);
      if (target) {
        e.preventDefault();
        const navHeight = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--nav-height')) || 64;
        const targetPosition = target.getBoundingClientRect().top + window.scrollY - navHeight - 8;
        window.scrollTo({
          top: targetPosition,
          behavior: 'smooth',
        });
      }
    });
  });

})();
