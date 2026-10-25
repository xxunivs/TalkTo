// TalkTo 랜딩: 천천히 나타나기, 머리글 선, 요금·사업자 정보는 서버 값으로(못 읽으면 적어 둔 값 그대로).
(function () {
  "use strict";
  window.__ttReveal = true; // 머리의 안전장치: 이 스크립트가 돌았다

  var items = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window) {
    var seen = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-in");
            seen.unobserve(entry.target);
          }
        });
      },
      { rootMargin: "0px 0px -40px 0px", threshold: 0.05 }
    );
    items.forEach(function (el) { seen.observe(el); });
  } else {
    items.forEach(function (el) { el.classList.add("is-in"); });
  }

  var top = document.querySelector(".top");
  if (top) {
    var onScroll = function () { top.classList.toggle("is-scrolled", window.scrollY > 8); };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  var year = document.querySelector("[data-year]");
  if (year) year.textContent = String(new Date().getFullYear());

  function num(v) { var n = Number(v); return isFinite(n) ? n : null; }
  function won(n) { return n.toLocaleString("ko-KR") + "원"; }

  function getJSON(url) {
    return fetch(url, { headers: { Accept: "application/json" } }).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      return r.json();
    }).then(function (body) { return body && body.data; });
  }

  // 요금제(서버 /api/v1/billing/plans). 값이 바뀌어도 이 쪽을 고치지 않아도 되게.
  getJSON("/api/v1/billing/plans").then(function (plans) {
    (plans || []).forEach(function (p) {
      var card = document.querySelector('[data-plan="' + p.id + '"]');
      if (!card) return;
      var price = num(p.price);
      var charge = p.chargePrice != null ? num(p.chargePrice) : price;
      if (charge === null || price === null) return;
      var discounted = charge !== price;
      var el = card.querySelector("[data-price]");
      if (el) el.textContent = won(charge);
      var was = card.querySelector("[data-was]");
      if (was) {
        was.hidden = !discounted;
        was.textContent = discounted ? "정식 요금 월 " + won(price) : "";
      }
      var beta = card.querySelector("[data-beta]");
      if (beta) {
        beta.hidden = !discounted;
        if (discounted) beta.textContent = "첫 100가족 · 12개월 할인가";
      }
      var count = card.querySelector("[data-count]");
      var n = num(p.conversations);
      if (count && n) count.textContent = "한 달 대화 " + n.toLocaleString("ko-KR") + "번";
    });
  }).catch(function () { /* 적어 둔 값 그대로 */ });

  // 사업자 정보(서버 /api/v1/legal). 전자상거래법 제10조 제1항에 따라 첫 화면에 보이는 값이라 앱과 같게.
  // 주소·전화는 서버 값으로만 채운다(저장소에 적어 두지 않는다).
  getJSON("/api/v1/legal").then(function (legal) {
    var b = legal && legal.business;
    var el = document.querySelector("[data-business]");
    if (!b || !el || !b.name) return;
    var lines = [
      ["상호 " + b.name, b.owner && "대표 " + b.owner, b.regNo && "사업자등록번호 " + b.regNo,
        "통신판매업 신고번호 " + (b.mailOrderNo || "제2026-서울용산-1112호")],
      [b.address && "주소 " + b.address],
      [b.phone && "전화 " + b.phone, b.email && "이메일 " + b.email,
        "호스팅 제공 " + (b.hosting || "Amazon Web Services(AWS)")]
    ];
    el.textContent = "";
    lines.forEach(function (line) {
      var text = line.filter(Boolean).join(" · ");
      if (!text) return;
      if (el.childNodes.length) el.appendChild(document.createElement("br"));
      el.appendChild(document.createTextNode(text));
    });
  }).catch(function () { /* 적어 둔 값 그대로 */ });
})();
