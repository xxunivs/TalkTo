// 약관 페이지(terms·refund·privacy.html): 서버 원문(/api/v1/legal/<key>)을 받아 앱과 같은 규칙으로 그린다.
// 원문은 talkto_api_server/app/static/legal/*.md 하나뿐이라 앱·랜딩이 늘 같은 글을 보여 준다.
// 쓰는 문법은 일부만: ## 제목, ### 소제목, 빈 줄로 나눈 문단, - 목록(두 칸 들여쓰면 하위 목록), **굵게**.
(function () {
  "use strict";
  var box = document.querySelector("[data-doc]");
  if (!box) return;
  var key = box.getAttribute("data-doc");

  function inline(el, text) {
    text.split("**").forEach(function (part, i) {
      if (!part) return;
      if (i % 2) {
        var b = document.createElement("strong");
        b.textContent = part;
        el.appendChild(b);
      } else {
        el.appendChild(document.createTextNode(part));
      }
    });
    return el;
  }

  function render(body) {
    var out = document.createDocumentFragment();
    var para = [];
    var lists = []; // 깊이마다 열려 있는 <ul>

    function flush() {
      if (para.length) out.appendChild(inline(document.createElement("p"), para.join(" ")));
      para = [];
    }

    body.split("\n").forEach(function (raw) {
      var line = raw.replace(/\s+$/, "");
      var text = line.replace(/^\s+/, "");
      if (!text) { flush(); lists = []; return; }
      if (line.indexOf("### ") === 0 || line.indexOf("## ") === 0) {
        flush(); lists = [];
        var h = document.createElement(line.indexOf("### ") === 0 ? "h3" : "h2");
        out.appendChild(inline(h, line.replace(/^#+ /, "")));
        return;
      }
      if (text.indexOf("- ") === 0) {
        flush();
        var depth = Math.floor((line.length - text.length) / 2);
        if (depth > lists.length) depth = lists.length; // 건너뛴 깊이는 한 단계로
        lists.length = Math.min(lists.length, depth + 1);
        if (lists.length === depth) {
          var ul = document.createElement("ul");
          var parent = depth ? lists[depth - 1].lastElementChild : null;
          (parent || out).appendChild(ul);
          lists.push(ul);
        }
        lists[depth].appendChild(inline(document.createElement("li"), text.slice(2)));
        return;
      }
      lists = [];
      para.push(text);
    });
    flush();
    return out;
  }

  function failed() {
    box.textContent = "";
    var p = document.createElement("p");
    p.className = "legal__note";
    p.appendChild(document.createTextNode("문서를 불러오지 못했어요. 잠시 뒤 다시 열어 주시거나 "));
    var a = document.createElement("a");
    a.href = "https://app.talkto.co.kr/#/legal/" + key;
    a.textContent = "앱에서 보기";
    p.appendChild(a);
    p.appendChild(document.createTextNode("를 눌러 주세요."));
    box.appendChild(p);
    box.setAttribute("aria-busy", "false");
  }

  fetch("/api/v1/legal/" + encodeURIComponent(key), { headers: { Accept: "application/json" } })
    .then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      return r.json();
    })
    .then(function (res) {
      var doc = res && res.data;
      if (!doc || !doc.body) throw new Error("empty");
      box.textContent = "";
      box.appendChild(render(doc.body.replace(/\{\{호칭\}\}/g, "그분")));
      box.setAttribute("aria-busy", "false");
      var version = document.querySelector("[data-doc-version]");
      if (version && doc.version) version.textContent = "버전 " + doc.version;
    })
    .catch(failed);
})();
